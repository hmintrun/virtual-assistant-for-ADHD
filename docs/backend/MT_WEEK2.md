# Phần việc MT tuần 2 — 07/11/2026

## Đã làm

- Chốt CheckIn với MT: tuần 2 chỉ có **Nghỉ ngay** và **Báo bận**; xác nhận hoàn thành phiên/bước/việc để tuần 3.
- Sửa ba micro-task dự phòng trong `ai_service.py`: mỗi gợi ý chỉ còn một hành động nhỏ; giữ ngữ cảnh, scope và thời lượng user cấu hình.
- Bổ sung kiểm thử nguồn Thought đang chạy/tạm dừng, sai owner hoặc không tồn tại: bị chặn trước khi lấy dịch vụ STT hoặc gọi LLM.
- Đồng bộ `Project.md` và `AGENTS.md`: giữ Gemini Transcribe hiện có theo quyết định MT, kiểm provider thật sau. UF4 trong phiên vẫn chỉ lưu audio thô, không STT/LLM, kể cả fallback trên thiết bị.
- Chuẩn bị nội dung UI, contract và các ca nghiệm thu dưới đây. Chưa ghép Mobile hoặc triển khai API UF3–UF4.

## Contract bàn giao

| Sự kiện | Run / CheckIn / kết quả | DND/Focus |
| --- | --- | --- |
| Hết giờ, chưa phản hồi | `awaiting_checkin` / `open` / `unknown` | Giữ trong lúc chờ; hạn khôi phục `ended_at+300s` |
| Nghỉ ngay | `closed` / `answered` / `unknown` | Khôi phục ngay |
| Báo bận | Dừng Run rồi `closed` / `answered` / `busy` | Khôi phục ngay; không tự dời lịch |
| Timeout 5 phút | `closed` / `no_respond` / `unknown` | Thiết bị tự khôi phục kể cả offline |
| Trả lời muộn | Sửa cùng CheckIn, giữ Run `closed` | Không tác động Run mới |

Không tự hoàn thành Session/Step/Task. `answered` không có nghĩa hoàn thành; `step_completed`, `task_completed`, `skip_scope` đều null ở tuần 2. `null` là chưa xác nhận, khác `false` là đã xác nhận chưa xong. Các token và response dưới đây là contract cần triển khai, chưa phải API đã hoạt động.

- `POST /runs/{run_id}/end` dưới `/api/v1`: nhận `device_id`, `expected_run_revision`, `event_id`, `ended_at`, `end_reason`, `work_elapsed_seconds`, `rest_elapsed_seconds`; `end_reason=timer_elapsed` hoặc `user_stopped`. Trả Run cùng CheckIn cuối, ID/revision; CheckIn mới có revision 0, chỉ một bản cuối/Run.
- `PUT /check-ins/{checkin_id}/response`: nhận payload đầy đủ bên dưới; Báo bận đổi `session_outcome` thành `busy`. Thành công trả HTTP 200, `{data: {check_in, run}, meta}`, gồm trạng thái và revision hiện hành của cả hai tài nguyên. Tuần 2 từ chối outcome khác unknown/busy hoặc Step/Task/Skip khác null bằng 422.

```json
{
  "expected_response_revision": 0,
  "responded_at": "2026-11-07T10:00:10Z",
  "session_outcome": "unknown",
  "step_completed": null,
  "task_completed": null,
  "skip_scope": null,
  "remaining_note": null
}
```

Cả hai lệnh dùng `Idempotency-Key` riêng, commit dữ liệu và receipt nguyên tử; replay kiểm trước revision. Cùng khóa khác operation/tài nguyên/payload trả 409. Revision cũ trả 409 kèm revision hiện hành; outbox giữ ý định, đọc lại rồi dùng khóa mới nếu chỉ xung đột do timeout, không ghi đè phản hồi user khác.

Timeout áp dụng một lần khi CheckIn còn open, tăng revision CheckIn/Run; response thay đổi dữ liệu cũng tăng revision tương ứng. Nếu timeout dẫn tới 409, phải lưu timeout trước khi trả revision mới. Đọc Run/start/end phải đối soát quá hạn nhưng vẫn giữ mọi kiểm tra start. User lấy từ xác thực; sai owner/không tồn tại trả 404, chưa xác thực 401, Run chưa dừng 409.

`ended_at` là lúc thực sự dừng, không phải lúc đồng bộ; actual chỉ tính work có nguồn hợp lệ, thiếu thì null. Không cộng nghỉ, micro-task hoặc chờ CheckIn. Response không sửa actual/ended_at; `responded_at` có timezone, nằm từ ended_at tới lúc server nhận. `dnd_release_at` chỉ được sớm hơn hoặc giữ nguyên, không lùi hạn.

Khi bấm nghỉ/bận giữa Run, thiết bị dừng timer và khôi phục tại chỗ, lưu bền **end trước → response sau**; end đến server trễ không mở lại 5 phút. Khôi phục đúng owner/snapshot, không tắt DND vốn user bật; response thành công không chứng minh thiết bị đã khôi phục. Báo kết quả chi tiết sau dùng cùng CheckIn ở tuần 3.

## Nội dung UI

| Ngữ cảnh | Nội dung / thao tác |
| --- | --- |
| Trước phiên | “Khởi động nhẹ trong 2 phút: {micro_task}”; **Bắt đầu đếm giờ {X}p**. Không timer/nút xác nhận micro-task riêng |
| Trong phiên | Hiện Step hiện tại và deadline; **Cất suy nghĩ** — “Giữ để nói, thả để cất. Đồng hồ vẫn chạy.” |
| File local đã lưu | “Đã cất ghi âm trên thiết bị.” — **Hoàn tác** trong 5 giây; chỉ báo sau khi lưu thành công |
| Lỗi thu/lưu | Lượt 1–2 cho ghi lại/nhập chữ; sau tổng 3 lượt mở text fallback. Mất mạng không tăng số lượt |
| Hết giờ | “Phiên đã kết thúc. Bạn có thể nghỉ ngay.” — **Nghỉ ngay**, **Báo bận** |
| Đã nghỉ | “Kết quả công việc chưa được xác nhận.” Không hỏi kết quả bắt buộc |
| Sau phiên | **Nghe ghi âm**, **Tạo việc từ ghi âm này**; chỉ khi user chọn mới STT rồi tạo Draft, chưa tạo Task |

Micro-task mẫu: “Gõ một tiêu đề vào file báo cáo”, “Đặt tiêu đề cho slide đầu tiên”, “Mở file chứa lỗi đã ghi trong bước này”, “Cất một món đồ không dùng trên bàn”. Không bịa công cụ/tên file, đổi scope hoặc ghi đè gợi ý user đã sửa. Tối đa 3–5 thao tác chính/màn, Focus chỉ một Step; Undo thay dialog xác nhận.

## Kiểm chứng và việc còn lại

**61 test, 2 subtest đạt** trong bộ AI service, receipt qua restart, planning API và week2 storage; Ruff/format/ty đạt. Đây là hồi quy backend liên quan, chưa kiểm provider thật hoặc Android.

Các ca phải nghiệm thu khi có API/APK: hết giờ; nghỉ; bận; timeout; race timeout/response; retry/restart; offline end→response; đồng bộ sau 5 phút; phản hồi cũ khi có Run mới; start sau timeout; null/false/outcome; owner/idempotency; DND vốn user bật. Thử thêm giữ mic lúc alarm đến: chuông ≤1 giây, file nghe được, timer/actual không bị kéo dài; Undo không vừa hủy vừa gửi; STT xong nhưng tạo Draft lỗi phải giữ transcript để thử lại.

**Còn chờ:** Hưng/Cương triển khai service/router UF3–UF4/CheckIn và guard capture/file finalize trước provider; Tín kiểm transaction/receipt; An/Thịnh/Khải ghép UI, recorder, outbox và DND; Thành cùng MT kiểm demo thật. Các file Focus/Thought/CheckIn và Mobile tương ứng còn là khung, nên chưa nghiệm thu toàn luồng. Bằng chứng chặn AI hiện chỉ bao phủ các entry point Thought có nguồn được kiểm, không khẳng định toàn app không gọi AI trong phiên.

Demo: ghi SHA backend/APK và thiết bị → tạo lịch thật → start online → tắt mạng, cất/Undo suy nghĩ → hết giờ/nghỉ/bận/timeout → sau phiên chủ động chuyển ghi âm thành Draft → reconnect/restart/replay. Ghi expected/observed và log/video từng ca, không lưu khóa/audio/transcript thử nghiệm vào Git. MT tiếp tục review PR và tổng hợp kết quả khi có bản ghép. **Phần MT nằm trên nhánh riêng `codex/mt-week2-progress`, chưa merge vào backend/dev.**
