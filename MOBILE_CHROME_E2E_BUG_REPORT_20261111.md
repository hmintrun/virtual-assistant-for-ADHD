# Báo cáo Kiểm thử Thao tác Live & Tổng hợp Bug Mobile (Flutter App)

- **Ngày kiểm thử:** `2026-11-11` (`00:00 – 01:35 UTC+7`)
- **Người nhận:** Mobile Developer Lead (**Thanh An**) & Team Mobile
- **Phạm vi mã nguồn đã đối chiếu:** `D:\HCMUS\CT\ProActive\mobile` *(nền `origin/mobile` @ `c013085` + WIP đang stage trên checkout chính)* kết nối live với **Backend FastAPI** (`http://127.0.0.1:8000/api/v1`, `backend_task_audit_docs` @ `18cac3d`, DB `alembic head 20261108_01`).
- **Môi trường test thao tác thực tế:** Flutter Web (`flutter run -d web-server --web-port 5050`) trên **Chrome DevTools** (`412x915` viewport mobile, kiểm tra cả **Dark Mode** & **Light Mode**, cả nhánh Fallback lẫn **AI thật** sau khi cập nhật key Backend).

---

## 1. Tóm tắt Hiện trạng & Ranh giới Đánh giá

| Hạng mục | Kết quả quan sát thực tế trên Chrome |
| :--- | :--- |
| **Kết nối Backend API** | Toàn bộ request đi qua HTTP `200`/`201` (`POST /drafts`, `POST /analyze`, `PATCH /drafts`, `confirm-summary`, `POST /candidates`, `POST /confirm`, `GET /schedule`, `GET /capabilities`), **0 lỗi HTTP 4xx/5xx**, **0 uncaught Dart crash** ở runtime UI. |
| **Luồng chính đi qua được** | `Onboarding` $\rightarrow$ `Home` $\rightarrow$ `UF1 (Text fallback)` $\rightarrow$ `TaskSummary` $\rightarrow$ `CandidateReview (AI thật & Feasibility loop)` $\rightarrow$ `Confirm Plan` $\rightarrow$ `FocusScreen (Micro-task + Active timer + Hộp suy nghĩ + Dừng sớm)` $\rightarrow$ `Calendar` $\rightarrow$ `Hỏi lịch` $\rightarrow$ `Trợ giúp` $\rightarrow$ `Tài khoản`. |
| **Phân tách Bug vs Sprint 3** | Báo cáo dưới đây tách rõ **lỗi cần sửa ngay trong các màn UF1–UF4 hiện có (`P0`/`P1`/`P2`)** với **các phần chưa nối Backend thuộc Sprint 3 (`UF5`, `UF6`, `UF7`, Auth thật)** để team Mobile không bị hiểu nhầm phạm vi. |

---

## 2. Danh sách Tổng hợp Bug Mobile theo Mức độ Ưu tiên

| Mã | Mức | Tiêu đề ngắn | Màn hình / File liên quan |
| :---: | :---: | :--- | :--- |
| `BUG-M01` | **P0** | Sai múi giờ `-7 tiếng` (hiển thị UTC+0) ở Home & Focus; "Còn lại hôm nay" lọc sai cả ngày mai | `home_screen.dart`, `focus_screen.dart` |
| `BUG-M02` | **P0** | Bẫy Voice-only ở UF1 & treo UI `"Đang nghe…"` khi bấm `"Dừng nghe"` (phải lỗi 3 lần mới cho gõ chữ) | `task_entry_screen.dart`, `voice_service.dart` |
| `BUG-M03` | **P0** | Hiện sai `"Thiếu tối thiểu 0 phút"` / `"Backend báo thiếu 0 phút"` khi khả thi hoặc `search_inconclusive` | `candidate_review_screen.dart`, `uf2_screens.dart` |
| `BUG-M04` | **P0** | Lỗi tương phản trắng-trên-xám ở Light Mode trên màn `"Bản nháp tạm lưu"` (`Hạn` & icon gần như tàng hình) | `uf2_screens.dart` |
| `BUG-M05` | **P1** | Focus không gọi `POST /sessions/{id}/start`, thiếu đường sang Check-in UF5, CTA sai & trạng thái không sync Home | `focus_provider.dart`, `focus_screen.dart` |
| `BUG-M06` | **P1** | Trang chủ chỉ đọc 1 `confirmedTask` trong RAM (bỏ qua DB `/schedule`), lặp thẻ giống hệt nhau bằng tên Task cụt | `home_screen.dart` |
| `BUG-M07` | **P1** | `"Hỏi về lịch"` (`ScheduleQueryScreen`) là mock tĩnh hoàn toàn dù DB có dữ liệu | `support_screens.dart` |
| `BUG-M08` | **P1** | `FocusScreen` lặp 2 đồng hồ + 2 nút `"Hộp suy nghĩ"`, đẩy nút `"Cất suy nghĩ"` bị cắt nửa vòng dưới màn `412x915` | `focus_screen.dart` |
| `BUG-M09` | **P2** | Lặp 3 nút Thêm việc & 3 lối Lịch ở Home; tràn mép `-10px` chấm mặt trăng; nút Avatar thiếu semantic/cỡ `< 48dp` | `home_screen.dart`, `melbel_ui.dart` |
| `BUG-M10` | **P2** | `TaskSummaryScreen` lẫn picker dial/input, tiếng Anh `OK/Cancel`, hardcode `-7h`, nuốt lỗi, thiếu spinner | `task_summary_screen.dart` |
| `BUG-M11` | **P2** | Onboarding dài 8 bước; nghịch lý nhãn `"Bắt đầu dùng"` (sang 3 màn quyền) vs `"Để sau"` (vào thẳng Home) | `onboarding_screens.dart` |
| `BUG-M12` | **P2** | `CandidateReviewScreen` lộ UUID 36 ký tự + lệch nút; `CalendarScreen` hiện micro-task & giờ làm/tổng dễ gây nhầm | `candidate_review_screen.dart`, `calendar_screen.dart` |
| `BUG-M13` | **P2** | `AccountScreen` ghi tĩnh `"Chưa xác thực với Backend"`; tên app lẫn giữa `ProPlan VA` và `Melbel` | `support_screens.dart`, `main.dart` |
| `BUG-M14` | **P2** | Poll `GET /capabilities` mỗi 12s suốt phiên tập trung; `Navigator.pop()` không đồng bộ hash URL trên Web | `focus_provider.dart` |

---

## 3. Chi tiết Từng Bug (Bước tái hiện, Bằng chứng Code & Hướng sửa)

### Nhóm P0 — Bắt buộc sửa ngay (Sai dữ liệu / Kẹt luồng / Mất khả năng đọc)

#### `BUG-M01`: Hiển thị sai giờ UTC+0 (`-7 tiếng`) ở Home & Focus; lọc sai "Còn lại hôm nay"
- **Quan sát thực tế trên Chrome:**
  - Chốt lịch ngày `12/11/2026` lúc `14:00` (giờ VN): ở `CandidateReviewScreen` hiển thị đúng `12/11 14:00`, ở `PlanLockedScreen` hiển thị `2:00 PM`, ở `CalendarScreen` hiển thị `14:00 – 14:41`.
  - Nhưng ở **Trang chủ (`HomeScreen`)** và **Màn hình Tập trung (`FocusScreen`)**, chính 3 phiên đó bị tụt 7 tiếng thành **`07:00`**, **`07:56`**, **`09:15`**.
  - Ngoài ra, cả 3 phiên nằm ở ngày mai `12/11` nhưng vẫn bị đưa vào mục **`"Còn lại hôm nay · 3 phiên"`**.
- **Nguyên nhân trong code:**
  - `mobile/lib/screens/home_screen.dart:574-575` (`_clock(DateTime value)`): dùng trực tiếp `value.hour` và `value.minute` mà **thiếu `.toLocal()`**.
  - `mobile/lib/screens/focus_screen.dart:1282-1283` (`_time(DateTime date)`): dùng trực tiếp `date.hour` và `date.minute` mà **thiếu `.toLocal()`**. *(Trong khi `calendar_screen.dart:70-73, 133` đã gọi `.toLocal()` đúng).*
  - `mobile/lib/screens/home_screen.dart:439-450`: chỉ lọc `session.status == 'scheduled'`, **không kiểm tra ngày local của `session.scheduledStart.toLocal()`** có phải hôm nay hay không trước khi đặt dưới tiêu đề `"Còn lại hôm nay"`.
- **Hướng khắc phục:**
  - Dùng `final local = value.toLocal();` trong `_clock` và `_time` (hoặc tách thành `DateTimeFormatter` dùng chung toàn app).
  - Lọc danh sách `"Còn lại hôm nay"` theo ngày local hiện tại, hoặc đổi nhãn nếu hiển thị phiên các ngày kế tiếp.

---

#### `BUG-M02`: Bẫy Voice-only và treo UI `"Đang nghe…"` khi bấm `"Dừng nghe"` ở UF1
- **Quan sát thực tế trên Chrome:**
  - Bấm `"Thêm việc mới"` mở `TaskEntryScreen` chỉ thấy **duy nhất 1 vòng tròn micro** trắng trơn, không có dòng hướng dẫn và **không có bất kỳ nút nào để chuyển sang gõ chữ**.
  - Khi bấm `"Bắt đầu nói"` rồi bấm `"Dừng nghe"` ngay, UI **bị treo ở `"Đang nghe…"`** cho tới khi Web Speech API tự timeout/fail; người dùng phải bị lỗi mic **3 lần** (`Chưa nhận được giọng nói (2/3)`) mới thấy `"Bạn có thể nhập văn bản."` mở ra form gõ chữ.
- **Nguyên nhân trong code:**
  - `mobile/lib/screens/task_entry_screen.dart:22-24`: `_voiceFailureLimit = 3`, `_showTextEntry = false` chặn hiển thị ô text ban đầu; ` _buildVoicePage` (`L295-337`) hoàn toàn không render nút chuyển nhanh sang nhập chữ.
  - `mobile/lib/services/voice_service.dart:68-70`: `OnDeviceSpeechService.stop()` kiểm tra `if (_speech.isListening) await _speech.stop();` — khi plugin chưa kịp chuyển `_speech.isListening == true` hoặc đang khởi tạo trên Web, `stop()` trở thành no-op, khiến `_toggleListening()` (`task_entry_screen.dart:164-171`) gọi `_finishSpeechAttempt()` trong lúc speech session hoặc trạng thái listen chưa giải phóng sạch.
- **Hướng khắc phục:**
  - Thêm nút rõ ràng **`"Hoặc nhập bằng chữ"`** ngay dưới nút mic ở `_buildVoicePage` *(làm giống sheet `"Tùy chỉnh phương án"` ở UF2 — thiết kế rất tốt vì có sẵn cả mic lẫn ô text ngay từ đầu)*.
  - Đảm bảo `OnDeviceSpeechService.stop()` luôn gọi `_speech.stop()` / `_speech.cancel()` an toàn và giải phóng cờ `_isListening` / `_startingSpeech` dù `_speech.isListening` đang là `false`.

---

#### `BUG-M03`: Hiện sai `"Thiếu tối thiểu 0 phút"` / `"Backend báo thiếu 0 phút"` khi khả thi hoặc `search_inconclusive`
- **Quan sát thực tế trên Chrome:**
  - Khi Backend trả `feasibility.status = "feasible"` với `deficit_seconds = 0`, thẻ phương án vẫn hiện: **`"Thiếu tối thiểu 0 phút theo kết quả backend."`**.
  - Khi Backend trả `feasibility.status = "search_inconclusive"` (do mảnh giờ rảnh cuối ngày thiếu `120s` / `2 phút`, `blocking_reasons: [{"code": "FRAGMENTED_FREE_SLOTS"}]`, `unscheduled_seconds: 120`, `deficit_seconds: 0`), cả màn `CandidateReviewScreen` lẫn màn `"Bản nháp tạm lưu"` đều hiện **`"Thiếu tối thiểu 0 phút"`** / **`"Backend báo thiếu 0 phút"`**.
- **Nguyên nhân trong code:**
  - `mobile/lib/screens/candidate_review_screen.dart:100-106`: kiểm tra `if (deficitSeconds != null)` thay vì `if (deficitSeconds != null && deficitSeconds > 0)`.
  - `mobile/lib/screens/uf2_screens.dart:1212-1215`: kiểm tra `deficit == null ? ... : 'Backend báo thiếu ${(deficit / 60).ceil()} phút'` tương tự, không phân biệt `deficit == 0` hay trường hợp `unscheduled_seconds > 0` / `blocking_reasons`.
- **Hướng khắc phục:**
  - Chỉ hiển thị dòng `"Thiếu tối thiểu X phút"` khi `deficitSeconds != null && deficitSeconds > 0`.
  - Khi `feasibilityStatus == 'search_inconclusive'` hoặc `unscheduledSeconds > 0` mà `deficitSeconds == 0`, hiển thị `blockingReasons` thân thiện (ví dụ: *"Còn khoảng X phút chưa xếp vừa vào các khung giờ rảnh hiện tại"*).

---

#### `BUG-M04`: Mất tương phản nghiêm trọng ở Light Mode trên màn `"Bản nháp tạm lưu"`
- **Quan sát thực tế trên Chrome (Light Mode, `412x915`):**
  - Khi bấm `"Lưu phương án chưa chốt"` tại `CandidateReviewScreen`, trên card `"BẢN NHÁP TẠM LƯU"`:
    - Pill **`"Hạn: Ngày kia, 20:00"`** bị **chữ trắng trên nền xám nhạt**, gần như không đọc được.
    - Icon bên trái ô **`"Chưa chốt"`** cũng bị mờ trắng gần như biến mất.
    - Dòng chữ chân trang *"Lưu trữ an toàn trong không gian riêng của bạn"* bị cắt mất nửa chữ dưới mép màn hình.
- **Nguyên nhân trong code:**
  - `mobile/lib/screens/uf2_screens.dart:1154-1196`: sử dụng màu `MelbelColors.kTertiaryContainer` (màu sáng/trắng ở Light theme) cho Icon & Text đặt trên nền `MelbelColors.kSurfaceContainerHighest` và `MelbelColors.kSurfaceContainerLow`.
- **Hướng khắc phục:**
  - Đổi màu chữ & icon sang `MelbelColors.kOnSurface` / `MelbelColors.kOnSurfaceVariant` (hoặc `kPrimary` / `kSecondary`) đảm bảo tỉ lệ tương phản WCAG AA trên cả Dark và Light mode; bọc/padding lại phần đáy trang để tránh cắt dòng chân trang.

---

### Nhóm P1 — Lỗi đồng bộ luồng, UX tập trung & Chức năng giả

#### `BUG-M05`: Không đồng bộ trạng thái Run với Backend & thiếu đường Check-in UF5 khi kết thúc sớm
- **Quan sát thực tế trên Chrome:**
  - Bấm `"Bắt đầu đếm giờ 36 phút"` kích hoạt đồng hồ đếm ngược ngay nhưng **không gửi `POST /api/v1/sessions/{id}/start`** (`task-2857.log` xác nhận chỉ có poll `GET /capabilities`).
  - Bấm `"Kết thúc phiên"` chuyển sang màn `"Phiên đã dừng"`: không có lựa chọn đi sang **Check-in UF5** (`Hoàn thành`, `Chưa xong`, `Việc đột xuất`, `Bỏ qua`), nút hành động chính (nút to nhất) lại là **`"Mở hộp suy nghĩ"`** ngay cả khi `Suy nghĩ đã cất: 0`.
  - Khi bấm `"Quay về trang trước"` về `HomeScreen`, phiên `07:00` vừa dừng vẫn giữ nguyên nhãn **`"Phiên đã xác nhận"`** và **`"Còn lại hôm nay 3 phiên"`**.
- **Nguyên nhân trong code:**
  - `mobile/lib/providers/focus_provider.dart:324-503`: `startSession`, `_beginRun`, `endEarly` hiện mới quản lý vòng đời timer/DND/outbox cục bộ (`_finishLocalRun`), chưa ghép API Run lifecycle của Backend (đã được ghi nhận là việc tích hợp Sprint 3 tại `_temp/CT_SHARED_CONTEXT.md`).
  - `mobile/lib/screens/focus_screen.dart:789-828` (`_ended`): đặt `PrimaryCta(label: 'Mở hộp suy nghĩ')` làm CTA mặc định bất kể `focus.thoughts.isEmpty`, còn `"Quay về trang trước"` chỉ là `TextButton` nhỏ bên dưới.
- **Hướng khắc phục:**
  - Ngắn hạn (UI): Ở `_ended()`, nếu `focus.thoughts.isEmpty`, đặt `PrimaryCta` là `"Quay về trang trước"` (hoặc `"Xác nhận tiến độ phiên"` khi nối UF5).
  - Sprint 3: Nối `POST /sessions/{id}/start` (lấy `run_id` từ server thay vì dùng `sessionId` làm `runId` ở client), `POST /runs/{run_id}/end` và mở màn Check-in UF5.

---

#### `BUG-M06`: Trang chủ chỉ hiển thị 1 `confirmedTask` trong RAM, bỏ qua dữ liệu DB `/schedule`
- **Quan sát thực tế trên Chrome:**
  - `CalendarScreen` tải lịch từ `GET /api/v1/schedule` nên thấy đầy đủ các phiên từ các công việc đã chốt trước đó (`11/11` 4 phiên + `12/11` 4 phiên).
  - Trong khi đó `HomeScreen` chỉ hiển thị `confirmedTask` hiện tại trong bộ nhớ (`TaskProvider`), không đồng bộ danh sách phiên từ Backend nếu mở app lại hoặc chốt nhiều công việc khác nhau.
  - Đồng thời 3 thẻ dưới `"Còn lại hôm nay"` đều hiện cùng một dòng: **`"07:00 · Tôi cần làm slide thuyế..."`**, **`"07:56 · Tôi cần làm slide thuyế..."`**, **`"09:15 · Tôi cần làm slide thuyế..."`** — bị cắt chữ giống hệt nhau nên người dùng không biết mỗi phiên làm bước nào, và các thẻ này không tap vào được.
- **Nguyên nhân trong code:**
  - `mobile/lib/screens/home_screen.dart:438-482`: đọc từ `task.confirmedTask?.sessions`, render `title: '${_clock(session.scheduledStart)} · ${confirmed!.title}'` thay vì tên Step (`stepTitle`) của từng `session.stepId`.
- **Hướng khắc phục:**
  - Hiển thị **`stepTitle`** (hoặc `microTaskText` ngắn) kèm tên Task phụ để phân biệt từng phiên, cho phép tap vào thẻ để mở phiên đó, và đồng bộ danh sách phiên trong ngày từ `/schedule`.

---

#### `BUG-M07`: `"Hỏi về lịch"` (`ScheduleQueryScreen`) là màn hình tĩnh chưa nối dữ liệu thật
- **Quan sát thực tế trên Chrome:**
  - Bấm FAB `"Hỏi lịch"` ở màn `Lịch` $\rightarrow$ nút micro phía trên **không bấm được** (không phải button trong Accessibility tree).
  - Nhập *"Ngày mai tôi có lịch lúc mấy giờ?"* rồi bấm `"Tra cứu"` $\rightarrow$ luôn hiển thị **`"Chưa có dữ liệu lịch / Kết nối lịch hoặc thêm việc để Melbel có thể tra cứu."`** ngay cả khi màn `Lịch` vừa tải 8 phiên từ Backend.
- **Nguyên nhân trong code:**
  - `mobile/lib/screens/support_screens.dart:267-315`: vòng tròn micro dùng `CircleAvatar` không bọc nút/hành động thu âm; nút `"Tra cứu"` (`L292`) chỉ `setState(() => _asked = true)` và render hằng `_NoticeCard(title: 'Chưa có dữ liệu lịch')`.
- **Hướng khắc phục:**
  - Nối dữ liệu từ `/schedule` (hoặc tìm kiếm local theo ngày người dùng hỏi) và gắn hành động cho nút mic, hoặc tạm ẩn nút FAB `"Hỏi lịch"` nếu tính năng chưa sẵn sàng để tránh gây hụt hẫng cho người dùng ADHD.

---

#### `BUG-M08`: `FocusScreen` lặp 2 đồng hồ + 2 nút `"Hộp suy nghĩ"`, cắt nửa nút `"Cất suy nghĩ"` ở màn `412x915`
- **Quan sát thực tế trên Chrome (`FocusScreen` Dark Mode, `412x915`):**
  - Trên cùng một màn hình tập trung (`_active`), đồng hồ đếm ngược **`23:20`** xuất hiện **2 lần xếp dọc ngay sát nhau**: 1 cái trong `_ProgressCard` (`CircularProgressIndicator`), 1 cái chữ cỡ `displaySmall` ngay bên dưới thẻ.
  - Vì bị chiếm chiều cao bởi đồng hồ lặp, nút Push-to-Talk **`"Cất suy nghĩ"`** bị đẩy xuống mép dưới màn hình `412x915` và **bị cắt nửa vòng tròn**, phải cuộn mới nhìn thấy nhãn `"Nhấn giữ để nói"` và `"Kết thúc phiên"`.
  - Cùng lúc, màn hình có **2 nút mở Hộp suy nghĩ** giống nhau (1 `IconButton` ở góc trên phải `L463-468`, 1 `TextButton.icon` ở góc dưới phải `L529-535`).
  - Trong cây Accessibility trên Web, nút `_HoldToRecord` (`L1092-1140`) bị gộp chung chuỗi nhãn vào node `progressbar` phía trên (`CircularProgressIndicator`), và hiện không có nút/gợi ý gõ nhanh cho tình huống người dùng không dùng được thao tác nhấn giữ (hoặc trình duyệt chặn mic) trước khi fail đủ 3 lần.
- **Nguyên nhân trong code:**
  - `mobile/lib/screens/focus_screen.dart:453-539`: giữ đồng thời `_ProgressCard` (có số đếm ngược) + `Text(_formatTime(focus.remainingSeconds))` ở `L493-501`, và lặp `IconButton(onPressed: _showThoughts)` với `TextButton.icon(label: 'Hộp suy nghĩ')`.
- **Hướng khắc phục (chuẩn 1-Step UI W3C COGA):**
  - Bỏ 1 đồng hồ thừa và 1 nút `"Hộp suy nghĩ"` thừa (giữ icon inbox trên góc hoặc 1 nút đáy gọn gàng) để **toàn bộ vùng Tập trung + nút `"Cất suy nghĩ"` nằm gọn trong 1 màn hình `412x915` không cần cuộn**.
  - Bọc `_HoldToRecord` bằng `Semantics(container: true, button: true, ...)` tách khỏi `progressbar`.

---

### Nhóm P2 — Tinh chỉnh UI/UX W3C COGA, Ngôn ngữ & Hiệu năng

1. **`BUG-M09` — Lặp CTA, tràn chấm tiến độ & lỗi chạm/A11y ở `HomeScreen` ([home_screen.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/screens/home_screen.dart), [melbel_ui.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/widgets/melbel_ui.dart)):**
   - Trên cùng màn Home đang có **3 nút Thêm việc** (`"Thêm việc mới"` ở `L192`, thẻ `"Thêm việc"` ở `L532-565`, tab `"Thêm việc"` ở `L140-144`) và **3 lối vào Lịch** (`"Mở lịch phiên"` ở `L419`, `"Lịch tháng"` ở `L493-529`, tab `"Lịch"` ở `L134-138`) $\rightarrow$ vi phạm nguyên tắc giới hạn 3–5 thành phần tương tác chính của W3C COGA.
   - `home_screen.dart:391-392`: `Positioned(left: (barWidth * factor) - 10)` khiến chấm mặt trăng lệch tràn mép trái `-10px` tại mốc `0/3`. Cần clamp `left` trong `[0, barWidth - 20]`.
   - `home_screen.dart:23-28`: `_greetingText` để `if (hour < 12) return 'Chào buổi sáng';`, dẫn đến `00:13` đêm vẫn chào `"CHÀO BUỔI SÁNG"`.
   - `melbel_ui.dart:100-117`: nút Avatar trên AppBar có kích thước `32x32` (nhỏ hơn chuẩn `48x48dp`) và thiếu `Tooltip`/`Semantics(label: 'Tài khoản')`.
   - Thẻ dưới cùng ở Home bị chạm sát thanh bottom nav, nhãn `"PHIÊN TIẾP THEO"` ở Light mode hơi mờ.

2. **`BUG-M10` — `TaskSummaryScreen` chưa đồng nhất picker, ngôn ngữ, lỗi & loading ([task_summary_screen.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/screens/task_summary_screen.dart)):**
   - `_pickSummaryDeadline` (`L186`) mở TimePicker chế độ `input`, còn `_pickAvailability` (`L65-75`) mở chế độ dial cho cả giờ bắt đầu & kết thúc (lại không giữ trạng thái input khi chuyển từ giờ bắt đầu sang giờ kết thúc). Toàn bộ DatePicker/TimePicker đang hiển thị **tiếng Anh** (`OK`, `Cancel`, `Switch to input`, `Select year`, `AM/PM`, `Nov 12, 2026 2:00 PM`) do app chưa cấu hình `GlobalMaterialLocalizations` & locale `vi`.
   - `_pickAvailability` (`L55, L78-84`) **hardcode trừ 7 tiếng** (`time.hour - 7`) cho `UTC+7` thay vì chuyển `.toUtc()` chuẩn từ `DateTime` local như `_pickSummaryDeadline` (`L189`).
   - Nút **`"Tạo phương án"`** (`L409-415`) không hiển thị loading/spinner hay *Breathing Circle* trong lúc chờ Backend (`POST /candidates` mất ~8s khi gọi AI thật).
   - `catch (_)` (`L160-163`) nuốt chi tiết `ApiException`, luôn hiện câu chung chung `"Chưa cập nhật được công việc. Thử lại nhé."`; và khi user bấm `"Bổ sung thông tin"` sau khi chỉnh tay trường, logic `_continue` (`L137-142`) vẫn gọi thêm `PATCH` + `analyze` lặp vòng.
   - Chưa tách `blocking: false` (`AI_FALLBACK_NOTICE`) thành banner nhẹ riêng mà gộp chung vào nhóm câu hỏi thiếu; câu *"Bạn xác nhận kiểu thời gian, mốc bắt đầu, mốc hoàn tất và múi giờ nhé?"* (từ Tier 3 fallback) hiển thị dài và kỹ thuật trên form.

3. **`BUG-M11` — Onboarding dài 8 bước & nghịch lý nhãn nút ([onboarding_screens.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/screens/onboarding_screens.dart)):**
   - Người dùng thử phải đi qua **8 màn hình** (`Login -> Giao diện -> Nhịp làm việc -> Nhắc nhở -> Hướng dẫn -> 3 màn xin quyền Android`) mới vào được `HomeScreen`.
   - Trên màn `"Hướng dẫn lần đầu"`, nút chính **`"Bắt đầu dùng"`** lại mở tiếp 3 màn xin quyền liên tiếp, trong khi nút phụ **`"Để sau"`** lại đi thẳng vào `#/home` ngay. Với người dùng ADHD dễ mất kiên nhẫn khi onboarding dài, nên gộp/trì hoãn xin quyền sang lúc dùng tính năng (hoặc đổi nhãn nút rõ ràng `"Cấp quyền thông báo & tiếp tục"`).

4. **`BUG-M12` — `CandidateReviewScreen` & `CalendarScreen` ([candidate_review_screen.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/screens/candidate_review_screen.dart), [calendar_screen.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/screens/calendar_screen.dart)):**
   - `candidate_review_screen.dart:98`: hiển thị nguyên chuỗi **UUID 36 ký tự** (`Mã phương án 84ec37b4-5e1b-4604-...`) gây rối mắt trên mobile; nên ẩn vào chi tiết hoặc chỉ giữ `Phiên bản #1`.
   - `candidate_review_screen.dart:141-161`: nút `"Tùy chỉnh theo ý tôi"` bọc trong `Align(alignment: Alignment.centerLeft)` còn nút `"Tùy chỉnh bằng giọng nói"` không bọc `Align`, khiến 2 nút xếp dọc nhưng **căn lề lệch hẳn nhau** (1 nút sát trái, 1 nút nằm giữa).
   - `calendar_screen.dart:96-114`: thẻ phiên trong sheet ngày hiển thị `micro_task_text` thay vì tên Step/Task; khoảng giờ `"14:00 – 14:41"` (41p) đi kèm chữ `"Làm 36 phút"` dễ gây hiểu nhầm toán học (vì không ghi chú `gồm 5p nghỉ`); thẻ phiên không tap vào được; và khi có $\ge 4$ phiên trong ngày, nút `"Thêm việc vào ngày này"` bị đẩy xuống ngoài màn nhìn.

5. **`BUG-M13` — `AccountScreen` placeholder tĩnh & Lẫn tên thương hiệu ([support_screens.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/screens/support_screens.dart), [main.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/main.dart)):**
   - `AccountScreen` (`L132-183`) ghi tĩnh toàn bộ `"Chuỗi ngày — · Chưa có dữ liệu"`, `"Thời gian tập trung — · Chưa có dữ liệu"`, `"Hòm thư nháp — · Chưa có dữ liệu tài khoản"`, `"Trạng thái kết nối: Chưa xác thực với Backend"` ngay cả khi Backend `127.0.0.1:8000` đang kết nối sống và đã tạo/chốt Task thành công.
   - Tên trợ lý/ứng dụng không đồng nhất giữa **`ProPlan VA`** (Title app, Android) và **`Melbel`** (`Login`, `Hướng dẫn`, `Help`, `Hỏi về lịch`). Nên thống nhất cách gọi (ví dụ: app **ProPlan VA**, trợ lý **Melbel**).

6. **`BUG-M14` — Tối ưu Probe mạng & URL trên Flutter Web ([focus_provider.dart](file:///D:/HCMUS/CT/ProActive/mobile/lib/providers/focus_provider.dart#L846-L852)):**
   - `_startOfflineProbe()` gọi `GET /api/v1/capabilities` mỗi **`12 giây`** liên tục trong suốt phiên tập trung (`task-2857.log` ghi nhận 17 lần liên tiếp) dù giao diện Focus hiện tại không tiêu thụ thông tin cập nhật từ `/capabilities` ở mỗi chu kỳ.
   - Khi đi sâu `Calendar -> Help -> Quay lại -> Quay lại`, hash URL trình duyệt vẫn kẹt ở `#/help` dù màn hình đã pop về `#/home`.

---

## 4. Checklist Kiểm tra lại (Retest Criteria sau khi Mobile sửa)

- [ ] Tất cả giờ hiển thị trên `Home`, `Calendar`, `Focus`, `CandidateReview` đồng nhất múi giờ máy (VN `+07:00`) bằng `.toLocal()`.
- [ ] `TaskEntryScreen` (UF1) có nút **`"Hoặc nhập bằng chữ"`** ngay từ lần mở đầu tiên; bấm `"Dừng nghe"` không treo `"Đang nghe…"`.
- [ ] Không còn dòng `"Thiếu tối thiểu 0 phút"` / `"Backend báo thiếu 0 phút"` khi kế hoạch hợp lệ hoặc `search_inconclusive`.
- [ ] Màn `"Bản nháp tạm lưu"` ở Light Mode đọc rõ 100% nhãn Hạn chót, icon `"Chưa chốt"`, không cắt chữ chân trang.
- [ ] `FocusScreen` hiển thị **1 đồng hồ duy nhất**, **1 nút Hộp suy nghĩ**, nút `"Cất suy nghĩ"` nằm trọn trong viewport `412x915` không cần cuộn.
- [ ] Bố cục `HomeScreen` tinh gọn, hiển thị đúng `stepTitle` cho từng phiên trong ngày, không tràn chấm tiến độ tại `0/3`.
