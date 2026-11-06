# BÁO CÁO ĐÁNH GIÁ BACKEND (CODE REVIEW) - PHẦN UF1 & UF2
**Ngày thực hiện:** 03/11/2026  
**Người đánh giá:** QA / Tech Lead  
**Phạm vi:** Đối chiếu Source Code Backend hiện tại với tài liệu thiết kế `AlgorithmDes.pdf` và kết quả bộ Unit Test.

---

## 🌟 PHẦN 1: NHỮNG ĐIỂM TỐT (STRENGTHS)
Nhìn chung, kiến trúc tổng thể của Backend được thiết kế rất chắc chắn, hiện đại và thể hiện sự hiểu biết sâu sắc về bài toán đặc thù (ADHD).

### 1. Kiến trúc & Công nghệ
- **Phân tách trách nhiệm (Hybrid Architecture) cực tốt:** Việc dùng LLM (AI) chỉ để trích xuất ngữ nghĩa (chia bước, lấy thông tin) và dùng Python thuần (`scheduler_engine`) để xử lý 100% các phép toán thời gian, giao thoa khoảng rảnh là một quyết định kiến trúc xuất sắc. Nó tránh được việc AI tính toán sai giờ giấc.
- **Pydantic V2 Validation rất chặt chẽ:** Các schema (như `TaskExtractionResult`, `CandidateSession`) có các `model_validator` chặn đứng dữ liệu rác. Ví dụ: ép buộc thời lượng phiên phải bằng tổng các segment, chặn Micro-task bị cộng nhầm vào thời gian phiên chính.
- **Cơ chế AI Fallback (Hedged Execution):** Hàm `_execute_hedged_structured` xử lý fallback 3 tầng (Gemini -> Groq -> Rule-based) cực kỳ mượt mà, có timeout rõ ràng, đảm bảo app không bao giờ bị "treo" hoặc trả màn hình trắng khi mất mạng.

### 2. Nghiệp vụ UF1 (Tương tác & Xác nhận)
- **Chốt chặn Zero Hallucination (Ảo giác AI):** Hàm `_enforce_uf1_temporal_guardrails` dùng Python kiểm tra lại deadline. Dù LLM có cố tình trả về `is_complete=True` cho một deadline trong quá khứ, Python vẫn tự động gạt thành `False` và bắt người dùng nhập lại. 
- **Single-Round Clarification:** Logic gộp tất cả các câu hỏi làm rõ (thiếu scope, thiếu giờ, deadline sai) vào chung 1 danh sách `clarification_questions` giúp UI không phải bắt người dùng trả lời lắt nhắt nhiều lần.

### 3. Nghiệp vụ UF2 (Bẻ nhỏ việc & Chốt lịch)
- **Thuật toán xếp lịch (Scheduler) chuẩn xác:** Hàm `schedule_candidate_sessions` xử lý rất tốt bài toán trừ khoảng bận, cắt khoảng rảnh (two-pointer interval subtraction), và đảm bảo khoảng cách tối thiểu 15 phút (900s) giữa các phiên.
- **Tính nguyên tử (Atomicity) khi chốt lịch:** Hàm `confirm_candidate` ghi toàn bộ Task, Steps, Sessions, Reminders, Schedule Version trong **một transaction duy nhất**. Nếu có 1 bảng lỗi, toàn bộ quá trình rollback, không để lại dữ liệu rác.
- **Cơ chế khóa lạc quan (Optimistic Locking):** Các bảng đều có cột `revision`. Nếu 2 thiết bị cùng sửa một Candidate, thiết bị gửi sau sẽ bị chặn bằng lỗi `REVISION_CONFLICT`.

---

## ⚠️ PHẦN 2: NHỮNG ĐIỂM CẦN CẢI THIỆN (WEAKNESSES & GAPS)
Dù phần lõi rất tốt, nhưng so với tài liệu `AlgorithmDes.pdf`, Backend đang thiếu một số tính năng quan trọng và mắc vài lỗi kỹ thuật (Technical Debt) cần xử lý trước khi release.

### 1. Thiếu hụt nghiệp vụ so với file PDF (Các test đang FAILED)
- **Chưa hỗ trợ "Lịch nháp" khi thiếu thông tin (Ghi chú của Khái - UF1):** 
  - *PDF yêu cầu:* Thiếu thông tin thì vẫn đưa ra lịch đề xuất để người dùng xem, kèm theo câu hỏi.
  - *Code hiện tại:* Chặn đứng (ném lỗi 422), không cho chuyển sang UF2. Điều này làm trải nghiệm người dùng bị kẹt nếu họ chưa nghĩ ra deadline.
- **Chưa có tính năng Rolling Horizon (UF2 - Trang 4):**
  - *PDF yêu cầu:* Với công việc dài (VD: Khóa luận), chỉ xếp lịch 2-3 tuần. Khi xong chặng 1, dùng phần còn lại để xếp chặng 2.
  - *Code hiện tại:* Chưa có API/Service nào xử lý việc "chuyển giao chặng tiếp theo". Hiện tại code chỉ xếp lịch 14 ngày đầu tiên và dừng lại.

### 2. Nợ kỹ thuật (Technical Debt) - Cần sửa gấp
- **Lưu trữ Idempotency bằng In-Memory (Rất nguy hiểm):** 
  - File `draft_service.py` đang dùng `dict` (như `_FAKE_DRAFTS_BY_OWNER`) để lưu dữ liệu tạm. Nếu server backend bị restart (hoặc chạy đa luồng), dữ liệu này sẽ **bay màu**, khiến người dùng bị mất draft hoặc lỗi khi retry request.
  - *Khắc phục:* Chuyển toàn bộ logic lưu trữ này vào Database (dùng bảng `CommandReceipt` giống như `plan_service.py` đã làm).
- **Authentication (Xác thực) mới chỉ là Stub:**
  - Hàm `resolve_dev_current_user` trong `security.py` hiện tại **luôn luôn** trả về `User A` bất kể gửi token gì. Hệ thống chưa hề có code giải mã JWT thực sự.
- **Lỗi Middleware & Cấu hình (`main.py`):**
  - `request_id` chưa được gắn vào middleware, khiến log lỗi không thể trace được (không biết lỗi thuộc về request nào).
  - Cấu hình CORS đang set `allow_origins=["*"]` đi kèm `allow_credentials=True`. Trình duyệt sẽ chặn (CORS Error) nếu Frontend gửi kèm Cookie/Token.
- **Lỗi N+1 Query:** Hàm `list_tasks` trong `plan_service.py` đang lấy danh sách Task xong, rồi dùng vòng lặp `for` để gọi hàm `get_task_by_id` cho từng Task. Nếu user có 50 task, hệ thống sẽ gọi DB 51 lần. Cần dùng `selectinload` hoặc viết query batch.

---

---
*Ghi chú: Bộ test `test_uf1_uf2_matrix.py` và `test_scheduler_engine.py` đã được cập nhật để cover toàn bộ các yêu cầu trên. Các test case đang FAILED chính là checklist những việc Backend cần code thêm để đáp ứng đúng 100% tài liệu thiết kế.*


# Phần API (giao diện app)
### Đánh giá: 
- Phần nhập giọng nói và phần nhập văn bản cần được tách riêng ra. 1 tab cho nhập văn bản và 1 tab cho giọng nói để người dùng lựa chọn.
- Thanh snackbar "Tiếp tục" hoặc các thanh snackbar khác nên được cố định ở dưới mà nổi để người dùng có thể ấn dễ dàng hơn.
- Lịch "Calendar" có vẻ bị bug (Hoặc do Thành chưa cài cái gì đó ) Nên hiện lỗi "Chưa tải được lịch: The XMLHttprequest onError callback was called"
-  The XMLHttprequest onError callback was called: khi truy cập vào lịch thì nó chx hiện cái gì lên hết (XML ở trên).
- Lịch còn hơi đơn sơ. Điều chỉnh lại cho đẹp hơn
- Có sai chính tả 
- Có tận 3 chỗ để thêm việc => Thừa.
- Sau khi thực hiện xong việc thêm việc. Cái ở đầu và cái ở snackbar thì từ chối mở việc. Còn cái giữa thì lại cho phép (Bug nặng nhất rồi).