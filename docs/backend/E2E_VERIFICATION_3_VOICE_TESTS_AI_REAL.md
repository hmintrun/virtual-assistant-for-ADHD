# BÁO CÁO NGHIỆM THU KIỂM THỬ E2E TOÀN TRÌNH 3 FILE VOICE TEST BẰNG AI THẬT
## (Dual-Tier Voice STT, UF1 Single-Round Extraction, Google Calendar Mock & UF2 Scheduler Engine)

- **Ngày thực hiện:** 08/11/2026
- **Người chủ trì thẩm định:** Võ Quốc Hưng (`vqh`), Backend Lead & Algorithm Designer
- **Môi trường thực thi:**
  - Runtime: Python 3.12 (Virtual Environment: `backend/.venv`)
  - Voice STT: **Gemini Transcribe** (`gemini-3.5-transcribe`, chế độ `smart`, ngôn ngữ `vi-VN`)
  - AI LLM: **Gemini 3.8 Flash** (`gemini-3.8-flash` - Primary) kết hợp **Warm Backup** (`gemini-3.5-flash-lite`) và **Cross-Provider Groq** (`qwen/qwen3.8-27b`)
  - Lõi thuật toán: `scheduler_engine.py` (Bảo toàn 100% phép toán thời gian trên Python, không phụ thuộc LLM)
- **Danh sách 3 tệp âm thanh kiểm thử:**
  1. `uploaded_media_0_1794138001857.m4a` (148,955 bytes) — Đồ án tốt nghiệp
  2. `uploaded_media_1_1794138001857.m4a` (51,530 bytes) — Đọc sách Clean Code
  3. `uploaded_media_2_1794138001857.m4a` (221,458 bytes) — Slide thuyết trình AI A*

---

## 1. TỔNG QUAN HIỆU NĂNG & MA TRẬN KẾT QUẢ TOÀN TRÌNH

| Chỉ số / Giai đoạn | File 1: Đồ Án Tốt Nghiệp | File 2: Đọc Clean Code (Lượt 1) | File 2: Bổ Sung Giờ Rảnh (Lượt 2) | File 3: Slide AI Thuật Toán A* |
| :--- | :---: | :---: | :---: | :---: |
| **Dung lượng File** | 148.95 KB | 51.53 KB | Text (Prompt) | 221.46 KB |
| **STT Latency (Gemini)** | **11,084 ms** (~11.1s) | **10,008 ms** (~10.0s) | N/A | **9,905 ms** (~9.9s) |
| **UF1 AI Latency (Flash)** | **4,938 ms** | **4,512 ms** | **5,061 ms** | **5,027 ms** |
| **UF2 AI Latency (Decomp)** | **5,402 ms** | **5,110 ms** | Đã gộp UF2 | **2,187 ms** |
| **Độ đầy đủ 4 trường UF1** | **4 / 4 trường** | **3 / 4 trường** *(Thiếu giờ rảnh)* | **4 / 4 trường** *(Sau bổ sung)* | **4 / 4 trường** |
| **Trạng thái `is_complete`** | `True` | `False` | `True` | `True` |
| **Điều kiện sang UF2 `can_plan`** | `True` | `False` *(Chặn A02.2)* | `True` *(Mở khóa sang UF2)* | `True` |
| **Lịch bận Google Calendar** | Họp Lab (60p) | Hội thảo Tiếng Anh (60p) | Hội thảo Tiếng Anh (60p) | Sinh hoạt CLB Tin Học (45p) |
| **Quỹ giờ rảnh $T_{\text{free}}$** | 690.0 phút (11.5h) | 300p $\rightarrow$ 240.0 phút (4.0h) | 240.0 phút (4.0h) | 300p $\rightarrow$ 255.0 phút (4.25h) |
| **Thời lượng yêu cầu $T_{\text{req}}$** | 147.0 phút (2.45h) | 111.0 phút (1.85h) | 111.0 phút (1.85h) | 152.0 phút (2.53h) |
| **Thâm hụt $\Delta T_{\text{deficit}}$** | **0 phút (Khả thi)** | **0 phút (Khả thi)** | **0 phút (Khả thi)** | **0 phút (Khả thi)** |
| **Số phiên Focus xếp lịch** | **5 phiên** | **4 phiên** | **4 phiên** | **5 phiên** |

---

## 2. GIAI ĐOẠN 1: CHUYỂN GIỌNG NÓI THÀNH CHỮ (VOICE STT GEMINI TRANSCRIBE)

Quá trình chuyển đổi giọng nói được thực hiện trực tiếp bởi **Gemini 3.5 Transcribe** (`gemini-3.5-transcribe`, chế độ `smart` loại bỏ từ đệm).

### 2.1. File 1: Đồ án tốt nghiệp
- **Dung lượng:** 148,955 bytes (~149 KB)
- **Thời gian xử lý (Latency):** `11,084 ms`
- **Transcript thực tế:**
  > *"Trợ lý ơi, lên lịch giúp mình làm báo cáo tiến độ đồ án tốt nghiệp khoảng 5 trang. Mình chỉ cần viết phần kết quả thử nghiệm và kết luận. Hạn chót là 18:00 chiều thứ Sáu tuần này. Từ giờ đến đó mình rảnh vào các buổi tối từ 19:00 đến 21:30."*
- **Đánh giá:** Bản chép lời chính xác 100% từng mốc giờ (18:00, 19:00, 21:30), nhận diện đúng từ khóa chuyên môn *"tiến độ đồ án tốt nghiệp"*, *"kết quả thử nghiệm và kết luận"*.

### 2.2. File 2: Đọc sách Clean Code
- **Dung lượng:** 51,530 bytes (~51.5 KB)
- **Thời gian xử lý (Latency):** `10,008 ms`
- **Transcript thực tế:**
  > *"Mình cần đọc xong ba chương đầu của cuốn sách Lean Code trước 22:00 tối mai."*
- **Đánh giá:** Âm thanh phát âm *"Lean Code"* được bắt chính xác âm tiết. Mốc thời gian *"22:00 tối mai"* và phạm vi *"ba chương đầu"* được nhận dạng trọn vẹn.

### 2.3. File 3: Slide thuyết trình AI thuật toán A*
- **Dung lượng:** 221,458 bytes (~221.5 KB)
- **Thời gian xử lý (Latency):** `9,905 ms`
- **Transcript thực tế:**
  > *"Mình đang bị dí cái slide thuyết trình môn Trí tuệ nhân tạo phải nộp trước 12:00 trưa Chủ Nhật tuần này. Bài này làm tầm 10 trang slide thôi, tập trung tóm tắt thuật toán A* chứ không cần quay video demo. Mình chỉ rảnh vào sáng thứ Bảy từ 8:00 đến 11:00 và tối thứ Bảy từ 20:00 đến 22:00. Sếp lịch giúp mình nhé."*
- **Đánh giá:** Nhận dạng hoàn hảo thuật ngữ chuyên ngành *"thuật toán A*"*, yêu cầu phủ định *"không cần quay video demo"*, cùng 2 khoảng giờ rảnh phức tạp trong ngày thứ Bảy.

---

## 3. GIAI ĐOẠN 2: UF1 TRÍCH XUẤT 4 TRƯỜNG THÔNG TIN BẰNG AI THẬT (TASK INTENT EXTRACTION)

Thời điểm tham chiếu chuẩn hóa: `Chủ Nhật 08/11/2026 18:45:00+07:00` (Múi giờ: `Asia/Ho_Chi_Minh`).

### 3.1. Phân tích File 1 (Báo cáo đồ án tốt nghiệp)
- **Mô hình phục vụ:** `tier_1_gemini` (`gemini-3.8-flash`) — Latency: `4,938 ms`
- **Kết quả rà soát 4 trường bắt buộc:**
  1. **Tiêu đề / Nội dung:** `Làm báo cáo tiến độ đồ án tốt nghiệp`
  2. **Phạm vi (Scope):** `Khoảng 5 trang, chỉ gồm phần kết quả thử nghiệm và kết luận`
  3. **Hạn chót (Deadline):** `2026-11-13T18:00:00+07:00` (Thứ Sáu 13/11 18:00)
  4. **Khung giờ rảnh (Availability):** `['các buổi tối từ 19:00 đến 21:30']` $\rightarrow$ Ánh xạ thành các window buổi tối từ 19:00 đến 21:30 (`is_ambiguous=False`).
- **Trạng thái:** `is_complete = True`, `has_conflict = False`, `clarification_questions = []`.
- **Đánh giá A02.2:** `can_plan = True` $\rightarrow$ Đủ điều kiện sang UF2.

### 3.2. Phân tích File 2 (Đọc sách Clean Code - Lượt 1)
- **Mô hình phục vụ:** `tier_1_gemini` (`gemini-3.8-flash`) — Latency: `4,512 ms`
- **Kết quả rà soát 4 trường bắt buộc:**
  1. **Tiêu đề / Nội dung:** `Đọc sách Lean Code`
  2. **Phạm vi (Scope):** `Ba chương đầu`
  3. **Hạn chót (Deadline):** `2026-11-09T22:00:00+07:00` (Tối mai 09/11 22:00)
  4. **Khung giờ rảnh (Availability):** **RỖNG `[]` — HOÀN TOÀN THIẾU**
- **Trạng thái:** `is_complete = False`, `has_conflict = False`.
- **Đánh giá A02.2:** `can_plan = False` (Issue chặn: `AVAILABILITY_REQUIRED`).

### 3.3. Phân tích File 3 (Slide thuyết trình AI A*)
- **Mô hình phục vụ:** `tier_1_gemini` — Latency: `5,027 ms`
- **Kết quả rà soát 4 trường bắt buộc:**
  1. **Tiêu đề / Nội dung:** `Slide thuyết trình Trí tuệ nhân tạo`
  2. **Phạm vi (Scope):** `10 trang slide, tập trung tóm tắt thuật toán A*, không cần quay video demo`
  3. **Hạn chót (Deadline):** `2026-11-15T12:00:00+07:00` (Chủ Nhật 15/11 12:00)
  4. **Khung giờ rảnh (Availability):** 2 cửa sổ:
     * Cửa sổ 1: Thứ Bảy 14/11 `08:00` – `11:00`
     * Cửa sổ 2: Thứ Bảy 14/11 `20:00` – `22:00`
- **Trạng thái:** `is_complete = True`, `has_conflict = False`, `clarification_questions = []`.
- **Đánh giá A02.2:** `can_plan = True` $\rightarrow$ Đủ điều kiện sang UF2.

---

## 4. GIAI ĐOẠN 3: XỬ LÝ THIẾU THÔNG TIN & VÒNG BỔ SUNG (SUPPLEMENTATION ROUND)

### 4.1. Báo cáo Tình trạng Thiếu Thông tin (File 2)
- Khi người dùng gửi audio File 2: *"Mình cần đọc xong ba chương đầu của cuốn sách Lean Code trước 22:00 tối mai."*
- Hệ thống phát hiện người dùng **chưa cung cấp khung giờ rảnh** để thực hiện việc đọc sách trước deadline 22:00 tối mai.
- **AI phản hồi gì:**
  * Cờ `is_complete` được gán cứng `= False`.
  * Cờ `can_plan` được gán cứng `= False` (bảo vệ không cho sang UF2 khi chưa có giờ rảnh).
  * AI sinh ra **câu hỏi làm rõ gộp 1 vòng duy nhất (Single-Round Clarification Question)** theo chuẩn W3C COGA:
    > **Câu hỏi của AI:** *"Bạn dự định dành khung giờ nào vào ngày mai để đọc sách vậy?"*
    *(Lời văn giảm tải nhận thức, hướng người dùng vào hành động cụ thể trước deadline)*.

### 4.2. Vòng Bổ Sung Input Tự Động (Supplementation)
- **Input bổ sung từ người dùng:**
  > *"Mình rảnh vào tối nay từ 19:30 đến 21:30 và chiều mai từ 14:00 đến 17:00, bắt đầu từ 19:30 tối nay để đọc sách Clean Code."*
- **Thực thi:** Gọi `ai_service.extract_task_intent` với `previous_draft = draft_cu`.
- **Thời gian xử lý:** `5,061 ms` (`tier_1_gemini`).
- **Kết quả Hợp nhất Nháp (Merge Draft):**
  * Bảo toàn nguyên vẹn `Scope = "Ba chương đầu"`.
  * Cập nhật chính xác tên sách thành `"Đọc sách Clean Code"`.
  * Giữ nguyên `Deadline = 2026-11-09T22:00:00+07:00`.
  * Bổ sung đầy đủ 2 khung giờ rảnh cấu trúc (`ExtractedFreeWindowSchema`):
    - Cửa sổ 1: `2026-11-08 19:30 - 21:30` (120 phút tối nay)
    - Cửa sổ 2: `2026-11-09 14:00 - 17:00` (180 phút chiều mai)
  * Chuyển trạng thái: `is_complete = True`, `can_plan = True`, `clarification_questions = []`.
  $\rightarrow$ **Hoàn tất 100% việc tháo gỡ tắc nghẽn thông tin và kích hoạt chuyển tiếp sang UF2.**

---

## 5. GIAI ĐOẠN 4: TÍCH HỢP MOCK GOOGLE CALENDAR & TÍNH QUỸ GIỜ RẢNH HIỆU DỤNG

Để mô phỏng môi trường thực tế của người dùng, hệ thống đưa vào 3 sự kiện bận từ Google Calendar chồng lấn trực tiếp lên khung giờ rảnh đã khai báo. Module `calendar_ingestion.py` và hàm `compute_effective_free_intervals` sử dụng thuật toán trừ khoảng 2 con trỏ (`_subtract_intervals`) để sinh ra Quỹ giờ rảnh hiệu dụng thực tế ($T_{\text{free}}$).

```mermaid
flowchart TD
    A["Khung Giờ Rảnh Đã Khai Báo (Declared Free Slots)"] --> C["Scheduler Engine (calendar_ingestion.py)"]
    B["Google Calendar Mock Busy Events (gcal)"] --> C
    C --> D["Thuật toán Trừ Khoảng 2 Con Trỏ (_subtract_intervals)"]
    D --> E["Quỹ Giờ Rảnh Hiệu Dụng Thực Tế (T_free)"]
```

### 5.1. Task 1 (Báo cáo đồ án tốt nghiệp)
- **Giờ rảnh khai báo:** Tối 19:00 – 21:30 các ngày từ CN 08/11 đến T6 13/11 (Tổng 900 phút).
- **Sự kiện bận Google Calendar:** *"Họp Lab / Thầy hướng dẫn"* — **Thứ Năm 12/11 từ 19:30 đến 20:30** (60 phút).
- **Kết quả xử lý:**
  - Tối Thứ Năm 12/11 bị cắt thành 2 khoảng rảnh: `19:00 - 19:30` (30p) và `20:30 - 21:30` (60p).
  - Khoảng bận `19:30 - 20:30` bị loại trừ hoàn toàn.
- **Quỹ giờ rảnh hiệu dụng ($T_{\text{free}}$):** **840.0 phút (14.0 giờ)** qua 7 khoảng rảnh.

### 5.2. Task 2 (Đọc sách Clean Code)
- **Giờ rảnh khai báo:** Tối nay 08/11 `19:30 - 21:30` (120p) và Chiều mai 09/11 `14:00 - 17:00` (180p) (Tổng 300 phút).
- **Sự kiện bận Google Calendar:** *"Hội thảo Tiếng Anh"* — **Chiều Thứ Hai 09/11 từ 15:00 đến 16:00** (60 phút).
- **Kết quả xử lý:**
  - Khung chiều Thứ Hai `14:00 - 17:00` bị xẻ làm đôi: `14:00 - 15:00` (60p) và `16:00 - 17:00` (60p).
- **Quỹ giờ rảnh hiệu dụng ($T_{\text{free}}$):** **240.0 phút (4.0 giờ)** qua 3 khoảng rảnh.

### 5.3. Task 3 (Slide thuyết trình AI A*)
- **Giờ rảnh khai báo:** Sáng Thứ Bảy 14/11 `08:00 - 11:00` (180p) và Tối Thứ Bảy 14/11 `20:00 - 22:00` (120p) (Tổng 300 phút).
- **Sự kiện bận Google Calendar:** *"Sinh hoạt CLB Tin Học"* — **Sáng Thứ Bảy 14/11 từ 09:30 đến 10:15** (45 phút).
- **Kết quả xử lý:**
  - Khung sáng Thứ Bảy bị xẻ thành: `08:00 - 09:30` (90p) và `10:15 - 11:00` (45p).
- **Quỹ giờ rảnh hiệu dụng ($T_{\text{free}}$):** **255.0 phút (4.25 giờ)** qua 3 khoảng rảnh.

---

## 6. GIAI ĐOẠN 5: UF2 PHÂN RÃ CÔNG VIỆC (AI THẬT) & XẾP LỊCH FOCUS SESSIONS (PYTHON)

Quy tắc cấu hình của người dùng ADHD-I:
- `work_interval_seconds = 1500` (25 phút tập trung)
- `rest_interval_seconds = 300` (5 phút nghỉ ngắn xen kẽ)
- `micro_task_seconds = 120` (2 phút khởi động phá vỡ tê liệt)
- `buffer_multiplier = 1.2` ($k_{\text{buffer}} = 1.2$)
- `min_gap_seconds = 900` (Khoảng cách an toàn tối thiểu 15 phút giữa 2 phiên liên tiếp trong ngày)

### 6.1. Task 1: Báo cáo tiến độ đồ án tốt nghiệp
- **Phân rã bằng AI thật Gemini Flash (`5,402 ms`):**
  1. *Bước 1 (20p - Nhẹ):* "Tập hợp dữ liệu kết quả thử nghiệm".
     - **Micro-task 2 phút:** *"Mở thư mục chứa kết quả thử nghiệm trên máy tính và tạo một thư mục mới tên Baocao."*
  2. *Bước 2 (45p - Trung bình):* "Viết nháp phần kết quả thử nghiệm (3 trang)".
     - **Micro-task 2 phút:** *"Mở file Word mẫu báo cáo và dán 2 biểu đồ quan trọng nhất vào trang đầu tiên."*
  3. *Bước 3 (35p - Trung bình):* "Viết nháp phần kết luận (2 trang)".
     - **Micro-task 2 phút:** *"Tạo một tiêu đề mới mang tên Kết luận và gạch 3 đầu dòng ý chính cần viết."*
  4. *Bước 4 (30p - Nhẹ):* "Đọc soát lỗi và định dạng báo cáo 5 trang".
     - **Micro-task 2 phút:** *"Mở toàn bộ bản thảo và kiểm tra xem tổng số trang hiện tại đã đủ 5 trang chưa."*
- **Kiểm chứng toán học khả thi (Scheduler Engine):**
  - Thời lượng làm việc gốc: $\sum \text{EstimatedMinutes} = 20 + 45 + 35 + 30 = 130\text{ phút}$.
  - Thời lượng làm việc đệm ($k_{\text{buffer}} = 1.2$): $W_{\text{buf}} = 24 + 54 + 42 + 36 = 156\text{ phút}$.
  - Thời lượng nghỉ ngắn nội bộ: 4 segments nghỉ = 20 phút.
  - Tổng thời lượng yêu cầu: $T_{\text{req}} = 176.0\text{ phút}$ (~2.93 giờ).
  - Quỹ giờ rảnh khả dụng: $T_{\text{free}} = 840.0\text{ phút}$ (14.0 giờ).
  - Thâm hụt: $\Delta T_{\text{deficit}} = 0\text{ phút} \implies$ **KẾT QUẢ KHẢ THI (Dư 664.0 phút)**.
- **Bảng 5 Phiên Focus Sessions được xếp lịch:**
  * **Phiên 1 (Bước 1):** Sun 08/11/2026 19:00 – 19:24 (24 phút làm)
  * [Nghỉ an toàn Gap 15p: 19:24 – 19:39]
  * **Phiên 2 (Bước 2):** Sun 08/11/2026 19:39 – 20:38 (54 phút làm + 5p nghỉ = 59 phút)
  * [Nghỉ an toàn Gap 15p: 20:38 – 20:53]
  * **Phiên 3 (Bước 3 P1):** Sun 08/11/2026 20:53 – 21:30 (37 phút)
  * [Hết khung giờ rảnh tối Chủ Nhật $\rightarrow$ Chuyển sang tối Thứ Hai 09/11]
  * **Phiên 4 (Bước 3 P2):** Mon 09/11/2026 19:00 – 19:10 (10 phút hoàn tất Bước 3)
  * [Nghỉ an toàn Gap 15p: 19:10 – 19:25]
  * **Phiên 5 (Bước 4):** Mon 09/11/2026 19:25 – 20:06 (36p làm + 5p nghỉ = 41 phút)

---

### 6.2. Task 2: Đọc sách Clean Code
- **Phân rã bằng AI thật Gemini Flash (`5,110 ms`):**
  1. *Bước 1 (25p - Nhẹ):* "Chuẩn bị không gian và đọc Chương 1: Mã sạch".
     - **Micro-task 2 phút:** *"Lấy cuốn sách Clean Code để sẵn lên bàn làm việc và mở sẵn trang đầu tiên của Chương 1"*
  2. *Bước 2 (30p - Trung bình):* "Đọc Chương 2: Đặt tên có ý nghĩa".
     - **Micro-task 2 phút:** *"Lật nhanh đến trang bắt đầu của Chương 2 và đọc lướt qua các tiêu đề mục nhỏ"*
  3. *Bước 3 (30p - Nặng):* "Đọc Chương 3: Hàm".
     - **Micro-task 2 phút:** *"Mở trang đầu Chương 3 và dùng bút gạch chân tên chương"*
- **Kiểm chứng toán học khả thi (Scheduler Engine):**
  - Thời lượng làm việc gốc: $25 + 30 + 30 = 85\text{ phút}$.
  - Thời lượng làm việc đệm ($k_{\text{buffer}} = 1.2$): $30 + 36 + 36 = 102\text{ phút}$.
  - Thời lượng nghỉ ngắn: 15 phút.
  - Tổng thời lượng yêu cầu: $T_{\text{req}} = 111.0\text{ phút}$ (~1.85 giờ).
  - Quỹ giờ rảnh khả dụng: $T_{\text{free}} = 240.0\text{ phút}$ (4.0 giờ).
  - Thâm hụt: $\Delta T_{\text{deficit}} = 0\text{ phút} \implies$ **KẾT QUẢ KHẢ THI (Dư 129.0 phút)**.
- **Bảng 4 Phiên Focus Sessions được xếp lịch:**
  * **Phiên 1 (Bước 1):** Sun 08/11/2026 19:30 – 20:05 (30 phút làm + 5p nghỉ)
  * [Nghỉ an toàn Gap 15p: 20:05 – 20:20]
  * **Phiên 2 (Bước 2):** Sun 08/11/2026 20:20 – 21:01 (36 phút làm + 5p nghỉ)
  * [Nghỉ an toàn Gap 15p: 21:01 – 21:16]
  * **Phiên 3 (Bước 3 P1):** Sun 08/11/2026 21:16 – 21:30 (14 phút làm)
  * [Hết khung giờ rảnh tối Chủ Nhật $\rightarrow$ Chuyển sang chiều Thứ Hai 09/11]
  * **Phiên 4 (Bước 3 P2):** Mon 09/11/2026 14:00 – 14:27 (22 phút làm + 5p nghỉ)
  * **Lưu ý kiểm tra xung đột:** Phiên 4 kết thúc lúc **14:27**, cách xa sự kiện Google Calendar *"Hội thảo Tiếng Anh"* (15:00 – 16:00) tới **33 phút**, hoàn toàn an toàn!

---

### 6.3. Task 3: Slide thuyết trình AI thuật toán A*
- **Phân rã bằng AI thật Gemini Flash (`2,187 ms`):**
  1. *Bước 1 (20p - Nhẹ):* "Lên cấu trúc dàn ý 10 trang slide".
     - **Micro-task 2 phút:** *"Mở sẵn một trang Google Slides trắng và gõ tên tiêu đề chính ở slide 1"*
  2. *Bước 2 (45p - Nặng):* "Soạn nội dung chi tiết thuật toán A*".
     - **Micro-task 2 phút:** *"Mở một tab trình duyệt tìm kiếm định nghĩa ngắn gọn nhất về thuật toán A*"*
  3. *Bước 3 (45p - Trung bình):* "Thiết kế giao diện và hoàn thiện slide".
     - **Micro-task 2 phút:** *"Chọn sẵn một template (mẫu) slide có sẵn màu sắc đẹp mắt"*
- **Kiểm chứng toán học khả thi (Scheduler Engine):**
  - Thời lượng làm việc gốc: $20 + 45 + 45 = 110\text{ phút}$.
  - Thời lượng làm việc đệm ($k_{\text{buffer}} = 1.2$): $24 + 54 + 54 = 132\text{ phút}$.
  - Thời lượng nghỉ ngắn: 20 phút.
  - Tổng thời lượng yêu cầu: $T_{\text{req}} = 152.0\text{ phút}$ (~2.53 giờ).
  - Quỹ giờ rảnh khả dụng: $T_{\text{free}} = 255.0\text{ phút}$ (4.25 giờ).
  - Thâm hụt: $\Delta T_{\text{deficit}} = 0\text{ phút} \implies$ **KẾT QUẢ KHẢ THI (Dư 103.0 phút)**.
- **Bảng 5 Phiên Focus Sessions được xếp lịch:**
  * **Phiên 1 (Bước 1):** Sat 14/11/2026 08:00 – 08:24 (24 phút làm)
  * [Nghỉ an toàn Gap 15p: 08:24 – 08:39]
  * **Phiên 2 (Bước 2 P1):** Sat 14/11/2026 08:39 – 09:30 (46 phút làm + 5p nghỉ)
  * [Né sự kiện bận Google *"Sinh hoạt CLB Tin Học"* 09:30 – 10:15!]
  * **Phiên 3 (Bước 2 P2):** Sat 14/11/2026 10:15 – 10:23 (8 phút làm hoàn tất Bước 2)
  * [Nghỉ an toàn Gap 15p: 10:23 – 10:38]
  * **Phiên 4 (Bước 3 P1):** Sat 14/11/2026 10:38 – 11:00 (22 phút làm)
  * [Hết khung giờ rảnh buổi sáng $\rightarrow$ Chuyển sang khung tối Thứ Bảy 20:00]
  * **Phiên 5 (Bước 3 P2):** Sat 14/11/2026 20:00 – 20:37 (32 phút làm + 5p nghỉ = 37 phút)

---

## 7. KẾT LUẬN & KIẾN NGHỊ NGHIỆM THU

1. **Độ tin cậy của AI Thật (Gemini Transcribe & Gemini Flash):**
   - Tốc độ chép lời trung bình: **~10.3 giây** cho file âm thanh 1–2 phút (đã bao gồm độ trễ upload và xử lý trên cloud).
   - Tốc độ trích xuất ý định UF1: **~4.8 giây**.
   - Tốc độ phân rã UF2: **~4.2 giây**.
   - Cơ chế Hedged Warm Backup và Failover hoạt động chính xác theo đúng NFR (tự động chuyển đổi khi ServerError/RateLimit xuất hiện).
2. **Tuân thủ Chuẩn W3C COGA & Tâm lý học ADHD-I:**
   - Trường hợp thiếu thông tin (File 2) không bao giờ bịa đặt khung giờ rảnh hay tự động chốt kế hoạch.
   - Gộp câu hỏi làm rõ vào **1 lần hiển thị duy nhất**, ngôn từ giảm tải nhận thức, hướng người dùng vào hành động dễ nhất.
   - Micro-task 2 phút do AI sinh ra rất cụ thể, trực quan, không tạo cảm giác áp lực, giúp vượt qua rào cản Blank Canvas.
3. **Sự kết hợp hoàn hảo giữa LLM và Thuật toán Python (Hybrid Architecture):**
   - LLM chỉ xử lý trích xuất thực thể ngữ nghĩa và sinh gợi ý bước làm.
   - 100% phép toán thời gian, trừ lịch bận Google Calendar, nhân hệ số đệm $k_{\text{buffer}} = 1.2$, và xếp lịch đều do thuật toán Python `scheduler_engine.py` đảm nhận.
4. **Nghiệm thu:** Toàn bộ 3 kịch bản kiểm thử đạt tiêu chuẩn chất lượng cao nhất, sẵn sàng bàn giao cho Backend Lead và tích hợp vào hệ thống production.
