# ProActive — Tự cài PostgreSQL và Google Calendar API để test

Ngày soạn: 11/11/2026. Đối chiếu backend tại commit `b028890ac4a13f09e933c69a6def216470c2a5eb` và source local hiện hành. Dành cho thành viên nhóm dùng Windows + PowerShell. Thời gian dự kiến lần đầu: 45–90 phút.

## 1. Kết quả cần đạt và phạm vi

Sau hướng dẫn, mỗi người có PostgreSQL riêng trên máy, backend dùng PostgreSQL đó, một kết nối OAuth với tài khoản Google của mình, và tự kiểm chứng được hai chiều:

- Google → backend: tự tạo/sửa một sự kiện trên web Google Calendar rồi gọi API đọc thấy đúng nội dung, giờ.
- Backend → Google: tạo preview → confirm → chạy sync job → thấy sự kiện thật; sau đó sửa và xóa đúng sự kiện thử.
- Khởi động lại backend vẫn đọc được kết nối vì database và khóa mã hóa được giữ lại.

Đây là kiểm thử tích hợp backend với Google thật. Chưa thay thế nghiệm thu đăng nhập Google trên Mobile, luồng UF2 tạo Focus Session, cơ chế xử lý xung đột, hoặc toàn bộ 598 testcase. Hướng dẫn không triển khai cloud/database dùng chung.

Luồng dữ liệu: PowerShell/Swagger → FastAPI → PostgreSQL lưu connection/preview/job → worker → Google Calendar. Google cấp token cho backend; Google không truy cập trực tiếp PostgreSQL.

**Mỗi người dùng database và lịch QA riêng.** `AUTH_MODE=dev_user` chỉ dành cho máy local cô lập; các request không có Bearer sẽ dùng tài khoản dev mặc định. Không áp dụng cấu hình này cho staging hoặc database nhóm. Calendar OAuth là cấp quyền đọc/ghi lịch, khác với Google Sign-In để đăng nhập app.

## 2. Chuẩn bị phần mềm và mã nguồn

| Thành phần | Yêu cầu |
|---|---|
| Git | Có quyền đọc repository, dùng nhánh/commit được nhóm thống nhất |
| Python | 3.12; `pyproject.toml` hiện yêu cầu `>=3.12,<3.13` |
| uv | Quản lý dependency theo `uv.lock` |
| Docker Desktop | Đang chạy Linux containers; Windows bật virtualization/WSL 2 theo tài liệu Docker |
| Google | Tài khoản có Calendar, quyền tạo Google Cloud project hoặc được quản trị viên cho phép |
| Cổng local | Backend 8000; PostgreSQL 55432 hoặc cổng trống khác |

Tải/cài theo [Docker Desktop Windows](https://docs.docker.com/desktop/setup/install/windows-install/), [uv](https://docs.astral.sh/uv/getting-started/installation/) và [Git](https://git-scm.com/downloads/win). Mở PowerShell mới sau cài, mở Docker Desktop và chờ engine sẵn sàng.

```powershell
git --version
uv --version
docker version
uv python install 3.12
```

`docker version` cần hiện được cả Client và Server. Không cần cài thêm PostgreSQL bằng installer khi chọn cách Docker dưới đây.

Nếu chưa có code, lấy URL repository từ Git Manager và clone về thư mục riêng. Nếu đã có code, kiểm tra trước khi cập nhật; không reset/xóa thay đổi đang làm:

```powershell
# Đổi theo nơi bạn đặt repository.
$repo = 'D:\HCMUS\CT\ProActive'
Set-Location $repo
git branch --show-current
git rev-parse HEAD
git status --short
Set-Location (Join-Path $repo 'backend')
uv sync --frozen --python 3.12
uv run --frozen python --version
```

Nếu thiếu các endpoint Calendar trong phần dưới, kiểm tra đúng nhánh/commit với nhóm. Không tự thêm dependency hay đổi `uv.lock` để chữa lỗi dùng nhầm Python.

## 3. Tạo cấu hình Google Cloud

### 3.1. Bật Google Calendar API

1. Mở [Google Cloud Console](https://console.cloud.google.com/), chọn/tạo project riêng, ví dụ `proactive-calendar-qa-tenban`.
2. Kiểm tra project đang chọn trên thanh trên cùng.
3. Vào **APIs & Services → Library**, tìm **Google Calendar API**, bấm **Enable**.
4. Không cần Gemini API key, Service Account hoặc API key thông thường cho bài test OAuth Calendar này.

Tham chiếu [Calendar API setup](https://developers.google.com/workspace/calendar/api/quickstart/python). Chỉ tham khảo phần cấu hình project; không dùng mẫu Desktop credentials/`token.json` của quickstart để thay adapter backend đang có.

### 3.2. Cấu hình màn hình đồng ý OAuth

Trong **Google Auth platform** (một số giao diện ghi OAuth consent screen):

1. **Branding**: đặt App name, support email, developer/contact email của bạn.
2. **Audience**: tài khoản Gmail cá nhân chọn **External**. **Internal** chỉ phù hợp tổ chức Google Workspace hỗ trợ lựa chọn này.
3. Giữ **Testing** cho bài tự test. Thêm chính email sẽ đăng nhập vào **Test users**.
4. **Data Access → Add or remove scopes**: thêm các scope dưới đây và lưu.

```text
openid
https://www.googleapis.com/auth/userinfo.email
https://www.googleapis.com/auth/calendar.calendarlist.readonly
https://www.googleapis.com/auth/calendar.events.readonly
https://www.googleapis.com/auth/calendar
```

Trong cấu hình backend, scope email dùng tên rút gọn `email`. Ba scope Calendar khớp cấu hình hiện tại; scope `calendar` khá rộng, đang hỗ trợ cả thao tác tạo lịch phụ. Đây là cấu hình tương thích để test code hiện hành, chưa phải kết luận tối ưu quyền cho production. Hai scope định danh giúp backend lấy email tài khoản đã kết nối.

### 3.3. Tạo OAuth Client loại Web application

1. **Clients → Create client → Web application**; đặt tên `ProActive Backend Local`.
2. **Authorized redirect URIs** thêm chính xác:

```text
http://localhost:8000/api/v1/calendar/oauth/callback
```

3. Lưu, tải file JSON client về máy. Cất ngoài repository, không gửi qua chat hoặc commit.
4. Backend trao đổi authorization code ở server nên dùng **Web application**. Client Android/iOS/Flutter Web để đăng nhập app là cấu hình riêng.
5. Authorized JavaScript origins không thay cho redirect URI; bài test backend này không cần đặt origin để chữa OAuth callback.

`localhost` và `127.0.0.1` là hai chuỗi khác nhau khi kiểm tra redirect URI. Hướng dẫn cố định callback bằng `localhost`; API gọi từ PowerShell có thể dùng `127.0.0.1`. Nếu đổi cổng backend, phải sửa cả URI trong Console và cấu hình backend.

Tham chiếu [OAuth cho web server](https://developers.google.com/identity/protocols/oauth2/web-server).

### 3.4. Tạo lịch phụ chuyên để test

1. Mở [Google Calendar](https://calendar.google.com/) trên web bằng đúng tài khoản Test user.
2. **Settings → Add calendar → Create new calendar**.
3. Đặt tên duy nhất, ví dụ `ProActive QA - TenBan`; timezone `Asia/Ho_Chi_Minh`.
4. Lưu. Không chọn lịch `primary` hoặc lịch công việc làm lịch ghi thử.

Hướng dẫn chọn lịch phụ tạo thủ công để dễ kiểm soát. Backend cũng có nhánh tạo lịch mới qua configuration preview `provision_new`, nhưng không cần dùng nhánh đó để hoàn thành bài test này.

## 4. Tạo cấu hình local riêng và giữ khóa qua các lần chạy

Các lệnh dưới chạy trong `backend/`. File cấu hình cá nhân chứa mật khẩu và OAuth secret; đặt trong `%LOCALAPPDATA%\ProActiveQA`, ngoài repository và ngoài thư mục đồng bộ/chia sẻ. File này không được đính kèm báo cáo test. Người có quyền đọc tài khoản Windows của bạn vẫn có thể đọc file; đây không phải secret manager production.

Chạy **một lần**. Nếu file đã tồn tại, dùng lại ở mục 5; không sinh khóa mới cho database cũ:

```powershell
$privateDir = Join-Path $env:LOCALAPPDATA 'ProActiveQA'
$settingsPath = Join-Path $privateDir 'local-settings.json'
if (Test-Path -LiteralPath $settingsPath) {
    throw 'Da co cau hinh. Dung lai o muc 5, khong ghi de khoa cu.'
}
New-Item -ItemType Directory -Path $privateDir -Force | Out-Null
$clientPath = Read-Host 'Duong dan day du toi OAuth Web client JSON vua tai'
$client = Get-Content -LiteralPath $clientPath -Raw | ConvertFrom-Json
if (-not $client.web.client_id -or -not $client.web.client_secret) {
    throw 'Can JSON cua OAuth Web application, khong phai Desktop/Service Account.'
}
$fernet = (uv run --frozen python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())').Trim()
if ($LASTEXITCODE -ne 0 -or -not $fernet) { throw 'Khong sinh duoc Fernet key' }
$dbPassword = (uv run --frozen python -c 'import secrets; print(secrets.token_urlsafe(32))').Trim()
if ($LASTEXITCODE -ne 0 -or -not $dbPassword) { throw 'Khong sinh duoc mat khau DB' }
$local = [ordered]@{
    db_host_port = 55432
    db_password = $dbPassword
    calendar_credential_secret_key = $fernet
    google_oauth_client_id = $client.web.client_id
    google_oauth_client_secret = $client.web.client_secret
    google_oauth_redirect_uri = 'http://localhost:8000/api/v1/calendar/oauth/callback'
}
$local | ConvertTo-Json | Set-Content -LiteralPath $settingsPath -Encoding UTF8
Write-Host 'Da luu cau hinh rieng; khong in secret ra man hinh.'
```

Nếu cổng 55432 đã dùng, chỉnh `db_host_port` trong file này thành 55433 **trước khi tạo container**, rồi dùng đúng cổng đó cho DBeaver/pgAdmin. Không đụng container QA đang có của người khác.

`CALENDAR_CREDENTIAL_SECRET_KEY` dùng Fernet mã hóa credential trong database. Giữ khóa và database cùng nhau; mất/đổi khóa sẽ không giải mã được token cũ, cần kết nối lại. Không dùng OAuth client secret làm Fernet key và không đưa Google token vào Flutter.

## 5. Nạp cấu hình vào cửa sổ backend

Chạy lại nguyên khối này mỗi khi mở cửa sổ PowerShell mới để chạy backend hoặc migration. Biến môi trường trong cửa sổ này ưu tiên hơn `backend/.env`, không sửa đè `.env` hiện có:

```powershell
# Dang o thu muc backend.
$settingsPath = Join-Path $env:LOCALAPPDATA 'ProActiveQA\local-settings.json'
$local = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$env:POSTGRES_PASSWORD = $local.db_password
$escapedPassword = [uri]::EscapeDataString($local.db_password)
$env:DATABASE_URL = "postgresql+asyncpg://proactive_local:${escapedPassword}@127.0.0.1:$($local.db_host_port)/proactive_local"
$env:ENVIRONMENT = 'development'
$env:AUTH_MODE = 'dev_user'
$env:CALENDAR_PROVIDER_MODE = 'real'
$env:GOOGLE_OAUTH_CLIENT_ID = $local.google_oauth_client_id
$env:GOOGLE_OAUTH_CLIENT_SECRET = $local.google_oauth_client_secret
$env:GOOGLE_OAUTH_REDIRECT_URI = $local.google_oauth_redirect_uri
$env:CALENDAR_CREDENTIAL_SECRET_KEY = $local.calendar_credential_secret_key
$env:GOOGLE_CALENDAR_DEFAULT_SCOPES = '["openid","email","https://www.googleapis.com/auth/calendar.calendarlist.readonly","https://www.googleapis.com/auth/calendar.events.readonly","https://www.googleapis.com/auth/calendar"]'
$env:ENABLE_CALENDAR_SYNC_WORKER = 'false'
$env:TESTING = 'false'
```

Tạm tắt background worker để quan sát rõ preview, confirm và job. Sau này muốn kiểm tra worker tự chạy, đổi thành `true` và khởi động lại backend; không kỳ vọng job tự chạy trong bài test đang tắt worker.

Không cần cấu hình Gemini/Groq để gọi các endpoint Calendar bên dưới. Chức năng AI/giọng nói khác vẫn cần cấu hình riêng và không nằm trong tiêu chí đạt bài test này.

## 6. Cài PostgreSQL bằng Docker và chạy migration

### 6.1. Tạo container lần đầu

Dùng PostgreSQL 17, volume riêng và chỉ mở cổng trên loopback. Không dùng `postgres:latest` vì major version và layout volume có thể đổi. Cách mount dưới đây dành cho image 17. Tham chiếu [Postgres official image](https://hub.docker.com/_/postgres).

```powershell
# Can da nap muc 5, Docker Desktop dang chay.
docker run --name proactive-postgres-local `
  --publish "127.0.0.1:$($local.db_host_port):5432" `
  --env POSTGRES_USER=proactive_local `
  --env POSTGRES_DB=proactive_local `
  --env POSTGRES_PASSWORD `
  --volume proactive_pg17_local_data:/var/lib/postgresql/data `
  --detach postgres:17
if ($LASTEXITCODE -ne 0) { throw 'Tao container that bai; xem loi truoc khi tiep tuc.' }
docker exec proactive-postgres-local pg_isready -U proactive_local -d proactive_local
```

Nếu chưa `accepting connections`, đợi vài giây rồi chạy lại `pg_isready`. Nếu container đã được tạo từ trước, dùng `docker start proactive-postgres-local`, không chạy lại `docker run` và không tạo mật khẩu mới. Biến `POSTGRES_PASSWORD` chỉ khởi tạo tài khoản khi volume chưa có dữ liệu; đổi biến không tự đổi password của database cũ.

### 6.2. Kiểm tra và áp migration

Chỉ chạy khi `DATABASE_URL` đã nạp từ mục 5 và trỏ database local mới này:

```powershell
uv run --frozen alembic heads
uv run --frozen alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration that bai; dung tai day.' }
uv run --frozen alembic current
docker exec proactive-postgres-local psql -U proactive_local -d proactive_local -c 'SELECT version();'
docker exec proactive-postgres-local psql -U proactive_local -d proactive_local -c 'SELECT version_num FROM alembic_version;'
docker exec proactive-postgres-local psql -U proactive_local -d proactive_local -c '\dt'
```

Kết quả cần có bảng nghiệp vụ và Alembic current khớp head của checkout. Không lấy số revision trong ảnh cũ làm chuẩn và không dùng `alembic stamp head` để che migration lỗi.

Lệnh `psql` trong container chứng minh DB hoạt động; request API thành công sau đây mới bổ sung bằng chứng backend dùng DB được. Không chỉ mở `/docs` rồi kết luận đã nối PostgreSQL.

Có thể dùng DBeaver/pgAdmin: host `127.0.0.1`, port theo `db_host_port`, database/user `proactive_local`, password trong file cấu hình cá nhân. Không dùng nguyên URL `postgresql+asyncpg://` làm JDBC URL. Không chụp/export cột `credential_ref`.

### 6.3. Chạy backend

```powershell
uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Giữ cửa sổ này mở. Mở `http://127.0.0.1:8000/docs`; kiểm tra có nhóm Calendar. Không dùng `--host 0.0.0.0` cho cấu hình dev_user này. Nếu cổng bận, xác định backend cũ trước; không tắt process của người khác.

## 7. Kết nối OAuth với Google thật

Mở **cửa sổ PowerShell thứ hai**, dùng cửa sổ đó xuyên suốt mục 7–11 để giữ biến. Không cần nạp secret vào cửa sổ gọi API.

```powershell
$base = 'http://127.0.0.1:8000/api/v1/calendar'
function Invoke-QAApi {
    param([string]$Method, [string]$Path, $Body)
    $argsForRequest = @{
        Method = $Method
        Uri = "$base$Path"
        ErrorAction = 'Stop'
    }
    if ($null -ne $Body) {
        $argsForRequest.ContentType = 'application/json; charset=utf-8'
        $argsForRequest.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 20))
    }
    Invoke-RestMethod @argsForRequest
}
$authorization = Invoke-QAApi POST '/authorizations' @{ device_id = 'local-calendar-qa' }
# Mo URL nay trong trinh duyet tren chinh may dang chay backend.
$authorization.data.auth_url
```

Chọn đúng Gmail đã thêm trong Test users, xem quyền và đồng ý. Không chia sẻ URL OAuth vì chứa state. Hoàn tất trong thời hạn authorization (hiện 15 phút); nếu hết hạn, tạo authorization mới, không sửa state hoặc phát lại callback cũ.

Sau callback, backend có thể chuyển tới `proplan://calendar/connected?...`. Máy chưa có handler app có thể báo không mở được liên kết này. Khi đó kiểm tra API dưới đây trước: lỗi mở deep link không tự chứng minh OAuth thất bại. Không reload callback để đổi cùng authorization code lần hai.

```powershell
$connections = Invoke-QAApi GET '/connections' $null
$connections.data | Select-Object connection_id, account_ref, connection_state
$cid = Read-Host 'Copy connection_id cua dung tai khoan QA vua ket noi'
$calendarList = Invoke-QAApi GET "/connections/$cid/calendars" $null
$calendarList.data.items | Select-Object id, summary, primary, access_role
$qaCalendarId = Read-Host 'Copy id cua lich phu ProActive QA - TenBan'
$chosen = @($calendarList.data.items | Where-Object { $_.id -eq $qaCalendarId })
if ($chosen.Count -ne 1 -or $chosen[0].primary -or $chosen[0].access_role -notin @('owner','writer')) {
    throw 'Dung lai: chi chon dung lich phu QA co quyen ghi.'
}
```

Kỳ vọng connection `connected`, email đúng, danh sách chứa lịch QA. Nếu `account_ref` hiển thị email mặc định/không đúng, kiểm tra scope email và kết nối lại; không bỏ qua rồi tiếp tục ghi khi chưa xác định được tài khoản.

## 8. Chọn lịch QA làm nguồn đọc và đích ghi

```powershell
$configuration = Invoke-QAApi POST "/connections/$cid/configuration-previews" @{
    read_calendar_ids = @($qaCalendarId)
    write_calendar_id = $qaCalendarId
    existing_event_action = 'keep'
}
$configured = Invoke-QAApi POST "/configuration-previews/$($configuration.data.preview_id)/confirm" @{
    expected_preview_revision = $null
}
if ($configured.data.write_calendar_id -ne $qaCalendarId) {
    throw 'Lich ghi khong dung QA; khong tao event.'
}
$configured.data | Select-Object connection_state, read_calendar_ids, write_calendar_id
$encodedCalendar = [uri]::EscapeDataString($qaCalendarId)
```

Chọn `keep` vì đây là database mới/lịch thử riêng, không có yêu cầu chuyển sự kiện cũ. `expected_preview_revision=null` được contract hiện tại cho phép và dùng ở bài local tuần tự này; không dùng ví dụ đó làm cam kết chống tranh chấp cho production. Nếu có 409 hoặc preview hết hạn, đọc lại cấu hình, tạo preview mới và review lại.

Worker ghi vào `write_calendar_id` đã cấu hình. Chỉ truyền một calendar ID trong request create chưa đủ để bảo đảm đích ghi đúng; luôn kiểm tra cấu hình trước.

## 9. Test Google → backend: đọc thay đổi trên Google

1. Trên web Google Calendar, **chọn đúng lịch QA** và tạo event `QA FROM GOOGLE - TenBan`, ngày mai 14:00–14:15 giờ Việt Nam.
2. Đặt trạng thái Busy, không phải Free. Event hủy/transparent có thể bị loại bởi normalizer của backend.
3. Gọi API với khoảng thời gian rõ ràng. Các lệnh dùng UTC; `07:00Z` tương ứng `14:00 +07:00`.

```powershell
$from = [uri]::EscapeDataString([DateTimeOffset]::UtcNow.ToString('o'))
$to = [uri]::EscapeDataString([DateTimeOffset]::UtcNow.AddDays(7).ToString('o'))
$eventsPath = "/connections/$cid/events?google_calendar_id=$encodedCalendar&from=$from&to=$to&limit=50"
$read = Invoke-QAApi GET $eventsPath $null
$read.data | Select-Object google_event_id, title, start_at, end_at
```

Kỳ vọng có title và khoảng giờ đúng. Trên Google kéo event sang 15:00–15:15, gọi lại GET và kiểm tra giờ mới. Danh sách rỗng là hợp lệ nếu lịch/khoảng thời gian không có event; chưa phải bằng chứng đọc Google thành công. Nếu có hơn 50 event, tăng `limit` tối đa 250 hoặc thu hẹp khoảng đọc.

Đây là đọc theo yêu cầu qua API. Không cần webhook `events.watch`, tunnel hay domain HTTPS để làm bài OAuth localhost này. Chưa chứng minh Mobile tự cập nhật màn hình hoặc tiến trình nền tự nhập mọi thay đổi Google về lịch Task.

## 10. Test backend → Google: tạo, sửa, xóa sự kiện thật

### 10.1. Chuẩn bị helper xử lý job

```powershell
function Complete-QAChange {
    param([string]$PreviewId)
    $confirmed = Invoke-QAApi POST "/event-change-previews/$PreviewId/confirm" @{
        expected_preview_revision = $null
    }
    $jobId = $confirmed.data.job_id
    if (-not $jobId) { throw 'Khong nhan duoc job_id' }
    # Co tac dung ghi Google: xu ly queue cua user, toi da 20 job, khong chi connection nay.
    $null = Invoke-QAApi POST "/connections/$cid/refresh" @{}
    $job = Invoke-QAApi GET "/sync-jobs/$jobId" $null
    $job.data | Select-Object job_id, operation, job_state, last_error_code | Format-Table | Out-Host
    if ($job.data.job_state -ne 'completed') {
        throw 'Job chua completed. Giu job_id, xem loi; khong tao lai preview mot cach mu quang.'
    }
    return $job.data
}
$marker = 'ProActive QA ' + [Guid]::NewGuid().ToString('N').Substring(0,12)
$start = [DateTimeOffset]::UtcNow.AddDays(1)
$end = $start.AddMinutes(15)
$payload = @{
    summary = $marker
    description = 'Temporary local integration test; delete this exact event after testing.'
    start = @{ dateTime = $start.ToString('o') }
    end = @{ dateTime = $end.ToString('o') }
}
```

`refresh` hiện gọi worker và có thể đẩy các job khác của user. Vì vậy chỉ chạy trong database QA riêng, không dùng kết nối database pilot dùng chung. Có thể xem từng API/job trong Swagger thay vì dùng helper.

### 10.2. Create: preview chưa ghi Google, confirm mới tạo job

```powershell
$create = Invoke-QAApi POST '/event-change-previews' @{
    operation = 'create'
    connection_id = $cid
    google_calendar_id = $qaCalendarId
    payload = $payload
}
$create.data | Select-Object preview_id, operation, status
# Tai diem nay: kiem tra tren Google chua co event mang $marker.
# Chi chay dong duoi sau khi da review payload va dung lich QA.
$createdJob = Complete-QAChange $create.data.preview_id
$eventId = $createdJob.target_ref
if (-not $eventId) { throw 'Khong co event ID sau completed' }
$eventPath = "/connections/$cid/events/${eventId}?google_calendar_id=$encodedCalendar"
$createdEvent = Invoke-QAApi GET $eventPath $null
$createdEvent.data | Select-Object google_event_id, title, start_at, end_at
```

Kỳ vọng: preview `pending`; confirm trả job, thường ban đầu `queued`; sau refresh job `completed`, `target_ref` là ID event Google. Mở Calendar web, tìm marker, kiểm tra event nằm trong lịch QA và giờ địa phương đúng. HTTP 200 của confirm chưa có nghĩa event đã được Google ghi xong.

Lưu `$eventId` ngay để dọn đúng event nếu test bị gián đoạn. Nếu mất response sau confirm, kiểm tra lại cùng preview/job trước khi tạo preview create khác để tránh trùng sự kiện.

### 10.3. Update: đổi tên và tăng thời lượng 5 phút

```powershell
$payload.summary = "$marker UPDATED"
$payload.end = @{ dateTime = $end.AddMinutes(5).ToString('o') }
$update = Invoke-QAApi POST '/event-change-previews' @{
    operation = 'update'
    connection_id = $cid
    google_calendar_id = $qaCalendarId
    google_event_id = $eventId
    payload = $payload
}
$updatedJob = Complete-QAChange $update.data.preview_id
$updatedEvent = Invoke-QAApi GET $eventPath $null
$updatedEvent.data | Select-Object google_event_id, title, start_at, end_at
```

Kỳ vọng cùng event ID, tên thêm `UPDATED`, thời lượng từ 15 lên 20 phút. Adapter update hiện dùng PUT; bài thử gửi lại đầy đủ payload của event tự tạo. Không dùng payload này để sửa event cá nhân có attendees/reminders/metadata khác vì có thể làm mất trường không gửi lại. Test etag/412 cần ca riêng; chuỗi chạy tuần tự này chưa kiểm chứng chống ghi đè đồng thời.

### 10.4. Delete: chỉ xóa event có ID đã tạo trong bài

```powershell
$delete = Invoke-QAApi POST '/event-change-previews' @{
    operation = 'delete'
    connection_id = $cid
    google_calendar_id = $qaCalendarId
    google_event_id = $eventId
}
$deletedJob = Complete-QAChange $delete.data.preview_id
```

Sau `completed`, gọi lại GET sau đây riêng; dự kiến báo HTTP 404 (Google có thể trả 410 cho tài nguyên đã bị xóa). Đây là kết quả mong đợi ở bước kiểm tra xóa, không áp dụng cho lỗi create/update:

```powershell
Invoke-QAApi GET $eventPath $null
```

Gọi lại danh sách `$eventsPath` và kiểm tra event ID không còn, đồng thời xác nhận trên web. Dọn event `QA FROM GOOGLE - TenBan` tạo thủ công ở mục 9 ngay trên lịch QA. Không xóa cả lịch/database để thay cho kiểm chứng xóa một event.

## 11. Kiểm tra lưu bền và dùng lại ngày hôm sau

1. Dừng backend bằng Ctrl+C trong đúng cửa sổ của mình.
2. Giữ container và volume. Có thể `docker stop proactive-postgres-local` khi nghỉ, rồi `docker start proactive-postgres-local` khi test lại.
3. Mở PowerShell mới, vào `backend/`, nạp lại mục 5 từ **cùng file settings**, rồi chạy backend như mục 6.3.
4. Cửa sổ API chạy lại helper mục 7, lấy `/connections`, lịch và events. Không cần OAuth lại nếu token/quyền vẫn hợp lệ.
5. Nếu dùng phần mềm DB, chỉ xem metadata như connection state, write calendar ID và job state; không xuất token/cột credential.

Không chạy `docker rm -v`, xóa volume, hoặc sinh Fernet key mới khi muốn giữ dữ liệu. Đây là tài khoản PostgreSQL khởi tạo từ Docker để test local, chưa phải cấu hình phân quyền/backup/SSL cho staging.

## 12. Lỗi thường gặp và hướng xử lý

| Hiện tượng | Kiểm tra và cách xử lý |
|---|---|
| `docker` không được nhận diện | Mở terminal mới; kiểm tra Docker Desktop cài xong/PATH. Bản per-user có thể ở `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`, bản toàn máy ở `C:\Program Files\Docker\Docker\resources\bin`. |
| Không kết nối được Docker engine | Mở Docker Desktop, chờ Server; kiểm tra WSL/virtualization theo tài liệu Docker. |
| Cổng 55432/8000 đã dùng | Xác định service đang chiếm; chọn cổng DB khác trong settings trước tạo container. Nếu đổi cổng backend thì đổi cả Google redirect URI. |
| Container name already in use | Dùng lại container đúng của mình bằng `docker start`; không xóa container có dữ liệu của người khác. |
| `password authentication failed` | Kiểm tra đúng container/cổng, password file settings và volume cũ; đổi env không đổi password trong volume đã khởi tạo. |
| `No module named asyncpg` hoặc Python 3.14 | Vào đúng `backend/`, dùng Python 3.12 và `uv sync --frozen --python 3.12`; chạy lệnh qua `uv run --frozen`. |
| `relation ... does not exist` | Migration và backend có thể dùng hai URL khác nhau. Nạp mục 5 trong chính cửa sổ chạy Alembic và backend; kiểm tra current/head. |
| API `/docs` chạy nhưng Calendar vẫn fake | Cửa sổ uvicorn chưa nhận `CALENDAR_PROVIDER_MODE=real` hoặc đang gọi backend cũ ở cổng 8000. Dừng đúng process của mình, nạp env rồi chạy lại. |
| `redirect_uri_mismatch` | So chính xác scheme/host/port/path giữa Console và env; dùng OAuth client loại Web application. |
| Google `access_denied` | Đúng tài khoản Test user chưa, user có bấm từ chối không, Workspace admin có chặn app/scope không. Không vượt cảnh báo nếu không chắc là project mình vừa tạo. |
| API chưa enabled/403 insufficient permissions | Enable Calendar API đúng project; thêm đúng scopes và đồng ý lại. Chỉ sửa Data Access trong Console không tự nâng quyền token đã cấp. |
| OAuth state/code invalid hoặc expired | Tạo `/authorizations` mới, mở đúng URL mới trên máy backend; không gọi lại callback/code đã dùng. |
| Deep link `proplan://` không mở | Kiểm tra `/connections`; callback có thể đã lưu thành công trước khi redirect. Không phát lại callback. |
| Email kết nối là giá trị mặc định | Kiểm tra `openid email` trong scopes runtime, consent và kết nối lại; xác nhận đúng tài khoản trước ghi. |
| `invalid_grant` sau nhiều ngày | Token hết hạn/bị revoke, user/policy/client thay đổi. Kết nối OAuth lại; xem quy tắc Testing bên dưới. Không retry vô hạn. |
| Không giải mã được credential | Có thể mất/đổi Fernet key hoặc dùng DB của người khác; khôi phục đúng khóa của DB hoặc kết nối lại trên môi trường riêng. |
| `401` khi gọi app API | Bài này dùng `dev_user` local và không gửi Bearer. Nếu bật `google`, cần **App JWT** từ API Auth; Google access token không thay thế App JWT. Bearer sai không tự rơi về dev user. |
| `404` connection/event | Kiểm tra đúng user, connection ID, calendar ID, event ID và database. Sau delete có thể là mong đợi; trước create/update là lỗi phải dừng. |
| `409`, preview hết hạn hoặc job conflict | Đọc trạng thái mới, không confirm lặp với dữ liệu cũ hoặc bỏ guard để qua lỗi. |
| Job cứ queued/retry hoặc có last_error_code | Background worker đang tắt là có chủ đích. Gọi refresh một lần, GET job; nếu lỗi giữ job ID/mã lỗi, xử lý nguyên nhân rồi mới cân nhắc retry. |
| Event lệch 7 giờ | So sánh UTC `Z` với giờ Việt Nam `+07:00`, kiểm tra timezone Google Calendar; không cộng 7 giờ hai lần. |
| API đọc không thấy event | Kiểm tra đúng lịch, from/to, giới hạn, event bị hủy hoặc đặt Free. |

**Refresh token ở Testing:** project External đang Testing với Calendar scopes chịu quy tắc refresh token hết hạn sau 7 ngày; ngoại lệ chỉ xin scope định danh cơ bản không áp dụng cho ca Calendar này. Chấp nhận kết nối lại trong tự test. Chuyển In production là quyết định riêng của nhóm và có thể cần verification; không bảo đảm refresh token vĩnh viễn vì còn nhiều nguyên nhân thu hồi/hết hạn. Xem [Google OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

## 13. Checklist và mẫu báo cáo gửi nhóm

- [ ] Ghi branch, commit, ngày test; Python 3.12, phiên bản Docker/PostgreSQL.
- [ ] PostgreSQL sẵn sàng, Alembic current trùng head.
- [ ] Backend chạy real provider, database local riêng, dev_user chỉ trên loopback.
- [ ] Connection đúng tài khoản; lịch QA phụ đúng, có quyền ghi, đúng write_calendar_id.
- [ ] Google → API: thấy event tạo thủ công và giờ mới sau khi kéo trên web.
- [ ] Preview create chưa tạo event; confirm tạo job; refresh hoàn tất job.
- [ ] API → Google: create đúng ID/tên/giờ; update cùng ID và đổi tên/thời lượng.
- [ ] Delete đúng event; list không còn, detail không còn, web không còn.
- [ ] Khởi động lại backend dùng cùng DB/khóa vẫn đọc được kết nối.
- [ ] Đã dọn hai event QA tạo trong bài; không tác động lịch cá nhân.

```text
Nguoi test / thoi diem:
Branch / commit:
OS / Python / PostgreSQL:
Migration current / head:
Provider mode / worker mode:
Google -> API: PASS/FAIL; title/khung gio da an thong tin ca nhan:
Create / Update / Delete: PASS/FAIL:
Job ID / trang thai / ma loi (neu co):
Restart persistence: PASS/FAIL:
Da don event QA: YES/NO:
Bug: endpoint, request da che thong tin, HTTP status, response da che thong tin, expected/actual.
```

Không gửi file settings, OAuth JSON, mật khẩu DB, Authorization header, auth URL/state/code, access/refresh token, `credential_ref` hoặc ảnh có thông tin cá nhân. Người nhận báo cáo không cần secret để đối chiếu kết quả.

## 14. Nguồn đối chiếu

Source repository: `backend/app/core/config.py`, `backend/app/core/database.py`, `backend/app/main.py`, `backend/app/api/v1/calendar.py`, `backend/app/schemas/calendar.py`, `backend/app/services/calendar_service.py`, `backend/app/services/google_calendar_client.py`, `backend/app/services/calendar_sync_worker.py`, `backend/migrations/`, `backend/pyproject.toml` và `backend/uv.lock`.

Các câu lệnh trong tài liệu là hướng dẫn cho thành viên tự chạy. Lượt soạn tài liệu này không cài phần mềm, tạo container, chạy migration, OAuth hay tạo/xóa sự kiện Google trên máy người dùng. Đã đối chiếu source và kiểm tra cấu trúc tài liệu; nhóm cần ghi kết quả thực chạy theo checklist, không lấy việc có file hướng dẫn làm bằng chứng PASS.
