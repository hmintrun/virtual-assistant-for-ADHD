"""Client tương tác với Google Calendar API v3 và OAuth 2.0 Token Endpoint.

Hỗ trợ Dual-Mode:
- Real (GoogleCalendarClient): Gọi Google REST API qua httpx.AsyncClient.
- Fake (FakeCalendarClient): Mock offline cho development, CI/CD và Unit Tests.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


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
    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Tạo mới một sự kiện trên lịch."""
        pass

    @abstractmethod
    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
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

            # Lấy email thông tin tài khoản qua tokeninfo hoặc userinfo
            account_ref = "unknown@google.com"
            try:
                info_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {token_data.get('access_token')}"},
                )
                if info_resp.status_code == 200:
                    account_ref = info_resp.json().get("email", account_ref)
            except Exception as e:
                logger.debug("Failed to fetch userinfo email: %s", e)

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

    async def list_calendar_list(self, access_token: str) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{GOOGLE_CALENDAR_BASE_URL}/users/me/calendarList"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired or invalid")
            if resp.status_code != 200:
                raise CalendarClientError(f"Google calendarList error: {resp.status_code}", status_code=resp.status_code)
            return resp.json().get("items", [])

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events"
        params = {
            "timeMin": time_min.astimezone(timezone.utc).isoformat(),
            "timeMax": time_max.astimezone(timezone.utc).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            if resp.status_code != 200:
                raise CalendarClientError(f"Google list events error: {resp.status_code}", status_code=resp.status_code)
            return resp.json().get("items", [])

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
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=event_payload)
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            if resp.status_code not in (200, 201):
                raise CalendarClientError(f"Google create event error: {resp.status_code}", status_code=resp.status_code)
            return resp.json()

    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> bool:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events/{event_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code in (200, 204, 404, 410):
                return True
            if resp.status_code == 401:
                raise CalendarAuthError("Access token expired")
            raise CalendarClientError(f"Google delete event error: {resp.status_code}", status_code=resp.status_code)


class FakeCalendarClient(CalendarClientInterface):
    """Mock client hoàn toàn offline phục vụ testing và dev không cần mạng."""

    def __init__(self):
        self.mock_events: dict[str, list[dict[str, Any]]] = {}

    def build_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        return f"http://localhost:8000/api/v1/calendars/oauth2callback?code=mock_auth_code_123&state={state}"

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

    async def list_calendar_list(self, access_token: str) -> list[dict[str, Any]]:
        return [
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
            {
                "id": "proplan_focus@group.calendar.google.com",
                "summary": "ProPlan Focus Sessions",
                "primary": False,
                "accessRole": "owner",
                "backgroundColor": "#5484ed",
            },
        ]

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        # Trả về các mock events đã lưu hoặc một số sample events
        if calendar_id in self.mock_events:
            return self.mock_events[calendar_id]
        return [
            {
                "id": "mock_evt_1",
                "summary": "Daily Standup Meeting",
                "start": {"dateTime": time_min.isoformat()},
                "end": {"dateTime": (time_min).isoformat()},
                "transparency": "opaque",
                "status": "confirmed",
            }
        ]

    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        event_id = f"mock_evt_{len(self.mock_events.get(calendar_id, [])) + 1}"
        created = {
            "id": event_id,
            "summary": event_payload.get("summary", "Focus Session"),
            "start": event_payload.get("start"),
            "end": event_payload.get("end"),
            "etag": '"mock_etag_123"',
            "status": "confirmed",
        }
        self.mock_events.setdefault(calendar_id, []).append(created)
        return created

    async def delete_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
    ) -> bool:
        if calendar_id in self.mock_events:
            self.mock_events[calendar_id] = [
                e for e in self.mock_events[calendar_id] if e.get("id") != event_id
            ]
        return True


def get_calendar_client() -> CalendarClientInterface:
    """Factory cấp Calendar Client tương ứng với cấu hình hệ thống."""
    if settings.is_calendar_real_mode:
        return GoogleCalendarClient(
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            default_redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
        )
    return FakeCalendarClient()
