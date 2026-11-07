
# Nghiệm thu BA và bàn giao MT

**Nguồn rà soát:** `dev` tại [`7e357f8`](https://github.com/hmintrun/virtual-assistant-for-ADHD/commit/7e357f8332e54601e69c1de02cc043f4e4811615), sau PR #11, cùng phần MT trên nhánh `codex/mt-dev-continuation`. Xem [đặc tả chung](../../Project.md) và [contract tối thiểu tuần 2](MT_WEEK2.md).

## Phần việc MT đã đưa lên nền mới

- Đồng bộ quyết định tuần 2 cho nút **Nghỉ ngay** và **Báo bận**, trạng thái CheckIn và dữ liệu bàn giao giữa backend và mobile. Nhắc lại: kết thúc timer không xác nhận hoàn thành bất kỳ tầng nào.
- Rút mỗi gợi ý micro-task dự phòng còn một hành động nhỏ. Test HTTP bảo vệ các đường chuyển Thought có nguồn sai chủ, không tồn tại, hoặc còn Run đang chạy/tạm dừng trước khi lấy STT hoặc gọi LLM.
- UF6 dùng lý do đã chuẩn hóa và trạng thái phương án do Python xác định. Provider chọn một câu đã kiểm; backend bỏ phản hồi ngoài danh sách và dùng câu dự phòng cùng ngữ cảnh. Tiêu đề Task và lý do tự do không đi vào prompt. Câu chữ phân biệt giữa có phương án chờ duyệt và chưa có phương án.

## Checklist UF1–UF7

| Luồng | Điều kiện nghiệm thu | Bằng chứng hoặc điểm còn chờ | Người phụ trách |
| --- | --- | --- | --- |
| UF1 | Gộp câu hỏi vào một lần hiển thị, giữ nháp khi thoát, chỉ cho xác nhận khi đủ bốn thông tin; quyền sửa thuộc người dùng. | Test planning API và Flutter trên nền PR #11 đã kiểm việc nhập giờ riêng, fallback và mở lại nháp. Cần kiểm STT tiếng Việt và nguồn bận Calendar thật. Nền hiện dùng Gemini Transcribe, trong khi mục tiêu là Groq Whisper. | MT, Khải, Hưng, An |
| UF2 | Chia Task → Step → Session; Python kiểm giờ rảnh, deadline, buffer và xung đột. Hiển thị phần chưa xếp; chỉ chốt đúng phạm vi người dùng đã xem. | Test API và Flutter bao phủ kế hoạch chốt được, không khả thi và kế hoạch một phần. Đồng bộ Google thật chưa có trong đường chốt. | Hưng, Cương, An, Thành |
| UF3 | Nhắc 15/10/5 phút; một thao tác khởi động; Focus, DND và timer chỉ hiển thị Step hiện tại. Kiểm âm thanh hết phiên sai số ≤1 giây khi Doze. | Có dữ liệu và repository hỗ trợ. Router Run/Focus chưa đăng ký, capabilities chưa bật offline start. Cần kiểm APK thật, kể cả lúc mất mạng hoặc khởi động lại. | Hưng, Cương, An, Thịnh, Khải |
| UF4 | Trong phiên chỉ lưu audio thô, timer tiếp tục. Retry tối đa ba lượt, có ô chữ khi thất bại, Undo 5 giây. STT chỉ chạy sau phiên và khi người dùng chủ động chuyển bản ghi. | Có test chặn nguồn Thought không hợp lệ hoặc còn phiên trước provider ở các entry point hiện có. Cần API Thought, kiểm recorder/outbox và đối soát toàn bộ đường gọi trên thiết bị. | Hưng, Cương, Tín, An, Thịnh, Khải |
| UF5 | Phân biệt phiên, bước và công việc. Kiểm đủ năm kết quả, ba chip bỏ qua, Undo và khôi phục DND sau 5 phút nếu không phản hồi. | Contract hai lựa chọn tuần 2 đã rõ. Router CheckIn, Voice Check-in và ca phục hồi DND trên thiết bị còn chờ. | Hưng, Cương, An, Khải, Thành |
| UF6 | Đóng app giữ lịch hiện hành. Mở lại kiểm thời gian và revision; chỉ apply khi người dùng duyệt, trong phạm vi cho phép. | Lời giải thích được kiểm bằng provider giả lập. API tạo/duyệt proposal, tính lại khi mở và đồng bộ Calendar thật còn chờ. | MT, Hưng, Cương, An |
| UF7 | Áp cấu hình từ phiên kế tiếp; thay thời lượng ảnh hưởng lịch phải đi qua đề xuất UF6. Ưu tiên hệ số user và chỉ gợi ý theo policy đã chốt. | Settings cơ bản có HTTP test. Luồng tác động lịch trả `POLICY_NOT_CONFIGURED` khi chưa có policy. A20 dùng trung vị theo Step ở cấu hình thử nghiệm, khác mục tiêu trung bình 7–10 Session và kiểm cuối tuần. Cần BA/backend chốt policy trước nghiệm thu. | MT, Hưng, Cương, Thành |

Dùng [W3C COGA](https://www.w3.org/TR/coga-usable/) để rà chữ ngắn, hành động rõ, hỗ trợ nhớ ngữ cảnh và Undo. Giới hạn 3–5 thao tác chính và Undo 5 giây là tiêu chí sản phẩm trong Project.md. [Safren et al.](https://jamanetwork.com/journals/jama/fullarticle/186469) là căn cứ cho hướng hỗ trợ lập kế hoạch và điều chỉnh hành vi. [Fuermaier et al.](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0058338) ghi nhận khó khăn ở bước lập kế hoạch trong một bài đo trí nhớ dự định ở người lớn ADHD. Hiệu quả của ứng dụng và các mục tiêu SMART cần dữ liệu đo riêng, kể cả tỷ lệ hoàn thành sau 30–60 ngày.

## Kiểm chứng lời giải thích UF6

`AIService.generate_reschedule_empathy` dùng schema `LLMRescheduleEmpathyPayload` và thời hạn của profile tương tác hiện hành. Caller chỉ truyền `proposal_available=True` sau khi Python đã tạo được phương án hợp lệ đang chờ duyệt. `False` là mặc định. `empathetic_note` là câu dùng trên màn hình chính; `encouragement_tip` là gợi ý tùy chọn.

| Tình huống | Điều cần quan sát |
| --- | --- |
| Bận đột xuất, đã có phương án | Câu nêu lý do và mời xem trước khi quyết định. |
| Đổi cài đặt, chưa có phương án | Câu nói về lịch hiện tại, không hứa đã dời hay có lịch mới. |
| Provider trả giờ tự bịa, lời hứa kịp hạn hoặc gợi ý ngoài schema nghiệp vụ | Dùng câu dự phòng đã kiểm. |
| Mất provider hoặc nhận lý do không nhận diện | Một câu trung tính, không nhắc lại dữ liệu tự do. |
| Tiêu đề Task có lệnh giả | Nội dung tiêu đề không được gửi vào prompt. |

Chạy hồi quy bằng `pytest -q tests/test_ai_service.py tests/test_week2_storage.py` trong `backend/`. Bằng chứng ở đây đo lựa chọn và chặn sai trong code với provider giả lập; cần đo tỷ lệ provider thật chọn được câu hợp lệ, độ trễ và giọng điệu trên bản tích hợp trước nghiệm thu.

## Điều kiện mở PR phát hành v1.0

1. Ghi SHA backend, SHA APK, phiên bản Android và provider thật/giả lập vào biên bản. Chạy demo theo [kịch bản tuần 2](MT_WEEK2.md), bổ sung đầy đủ UF5–UF7 khi API và APK đã ghép.
2. Kiểm trên thiết bị các tình huống Doze, offline, giữ mic khi chuông đến, DND vốn do user bật, mở lại app khi proposal đã cũ và replay sau mất mạng. Ghi expected/observed và bằng chứng theo từng ca.
3. Đối soát chính sách buffer, quyền duyệt lịch, Calendar thật và các giới hạn ở bảng trên. Khi đã có bằng chứng, tạo nhánh release theo quy ước của nhóm, mở PR tổng và cập nhật release notes từ commit đã nghiệm thu.

**Release notes nháp cho phạm vi MT:** Khởi động phiên bằng một việc nhỏ cụ thể. Khi xem phương án đổi lịch, trợ lý giải thích ngắn gọn lý do và giữ quyền quyết định cho người dùng. Chỉ đưa các mục này vào ghi chú phát hành sau khi xác nhận chúng xuất hiện trong bản APK tích hợp.