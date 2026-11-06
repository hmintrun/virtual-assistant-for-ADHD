# Checklist kỹ thuật và bằng chứng tuần 2

Đi kèm [kế hoạch tuần 2](week2_detailed_plan.md), theo [Project.md](../Project.md) và [schema Backend](backend/schema.md). Đây là đầu việc cần triển khai/kiểm chứng, không phải báo cáo đã đạt. Các ghi nhận code bên dưới đã được đối chiếu trong lượt tích hợp nền; xem commit và kết quả ở [biên bản nền tuần 2](week2_baseline.md). Mobile chưa được build/analyze trên máy MT theo lựa chọn chốt Backend, Mobile chờ kiểm. Mỗi mục triển khai mới vẫn phải kiểm lại trên commit nhóm đang dùng.

## CheckIn tối thiểu và timeout — giai đoạn 2

**Hưng phụ trách service; Cương phụ trách router; Tín phụ trách lưu trữ; An phụ trách UI, Thịnh hỗ trợ outbox; Khải phụ trách DND trên thiết bị.**

- End Run tạo/lấy duy nhất CheckIn cuối và trả `checkin_id` cùng revision theo contract để Mobile gửi phản hồi. Schema đã có `PUT /api/v1/check-ins/{checkin_id}/response`; Cương triển khai/đăng ký router trong `backend/app/api/v1/checkin.py`.
- Trung và Hưng xác định payload Chọn nghỉ/Báo bận với Cương/An trước khi ghép: nghỉ là quyết định dừng, không tự suy `session_outcome=completed`, `step_completed=true` hoặc `task_completed=true`. Nếu contract chưa biểu diễn được nghỉ mà chưa xác nhận kết quả, cập nhật contract chung rõ ràng trước triển khai; không gán kết quả giả để vượt validation.
- Service xử lý phản hồi hợp lệ: rời `awaiting_checkin`, cập nhật CheckIn và mốc DND theo nghiệp vụ. Giữ `ended_at`/actual, không tự dời lịch. Chưa mở rộng Voice Check-in, 3-chip Skip hoặc UF6 trong tuần này.
- `uq_user_live_run` trong `backend/app/models/execution.py` bao gồm `awaiting_checkin`. Khi đọc trạng thái liên quan hoặc start mới, service đối soát Run đã quá `ended_at+300`: cập nhật `no_respond`, giữ outcome chưa xác nhận là `unknown` và chuyển Run ra khỏi tập trạng thái live theo contract. Xử lý nguyên tử cùng kiểm start để hai request không tạo hai Run.
- Lazy timeout là đối soát Backend khi có request, không bảo đảm một tác vụ chạy đúng giây thứ 300. Thiết bị phải tự khôi phục DND đúng owner ở hạn đó dù offline hoặc không có request Backend; `dnd_release_at` không phải bằng chứng thiết bị đã khôi phục.
- User chọn nghỉ/báo bận thì khôi phục DND cục bộ ngay đúng owner; gửi API hoặc lưu bền outbox nếu offline. Khi reconnect, đồng bộ end trước phản hồi nếu cần lấy ID/revision; không tạo CheckIn trùng.
- Trả lời muộn cập nhật cùng CheckIn; kiểm revision/race với timeout, không kéo dài actual, không tác động DND của Run mới. Dọn Run cũ không bỏ kiểm thời gian, gap, thiết bị và quyền của lần start tiếp theo.

**Bằng chứng:** phản hồi online/offline; hai request start đồng thời sau timeout; đọc lại Run quá hạn; retry phản hồi; trả lời muộn; DND tự khôi phục khi không có request. Cương ghi kết quả HTTP, Hưng/Tín ghi kiểm thử trạng thái/transaction, Thành đối chiếu trên APK.

## Audio Thought → Draft và lưu bền — giai đoạn 1, 3

**Cương chịu trách nhiệm ghép chuỗi API; Hưng phụ trách service, Tín phụ trách quan hệ dữ liệu, An phụ trách luồng Mobile.**

1. Sau khi phiên kết thúc và user chọn chuyển ghi âm, bảo đảm file đã finalize và metadata Thought đã được đồng bộ lên server, còn thuộc user. Nếu offline thì giữ ý định để xử lý khi có mạng; không gọi STT ngay trong phiên.
2. Gọi `POST /api/v1/voice/transcriptions` với `purpose=thought_to_draft`, `thought_id`, `device_id`, file và khóa idempotency theo schema. Backend kiểm quyền và điều kiện phiên nguồn trước provider, kiểm lại điều kiện cần thiết trước lưu kết quả.
3. Giữ transcript và trạng thái lệnh để phục hồi; gọi `POST /api/v1/thoughts/{thought_id}/draft` với `expected_thought_revision`, `text_content` và khóa idempotency riêng cho bước tạo Draft. Retry cùng bước dùng lại khóa/payload cũ; không dùng một khóa cho hai lệnh khác nhau.
4. Nếu bước tạo Draft lỗi, giữ transcript để xử lý lỗi/retry phù hợp mà không bắt buộc gọi STT lại. Không tự xác nhận Task hoặc ghi Calendar.

- Tín kiểm `PlanningDraft.source_thought_id` trong `backend/app/models/planning.py`: model đã có FK và `unique=True`. Xác minh migration/DB thực tế và kiểm gửi đồng thời; không tạo thêm ràng buộc trùng chỉ vì feedback gọi là thiếu.
- Tín xác định cách trả `linked_draft_id` từ quan hệ Thought–Draft và `converted_task_id` chỉ khi confirm UF2 thành công. Ưu tiên quan hệ đã có; nếu cần cột mới phải có lý do, migration và kiểm nhất quán, không duy trì hai nguồn liên kết dễ lệch.
- `backend/app/api/v1/voice.py` đã replay/record receipt DB, đồng thời còn `_VOICE_IDEMPOTENCY_STORE`. Hưng chịu trách nhiệm cơ chế idempotency trong service, Cương sửa wiring/router tương ứng: bỏ dict RAM trùng sau khi kiểm đường receipt bền; Tín hỗ trợ transaction. Không xây lại CommandReceipt đã có.
- Receipt bảo vệ kết quả nghiệp vụ khỏi áp dụng hai lần; sự cố trước khi lưu receipt vẫn có thể dẫn tới gọi lại provider. Không hứa STT chỉ được gọi một lần tuyệt đối.

**Bằng chứng:** Thought chưa đồng bộ/sai owner/phiên chưa kết thúc bị chặn; STT thành công nhưng tạo Draft lỗi; restart giữa hai bước; retry/đồng thời không tạo nhiều Draft; chuyển thành Task sau confirm UF2 trả liên kết đúng.

## Reminder offsets — chuẩn bị ở giai đoạn 1, ghép thật ở giai đoạn 2

**Cương chịu trách nhiệm đầu ra API/contract; Hưng làm logic service, Tín hỗ trợ default DB, Thịnh/Khải/An ghép phần thiết bị.**

| Điểm rà soát | Việc cần làm |
| --- | --- |
| `backend/app/schemas/settings.py` | Mở `UserPreference.reminder_offsets` từ `max_length=1` thành 3; kiểm cả DTO đầu vào/đầu ra: 1–3 integer giây không âm, không trùng, thứ tự giảm dần |
| `backend/app/api/v1/settings.py` | Sửa validation còn `len(offsets) != 1`; đưa quy tắc vào tầng phù hợp, tránh input và output chấp nhận khác nhau |
| `backend/app/models/user.py`, `backend/app/services/draft_service.py` | Cập nhật default khởi tạo `[900, 600, 300]`; giữ cấu hình user đã lưu, kiểm các đường tạo preference khác nếu có |
| `backend/app/api/v1/system.py`, `backend/app/schemas/common.py` | Capabilities đang dùng số đơn `default_reminder_offset_seconds`; xác định cách biểu diễn nhiều mốc và cập nhật DTO/contract/Mobile cùng nhau, không đổi giá trị thành mảng trong trường vẫn khai báo integer |
| `backend/app/services/plan_service.py` | `dedupe_key` hiện đã chứa offset. Kiểm tạo nhiều reminder không đè/trùng và hủy theo revision/start; không sửa lại chỉ để thêm offset đã có |
| Schema/OpenAPI/JSON mẫu và Mobile | Cập nhật nguồn chung, kiểm round-trip cấu hình, không phát dồn các mốc đã qua; báo cáo hành vi thực tế khi Doze |

**Bằng chứng:** 1/2/3 mốc hợp lệ; mảng rỗng/quá 3/trùng/âm/sai kiểu bị xử lý đúng; user cũ giữ cấu hình; lịch thay đổi/start không còn nhắc thừa; mẫu phát nhắc khi Doze được ghi riêng với chuông kết thúc.

## Chuông khi thu âm — thử ngày 04/11, nghiệm thu giai đoạn 3–4

**Khải chịu trách nhiệm chính; An hỗ trợ UI và Thành ghi bằng chứng.**

- Thử giữ mic đúng lúc timer hết: đo âm thanh thực sự phát, kiểm file sau finalize, trạng thái recorder/audio focus và tác động của loa lên bản thu. Không suy cấu hình audio focus đơn lẻ bảo đảm mọi thiết bị đều thu/phát tốt.
- Dùng giới hạn `audio_max_seconds`/`audio_max_bytes` theo capabilities và policy đã xác định. Nếu chưa có giới hạn hữu hiệu, ghi rõ để chốt, không tự gán số trong test rồi coi là contract.
- Giữ dung sai chuông ≤1 giây; chime dịu có âm thanh đúng hạn có thể kiểm theo tiêu chí này. Rung/UI chỉ là bổ trợ, không thay chuông để ghi Pass. Nếu thiết bị không đáp ứng, ghi lỗi/giới hạn và xử lý; không hoãn chuông tới khi nhả mic.
- Không lùi `ended_at`, actual hoặc `ended_at+300` vì chờ finalize/check-in. File không hỏng, không gọi STT/LLM trong phiên.

## Nền tích hợp và demo offline

- **Trung chịu trách nhiệm commit nền đầu giai đoạn 1:** cùng Hưng đối chiếu trạng thái `dev`, nhánh Backend và thay đổi local; chỉ review/ghép phần còn thiếu, kiểm chạy và ghi SHA. Không merge mù toàn bộ nhánh hoặc kéo thay đổi local chưa được review vào nền chung.
- **An chịu trách nhiệm demo offline ngày 06/11:** Khải/Thịnh hỗ trợ recorder/timer/local, Thành ghi bằng chứng trên APK/commit xác định. Bắt đầu Run thật khi có mạng → tắt mạng → cất audio → timer vẫn chạy → nghe lại được file. Cương theo dõi chỗ nối server và chịu trách nhiệm toàn giai đoạn 3 đến 07/11.

## Quy trình đo chuông/Doze — giai đoạn 4

**Thành chịu trách nhiệm báo cáo; Khải vận hành/đối soát native, Thịnh hỗ trợ mốc thời gian.**

1. Ghi model/OS, APK/commit, quyền, tối ưu pin, mạng và cấu hình phiên. Phân nhóm foreground, screen off/background, Doze cưỡng bức và phiên rút cáp thực tế; ghi số mẫu từng nhóm, tổng ít nhất 30 trên mỗi máy thuộc ma trận.
2. Với mẫu cưỡng bức, cài/chạy app, đặt phiên rồi dùng các lệnh dưới trong môi trường thử. Xem `dumpsys deviceidle` để xác nhận trạng thái deep idle trước mốc chuông; ghi lại trạng thái, không chỉ ghi đã chạy lệnh.

```text
adb shell dumpsys battery unplug
adb shell dumpsys deviceidle force-idle
adb shell dumpsys deviceidle
```

3. Ghi thời điểm dự kiến, callback và bằng chứng âm thanh thực sự phát bằng phương pháp đo có độ phân giải đủ kiểm ≤1 giây, ví dụ bản ghi ngoài với mốc thời gian đối chiếu được. Log lệnh gọi phát âm thanh chưa chứng minh loa đã phát; mẫu chỉ có callback phải ghi giới hạn phép đo.
4. Kiểm ít nhất một phiên đủ cấu hình với USB rút hoàn toàn, mục tiêu thêm một phiên đối chiếu. Ghi trạng thái thực tế; rút cáp không tự chứng minh máy vào Doze. Mẫu ngắn/cưỡng bức không thay toàn bộ phiên thực tế.
5. Không dồn 30 alarm sát nhau rồi coi mọi mẫu có cùng điều kiện: xét giới hạn tần suất alarm đang dùng, trạng thái idle và tác động lần phát trước. Ghi mọi lần trễ/không phát, không loại mẫu thất bại khỏi thống kê.
6. Kết thúc thử phải hoàn nguyên, kiểm app phục hồi và trạng thái pin trở lại bình thường:

```text
adb shell dumpsys deviceidle unforce
adb shell dumpsys battery reset
```

Báo cáo phân biệt Doze cưỡng bức với điều kiện sử dụng thực tế; không suy độ tin cậy 99,9% hoặc hỗ trợ mọi Android từ 30 mẫu. Tham khảo [quy trình thử Doze của Android](https://developer.android.com/training/monitoring-device-state/doze-standby) và [quản lý audio focus](https://developer.android.com/media/optimize/audio-focus).
