# Google Sign-In / app JWT — implementation contract (11/11/2026)

## Delivery scope

PR17 delivers **Backend Auth and its documentation** on baseline `76c5632`. The committed Flutter login at this baseline is a demo placeholder; Mobile Google Sign-In integration is a separate delivery. The Mobile requirements below describe the target client behavior, not functionality accepted in this PR. See [verification evidence and reproduction](PR17_AUTH_VERIFICATION.md) for the PR review fixes and current QA scope.

## Runtime setup

Backend uses `AUTH_MODE=google` for pilot/staging/production. `dev_user` is accepted only in `development`/`testing`; it bootstraps fixture A only when Authorization is absent. A supplied malformed token never falls back to A. Startup validates configuration before Calendar workers. Module import does not bootstrap DB or call Google.

Use a separate random `JWT_SECRET_KEY` of at least 32 bytes. Keep the existing `SECRET_KEY` unchanged: Calendar credential encryption can depend on it. Supply `GOOGLE_SIGN_IN_AUDIENCES` as a JSON list of actual OAuth client IDs. Set `JWT_ISSUER=proplan-va`, `JWT_AUDIENCE=proplan-va-api`, `ACCESS_TOKEN_TTL_SECONDS=3600`, `AUTH_CLOCK_SKEW_SECONDS=0` (allowed 0–60), `GOOGLE_AUTH_TIMEOUT_SECONDS=5` (maximum 30). No credentials belong in source, handoffs, logs or chat.

`User.google_sub`, unique email, and one `UserPreference` per user already exist. No migration was added. Deploy through existing Alembic migrations on the selected deployment DB; this task never migrates the app DB. Google-mode services never run dev `create_all` or seed. Tests use fresh SQLite databases and fake providers; SQLite concurrency is not PostgreSQL acceptance.

## HTTP contract

OpenAPI declares `AppBearer` as `type: http`, `scheme: bearer`, `bearerFormat: JWT` for operations using `CurrentUserDep`. In Swagger, select **Authorize** and paste the app `access_token` without the `Bearer ` prefix. Google exchange and Calendar browser callbacks remain public with respect to app JWT; callbacks still validate stored state. The legacy callback stays intentionally hidden from OpenAPI. The documented Bearer requirement describes Google mode; guarded `dev_user` fallback remains available only when Authorization is genuinely absent.

`POST /api/v1/auth/google` is public, JSON `{"id_token":"<google_id_token>"}`; body rejects extras, blank/padded tokens, nonstrings and values longer than 16384 characters. Neither Bearer nor Idempotency-Key is required. Google RS256 signature/iat/exp/audience are checked by google-auth against Google certificates; issuer must be accounts.google.com or https://accounts.google.com. Certificate fetching has bounded timeout and Cache-Control/age based in-process caching.

200 for both first and returning login: `{"data":{"access_token":"<app_jwt>","token_type":"bearer","expires_in":3600,"user":{"user_id":"<UUID>","email":"user@example.com","display_name":"User","is_active":true}},"meta":{"request_id":"req-auth","server_time":"2026-11-11T10:00:00+07:00"}}`.

`GET /api/v1/auth/me` requires `Authorization: Bearer <app_jwt>` in Google mode, sends no body and returns `{"data":{"user_id":"<UUID>","email":"user@example.com","display_name":"User","is_active":true},"meta":{"request_id":"req-auth","server_time":"2026-11-11T10:00:00+07:00"}}`. Business routes retain CurrentUser/ownership checks. The app verifier accepts HS256 only with required UUID sub, integer exp/iat, exact issuer/audience and token_type=access; it reloads User/active from DB. Google and Calendar tokens are rejected at business APIs.

Every Auth error is `{"error":{"code":"...","message":"...","field_errors":[],"retryable":false,"current_revisions":{},"next_action":"..."},"meta":{"request_id":"...","server_time":"<offset datetime>"}}`. HTTP401 includes `WWW-Authenticate: Bearer`.

| HTTP | code | next_action | retryable |
|---|---|---|---|
| 422 | VALIDATION_ERROR | fix_fields | false |
| 401 | INVALID_GOOGLE_ID_TOKEN | authenticate | false |
| 401 | UNAUTHENTICATED | authenticate | false |
| 403 | ACCOUNT_DISABLED | contact_support | false |
| 409 | ACCOUNT_LINK_CONFLICT | review_account_link | false |
| 422 | GOOGLE_PROFILE_INCOMPLETE | review_google_profile | false |
| 503 | AUTH_PROVIDER_UNAVAILABLE | retry_login | true |

Validation errors include `field_errors[{field_path,message,code}]`; arbitrary upstream/token exception text is not returned. Config unavailable in dev exchange also returns503 before DB writes. A valid token for a deleted user returns401; disabled returns403.

Identity is Google sub, never email. Verified email is required (nonempty, one @, no whitespace, <=320); no automatic linking to another Google subject/seed email. Display name uses trimmed Google name or email local part, capped at100. Existing preferences survive repeat login/email changes. New users receive current default preferences, no fake Draft/availability. Auth owns a fresh session transaction, resolves unique-sub races once, rolls back failed writes, and issues JWT only after commit.

## Session semantics and Mobile

Access TTL is3600 seconds by default. No app refresh token, server logout/revocation/session store, password or Firebase endpoint is introduced. Obtain a fresh Google ID token and exchange again when required. The Mobile logout requirement is to delete the app binding and SDK sign-in and isolate caches; an issued JWT remains valid before exp and Calendar consent stays connected. This PR adds no server logout endpoint.

### Mobile integration requirements — pending separate delivery

The planned client uses google_sign_in7.2.0, a Web native GIS button and authentication events. Configure `--dart-define=GOOGLE_CLIENT_ID=<web_client_id>` on Web and `--dart-define=GOOGLE_SERVER_CLIENT_ID=<web/server_client_id>` on Android. Android OAuth registration needs package name plus signing SHA fingerprints; Web needs exact authorized origins and fixed local port. Client IDs are public configuration; client secrets stay on Backend. When integrating Mobile, make development seed flow explicit with `--dart-define=AUTH_MODE=dev_user` and disable guest API bypass by default. These are client acceptance requirements; the committed demo in this PR does not implement them.

The integrated client must use an AuthGate that restores `/auth/me` before restoring owner Draft/Focus. Bound offline cache remains available after expiry/network error, with reauthentication on the account screen; Focus/capture/outbox are retained. Clients and persistent Draft/Run/Thought/command slots are tied to an immutable owner and reject responses from old owners. Legacy unbound caches are quarantined. 401 uses single-flight lightweight SDK recovery, retries only same-owner requests once with identical body/key, and retains unknown command intent on401. Web may need explicit GIS interaction. Logout/account switch is blocked while Focus/capture is active; reauth of a different account requires explicit logout first.

Calendar browser callbacks `/calendar/oauth/callback` and legacy `/calendar/oauth2callback` use stored state rather than app Bearer. Connection/job/event APIs retain app authentication and ownership. App login grants no Calendar permissions.

## Verification and handoff

[PR17 verification evidence](PR17_AUTH_VERIFICATION.md) contains reproducible commands, baseline/results and acceptance limits in the repository; no `_temp` file is required to review this PR.

On 11/11/2026, independent QA reported 79 passing cases on PostgreSQL 17.11, including isolated-schema validation and concurrent login scopes, and successful live Calendar OAuth/list/create/update/delete on the dedicated ProActive QA secondary calendar. The test event was removed. This follow-up confirmed the PostgreSQL connection/migration `20261108_01` using read-only SQL and successfully read the live Calendar list. The Calendar QA server uses `dev_user`: this does not accept real Google Sign-In for the app. Full PostgreSQL regression, real app login on Android/Web with two accounts, Calendar refresh/revoke/412 cases, device/Doze and measurement remain separate gates. Do not label all 598 catalog cases PASS from these scoped results.

References: https://developers.google.com/identity/sign-in/web/backend-auth ; https://google-auth.readthedocs.io/en/latest/reference/google.auth.jwt.html ; https://pub.dev/packages/google_sign_in ; https://pub.dev/packages/google_sign_in_web
