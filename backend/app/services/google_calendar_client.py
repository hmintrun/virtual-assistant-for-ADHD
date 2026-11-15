"""Client tương tác với Google Calendar API v3 và OAuth 2.0 Token Endpoint.

Hỗ trợ Dual-Mode:
- Real (GoogleCalendarClient): Gọi Google REST API qua httpx.AsyncClient.
- Fake (FakeCalendarClient): Mock offline cho development, CI/CD và Unit Tests.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


def _calendar_events_url(calendar_id: str, event_id: str | None = None) -> str:
    """Build Google Calendar events endpoint URL with URL-escaped path segments."""
    encoded_cal = quote(calendar_id, safe="")
    base = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{encoded_cal}/events"
    if event_id is None:
        return base
    return f"{base}/{quote(event_id, safe='')}"


class CalendarClientError(Exception):
    """Lỗi chung khi gọi Calendar Provider."""

    def __init__(self, message: str, status_code: int = 500, error_code: str = "calendar_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code


class CalendarAuthError(CalendarClientError):
    """Lỗi xác thực OAuth 2.0 (hết hạn token hoặc không được cấp quyền)."""

    def __init__(self, message: str = "Google OAuth authentication failed"):
        super().__init__(message=message, status_code=401, error_code="auth_error")


class CalendarConflictError(CalendarClientError):
    """Lỗi xung đột phiên bản sự kiện Google Calendar (HTTP 412 Precondition Failed / Etag Mismatch)."""

    def __init__(self, message: str = "Google Calendar event was modified externally (etag conflict)"):
        super().__init__(message=message, status_code=412, error_code="conflict")


class CalendarClientInterface(ABC):
    """Giao diện chuẩn hóa cho Calendar Client."""

    @abstractmethod
    def build_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        """Sinh URL dẫn tới Google OAuth Consent Screen."""
        pass

    @abstractmethod
    async def exchange_auth_code(
        self, code: str, redirect_uri: str | None = None
    ) -> dict[str, Any]:
        """Đổi authorization code lấy access_token, refresh_token và account info."""
        pass

    @abstractmethod
    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Dùng refresh_token để cấp mới access_token."""
        pass

    @abstractmethod
    async def provision_proactive_calendar(
        self, access_token: str, summary: str = "ProPlan VA Focus", time_zone: str = "Asia/Ho_Chi_Minh"
    ) -> dict[str, Any]:
        """Tạo một secondary calendar riêng trên Google Calendar (POST /calendars)."""
        pass

    @abstractmethod
    async def list_calendar_list(self, access_token: str) -> list[dict[str, Any]]:
        """Lấy danh sách các Calendar của người dùng (users/me/calendarList)."""
        pass

    @abstractmethod
    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        """Lấy danh sách sự kiện trong khoảng thời gian [time_min, time_max]."""
        pass

    @abstractmethod
    async def get_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        """Lấy thông tin chi tiết một sự kiện trên lịch."""
        pass

    @abstractmethod
    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Tạo mới một sự kiện trên lịch."""
        pass

    @abstractmethod
    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        event_payload: dict[str, Any],
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        """Cập nhật sự kiện có kiểm tra If-Match Etag để phát hiện xung đột."""
        pass

    @abstractmethod
    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        expected_etag: str | None = None,
    ) -> bool:
        """Xóa sự kiện trên lịch."""
        pass


class GoogleCalendarClient(CalendarClientInterface):
    """Client thực tế giao tiếp với Google Calendar API v3 qua HTTP."""

    def __init__(self, client_id: str, client_secret: str, default_redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.default_redirect_uri = default_redirect_uri

    def build_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri or self.default_redirect_uri,
            "response_type": "code",
            "scope": " ".join(settings.GOOGLE_CALENDAR_DEFAULT_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_auth_code(
        self, code: str, redirect_uri: str | None = None
    ) -> dict[str, Any]:
        data = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri or self.default_redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=data)
            if resp.status_code != 200:
                logger.warning("Failed to exchange Google auth code: status=%d", resp.status_code)
                raise CalendarAuthError(f"OAuth code exchange failed with status {resp.status_code}")
            token_data = resp.json()

            account_ref = "unknown@google.com"
            auth_headers = {"Authorization": f"Bearer {token_data.get('access_token')}"}
            try:
                info_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers=auth_headers,
                )
                if info_resp.status_code == 200:
                    email = info_resp.json().get("email")
                    if isinstance(email, str) and email.strip():
                        account_ref = email.strip()
            except Exception as e:
                logger.debug("Failed to fetch userinfo email: %s", e)

            if account_ref in ("unknown@google.com", "unknown@gmail.com"):
                try:
                    cal_resp = await client.get(
                        f"{GOOGLE_CALENDAR_BASE_URL}/users/me/calendarList/primary",
                        headers=auth_headers,
                    )
                    if cal_resp.status_code == 200:
                        cal_id = cal_resp.json().get("id")
                        if (
                            isinstance(cal_id, str)
                            and cal_id.strip()
                            and cal_id.strip() != "primary"
                        ):
                            account_ref = cal_id.strip()
                except Exception as e:
                    logger.debug("Failed to fetch primary calendar id: %s", e)

            token_data["account_ref"] = account_ref
            return token_data

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        data = {
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=data)
            if resp.status_code != 200:
                logger.warning("Failed to refresh Google access token: status=%d", resp.status_code)
                raise CalendarAuthError("Refresh access token failed")
            return resp.json()

    async def provision_proactive_calendar(
        self, access_token: str, summary: str = "ProPlan VA Focus", time_zone: str = "Asia/Ho_Chi_Minh"
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars"
        payload = {"summary": summary, "timeZone": time_zone}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            if resp.status_code not in (200, 201):
                raise CalendarClientError(
                    f"Google create calendar error: {resp.status_code}", status_code=resp.status_code
                )
            return resp.json()

    async def list_calendar_list(self, access_token: str) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{GOOGLE_CALENDAR_BASE_URL}/users/me/calendarList"
        all_items: list[dict[str, Any]] = []
        page_token: str | None = None
        async with httpx.AsyncClient(timeout=20.0) as client:
            while True:
                params: dict[str, Any] = {"maxResults": 250}
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 401:
                    raise CalendarAuthError("Access token expired or invalid")
                if resp.status_code != 200:
                    raise CalendarClientError(f"Google calendarList error: {resp.status_code}", status_code=resp.status_code)
                body = resp.json()
                all_items.extend(body.get("items", []))
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
        return all_items

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = _calendar_events_url(calendar_id)
        all_events: list[dict[str, Any]] = []
        page_token: str | None = None
        async with httpx.AsyncClient(timeout=25.0) as client:
            while True:
                params: dict[str, Any] = {
                    "timeMin": time_min.astimezone(UTC).isoformat(),
                    "timeMax": time_max.astimezone(UTC).isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 250,
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 401:
                    raise CalendarAuthError("Access token expired")
                if resp.status_code != 200:
                    raise CalendarClientError(f"Google list events error: {resp.status_code}", status_code=resp.status_code)
                body = resp.json()
                all_events.extend(body.get("items", []))
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
        return all_events

    async def get_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = _calendar_events_url(calendar_id, event_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                return None
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            if resp.status_code != 200:
                raise CalendarClientError(f"Google get event error: {resp.status_code}", status_code=resp.status_code)
            return resp.json()

    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        url = _calendar_events_url(calendar_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=event_payload)
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            if resp.status_code == 409:
                raise CalendarConflictError("Event ID already exists on Google Calendar")
            if resp.status_code not in (200, 201):
                raise CalendarClientError(f"Google create event error: {resp.status_code}", status_code=resp.status_code)
            return resp.json()

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        event_payload: dict[str, Any],
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        if expected_etag:
            headers["If-Match"] = expected_etag

        url = _calendar_events_url(calendar_id, event_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(url, headers=headers, json=event_payload)
            if resp.status_code == 412:
                raise CalendarConflictError("Google Calendar event was modified externally (etag mismatch)")
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            if resp.status_code not in (200, 201):
                raise CalendarClientError(f"Google update event error: {resp.status_code}", status_code=resp.status_code)
            return resp.json()

    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        expected_etag: str | None = None,
    ) -> bool:
        headers = {"Authorization": f"Bearer {access_token}"}
        if expected_etag:
            headers["If-Match"] = expected_etag

        url = _calendar_events_url(calendar_id, event_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code == 412:
                raise CalendarConflictError("Google Calendar event was modified externally before delete")
            if resp.status_code in (200, 204, 404, 410):
                return True
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            raise CalendarClientError(f"Google delete event error: {resp.status_code}", status_code=resp.status_code)


class FakeCalendarClient(CalendarClientInterface):
    """Mock client hoàn toàn offline phục vụ testing và dev không cần mạng."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """Đặt lại trạng thái mock cho các bài kiểm thử."""
        self.mock_events: dict[str, list[dict[str, Any]]] = {}
        self.mock_calendars: list[dict[str, Any]] = [
            {
                "id": "mock_user@gmail.com",
                "summary": "Lịch Cá Nhân (Primary)",
                "primary": True,
                "accessRole": "owner",
                "backgroundColor": "#9a9cff",
            },
            {
                "id": "work_calendar_123@group.calendar.google.com",
                "summary": "Công việc & Họp",
                "primary": False,
                "accessRole": "writer",
                "backgroundColor": "#ff887c",
            },
        ]

    def build_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        base = redirect_uri or "http://localhost:8000/api/v1/calendar/oauth/callback"
        delimiter = "&" if "?" in base else "?"
        return f"{base}{delimiter}code=mock_auth_code_123&state={state}"

    async def exchange_auth_code(
        self, code: str, redirect_uri: str | None = None
    ) -> dict[str, Any]:
        if code == "invalid_code":
            raise CalendarAuthError("Mock invalid code")
        return {
            "access_token": "mock_access_token_xyz",
            "refresh_token": "mock_refresh_token_abc",
            "expires_in": 3600,
            "scope": " ".join(settings.GOOGLE_CALENDAR_DEFAULT_SCOPES),
            "token_type": "Bearer",
            "account_ref": "mock_user@gmail.com",
        }

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        return {
            "access_token": "mock_refreshed_access_token_xyz",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    async def provision_proactive_calendar(
        self, access_token: str, summary: str = "ProPlan VA Focus", time_zone: str = "Asia/Ho_Chi_Minh"
    ) -> dict[str, Any]:
        cal_id = "proplan_focus@group.calendar.google.com"
        created = {
            "id": cal_id,
            "summary": summary,
            "primary": False,
            "accessRole": "owner",
            "backgroundColor": "#5484ed",
            "timeZone": time_zone,
        }
        if not any(c.get("id") == cal_id for c in self.mock_calendars):
            self.mock_calendars.append(created)
        return created

    async def list_calendar_list(self, access_token: str) -> list[dict[str, Any]]:
        return list(self.mock_calendars)

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        if calendar_id in self.mock_events:
            return self.mock_events[calendar_id]
        return [
            {
                "id": "mock_evt_1",
                "summary": "Daily Standup Meeting",
                "start": {"dateTime": time_min.isoformat()},
                "end": {"dateTime": (time_min + timedelta(hours=1)).isoformat()},
                "transparency": "opaque",
                "status": "confirmed",
                "etag": '"mock_etag_1"',
            }
        ]

    async def get_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        events = self.mock_events.get(calendar_id, [])
        for e in events:
            if e.get("id") == event_id:
                return e
        if event_id == "mock_evt_1":
            return {
                "id": "mock_evt_1",
                "summary": "Daily Standup Meeting",
                "etag": '"mock_etag_1"',
                "status": "confirmed",
            }
        return None

    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        event_id = event_payload.get("id") or f"mock_evt_{len(self.mock_events.get(calendar_id, [])) + 1}"
        created = {
            "id": event_id,
            "summary": event_payload.get("summary", "Focus Session"),
            "start": event_payload.get("start"),
            "end": event_payload.get("end"),
            "etag": f'"mock_etag_{len(self.mock_events.get(calendar_id, [])) + 1}"',
            "status": "confirmed",
            "extendedProperties": event_payload.get("extendedProperties"),
        }
        self.mock_events.setdefault(calendar_id, []).append(created)
        return created

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        event_payload: dict[str, Any],
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        events = self.mock_events.setdefault(calendar_id, [])
        for idx, e in enumerate(events):
            if e.get("id") == event_id:
                if expected_etag and e.get("etag") != expected_etag:
                    raise CalendarConflictError("Mock etag conflict")
                e["summary"] = event_payload.get("summary", e.get("summary"))
                e["start"] = event_payload.get("start", e.get("start"))
                e["end"] = event_payload.get("end", e.get("end"))
                e["etag"] = f'"mock_etag_updated_{idx}"'
                return e

        if expected_etag:
            raise CalendarConflictError("Mock etag conflict: event not found or etag mismatch")

        created = {
            "id": event_id,
            "summary": event_payload.get("summary", "Focus Session"),
            "start": event_payload.get("start"),
            "end": event_payload.get("end"),
            "etag": '"mock_etag_new"',
            "status": "confirmed",
        }
        events.append(created)
        return created

    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        expected_etag: str | None = None,
    ) -> bool:
        if calendar_id in self.mock_events:
            if expected_etag:
                for e in self.mock_events[calendar_id]:
                    if e.get("id") == event_id and e.get("etag") != expected_etag:
                        raise CalendarConflictError("Mock etag conflict on delete")
            self.mock_events[calendar_id] = [
                e for e in self.mock_events[calendar_id] if e.get("id") != event_id
            ]
        return True


_fake_client_instance: FakeCalendarClient | None = None


def reset_fake_calendar_client() -> None:
    """Đặt lại client giả lập phục vụ cô lập môi trường test."""
    global _fake_client_instance
    if _fake_client_instance is not None:
        _fake_client_instance.reset()


def get_calendar_client() -> CalendarClientInterface:
    """Factory cấp Calendar Client tương ứng với cấu hình hệ thống."""
    if settings.is_calendar_real_mode:
        return GoogleCalendarClient(
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            default_redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
        )
    global _fake_client_instance
    if _fake_client_instance is None:
        _fake_client_instance = FakeCalendarClient()
    return _fake_client_instance
