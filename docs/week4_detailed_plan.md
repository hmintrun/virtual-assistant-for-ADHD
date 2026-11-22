# Kế hoạch tuần 4 — Ổn định tích hợp và nghiệm thu UF1–UF7 trên Android

**Thời gian dự kiến** 17–23/11/2026, giờ Việt Nam (UTC+7).  
**Mục tiêu** Bàn giao APK và Backend staging chạy xuyên UF1–UF7. Người dùng đăng nhập, lập lịch có xét giờ bận Google Calendar, tiếp tục phiên đã bắt đầu khi mất mạng, cất ghi âm, check-in cuối phiên bằng giọng nói hoặc nút chọn, rồi xem và duyệt lịch mới.  
**Tích hợp** Minh Trung điều phối nhánh tích hợp, Quốc Hưng duyệt Backend, Thanh An đóng APK và Doãn Thành xác nhận kết quả trên đúng bản bàn giao. Ngày đầu tuần, nhóm xác nhận thời gian tham gia, người nhận việc, thiết bị kiểm thử và nhánh đích.

Nguồn nghiệp vụ và hợp đồng dùng chung gồm [Project.md](../Project.md), [AGENTS.md](../AGENTS.md) và [schema Backend](backend/schema.md). Kế hoạch tiếp nối [Tuần 3](week3_detailed_plan_revised.md) và cụ thể hóa các điều chỉnh trong [bản rà soát Tuần 4](week4_audit_and_plan_revised_20261116.md).

## 1. Tổng kết tuần trước và phạm vi tuần này

| Phân hệ | Nền quan sát được khi lập kế hoạch | Phần cần tiếp nối |
| --- | --- | --- |
| Tài khoản và Google Calendar | Đã có API xác thực, OAuth Calendar và worker đồng bộ | Kiểm gia hạn phiên ProPlan, lỗi cấp quyền Calendar, lưu revision và đồng bộ lên Google sau xác nhận/duyệt |
| UF1–UF2 — Lập kế hoạch | Đã có luồng nhập việc, đề xuất chia bước và xếp lịch | Kiểm đường dùng lịch bận thật, hỏi gộp một vòng, đề xuất mặc định, chỉnh trước khi chốt và chống tạo trùng khi gửi lại |
| UF3–UF4 — Focus và Thought | Đã có Run, timer và thu âm thô. Bản rà soát ghi nhận cache, outbox và nhắc nền chưa ghép đầy đủ | Nối lịch ngày và Run đã chấp nhận với cache/outbox. Kiểm phục hồi và báo thức trên máy thật, đồng bộ Thought sau khi có mạng |
| UF5 — Check-in | Đã có API CheckIn và tạo lời nhắc. Bản rà soát ghi nhận Mobile chưa cung cấp voice hai chiều | Ghép câu hỏi phát thành tiếng, trả lời bằng giọng nói hoặc nút chọn. Kiểm năm nhánh, Hoàn tác và khôi phục DND |
| UF6–UF7 — Đổi lịch và cài đặt | Đã có proposal, change request và A20. Các điểm nghi lệch nằm ở `effective_revision`, onboarding và khôi phục đề xuất trên Mobile | Kiểm người dùng lưu cài đặt nhiều lần, Run giữ snapshot và lịch cũ giữ nguyên khi chưa duyệt. Mở lại app hiển thị đề xuất đã được kiểm theo thời gian thực |

Khi lập kế hoạch, ref local là `origin/backend` tại `e3bbd5a` và `origin/mobile` tại `18be48a`. SHA Backend `d7f6e13` của audit gốc chưa có ở máy. Số test và kết quả Live E2E trong audit là dữ liệu tham chiếu. W4-01 chọn cặp SHA ứng viên và tái hiện các lỗi còn tồn tại trước khi giao sửa.

| Phạm vi | Kết quả cần bàn giao |
| --- | --- |
| **Bắt buộc** | Ổn định phiên đăng nhập, Calendar và cài đặt. Chạy xuyên UF1–UF7 trên Android thật. Timer và lịch ngày hoạt động ngoại tuyến sau khi server chấp nhận Start. UF4 lưu ghi âm thô và phục hồi sau khi mở lại app. Nhắc nền, báo hết phiên, DND, voice UF5 và duyệt lại lịch có bằng chứng kiểm thử. Staging đạt điều kiện cấu hình trước khi mở công khai |
| **Làm khi phần bắt buộc đạt** | Ghép chỉnh bước UF2 bằng giọng nói và đọc micro-task chuẩn bị ở UF3. Chỉ nhận đầu việc có đủ thời gian triển khai và kiểm lại trước 21/11 |
| **Để sau Tuần 4** | Start Run mới ngoại tuyến, FCM, hỗ trợ Web đầy đủ, mở rộng hiểu ngôn ngữ tự nhiên UF6 và dùng AI mở rộng horizon, worker A20 định kỳ, Streak Freeze/avoidance/thống kê, dọn legacy và refactor GET diện rộng. Ghi rõ phần đặc tả còn thiếu và người giữ backlog |

**Chính sách ngoại tuyến** Giữ `offline_start_supported=false`. Run mới cần được server chấp nhận. Nếu mất phản hồi Start, Mobile đối soát Run hoặc receipt trước khi gửi lại. Với Run đã được chấp nhận, thiết bị giữ timer, báo thức, lịch ngày và ghi âm. Sự kiện đủ điều kiện được đồng bộ lại theo ID của Run và khóa chống trùng.

**Chính sách voice và AI** Ưu tiên Groq Whisper cho STT và `edge-tts` cho TTS, rồi dùng khả năng trên thiết bị. Luôn có chữ hoặc nút chọn khi voice lỗi hoặc máy thiếu gói tiếng Việt. UF4 chỉ thu và lưu raw audio trong phiên. STT chỉ chạy sau phiên, khi người dùng chọn tạo việc từ bản ghi. Python tính giờ rảnh, xung đột, thiếu hụt và lịch. LLM UF6 chỉ chọn nguyên văn câu đã được kiểm theo trạng thái, với fallback tương ứng và payload loại `task_title` thô. Giữ profile UF1 riêng với cơ chế Warm Backup của UF2/UF6.

## 2. Bảng phân công công việc từng người

Mỗi mã ở mục 3 có **một người phụ trách chính và tối đa một người cùng làm**. Ô **Cùng làm** để `—` khi một người đủ để hoàn thành. Người phụ trách chính cập nhật tiến độ và bàn giao đầu ra. Các nhóm nhận đầu ra qua mã việc phụ thuộc. Phân công theo vai trò ở `Project.md` §9.2 và kế hoạch Tuần 3.

| Thành viên | Phần chịu trách nhiệm | Đầu ra bàn giao |
| --- | --- | --- |
| **Minh Trung (`mt`)** | Khóa phạm vi, SHA và quyết định nghiệp vụ. Review AI, COGA và điều phối tích hợp | Biên bản phạm vi, bộ ca nghiệp vụ, kết quả kiểm profile/grounded copy và hồ sơ bàn giao |
| **Võ Quốc Hưng (`vqh`)** | Vòng đời phiên ProPlan, nghiệp vụ Run/CheckIn, revision và tích hợp Backend | Hợp đồng gia hạn/logout, kiểm nhận Start/End, review Backend và kết quả hồi quy nghiệp vụ |
| **Đặng Kim Cương (`kc`)** | Hợp đồng HTTP/DTO, mã lỗi, OpenAPI và fixtures cho Mobile | JSON mẫu settings, Calendar, Run/CheckIn, proposals và voice. Test xác nhận mapping API |
| **Trần Trang Tín (`tn`)** | SQLite/PostgreSQL, migration, bảo toàn dữ liệu và kiểm cấu hình staging cùng Hưng | Migration head, log suite, seed demo, kiểm receipt/outbox và phương án phục hồi dữ liệu |
| **Trần Thanh An (`tan`)** | Sửa tích hợp cài đặt/session phía Mobile, onboarding, UF6 khi mở lại app, ghép Provider và đóng APK | UI ghép API đúng hợp đồng, APK ứng viên, log analyze/test và hướng dẫn cài |
| **Trần Quang Khải (`kai`)** | Nhắc, alarm, DND/Wakelock và phục hồi native. Sau mốc native, ghép STT/TTS. Hỗ trợ kiểm Calendar thật | Log native theo máy và OS, voice UF1/UF5, kết quả kiểm cấp quyền/đồng bộ Calendar |
| **Nguyễn Quốc Thịnh (`qt`)** | Cache lịch ngày, phục hồi Run, đồng bộ thời gian, outbox và Thought Inbox | Local repository theo tài khoản, ca reconnect/restart, đối soát conflict và chống trùng |
| **Doãn Thành (`thành`)** | Chuẩn bị fixture, thiết bị và kịch bản từ ngày đầu. Kiểm mỗi mốc và nghiệm thu bản cuối | Ma trận ca theo SHA/APK, số liệu native/voice, video và danh sách lỗi còn mở |

**Điểm giao Backend–Mobile** W4-02 bàn giao hợp đồng chung cho W4-04 và W4-05. W4-06 bàn giao migration nếu vòng đời refresh cần đổi dữ liệu. W4-07 bàn giao trạng thái Run và cache cho W4-08 và W4-09. W4-14B dùng hướng dẫn đo từ W4-08 và W4-10 trên APK của W4-15.

**Điểm giao tích hợp** PR theo owner. Trung chốt cặp SHA và nhánh tích hợp ngày 17/11 theo quy trình nhóm. Hưng duyệt thay đổi Backend, An tạo APK từ bản ghép và Thành chạy smoke sau mỗi mốc.

## 3. Đầu việc và điều kiện chuyển giai đoạn

### Giai đoạn 1 — Khóa nền, hợp đồng và môi trường (17/11)

**Người chịu trách nhiệm giai đoạn** Minh Trung.

| Mã | Phụ trách chính | Cùng làm | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- | --- |
| **W4-01** | Trung | Hưng | Chọn SHA Backend/Mobile từ các bản dựng hiện có. Xác nhận lịch, người nhận việc và phạm vi staging/múi giờ. Dùng kết quả W4-03B để đối chiếu audit | Cặp SHA, danh sách lỗi theo môi trường và bước tái hiện. Mỗi lỗi có người nhận hoặc lý do chưa xác minh |
| **W4-02** | Cương | Hưng | Khóa hợp đồng HTTP cho lỗi, refresh/logout, `effective_revision`, Calendar preview revision, Run/CheckIn, proposals và voice | OpenAPI, JSON mẫu cho thành công, lỗi, retry và xung đột, cùng giới hạn xử lý voice phía Backend. W4-07 chốt replay, W4-10 dùng giới hạn Backend để chốt timeout và fallback xuyên suốt |
| **W4-03A** | Tín | — | Chuẩn bị DB test, migration và seed theo cặp SHA của W4-01 | DB dựng được, migration head và dữ liệu mẫu cho W4-03B và W4-13 |
| **W4-03B** | Thành | Khải | Chuẩn bị hai tài khoản thử, APK nền và Android thật. Chạy smoke settings, clock/alarm và Google với dữ liệu W4-03A | Log smoke, danh sách máy/quyền, cách đo âm thanh phát thực và thời điểm DND khôi phục |

**Điều kiện chuyển tiếp** Cặp SHA dựng được, QA có thiết bị và dữ liệu thử, các phần triển khai có hợp đồng rõ. Hạng mục phụ thuộc P0 có người nhận trước khi ghép.

### Giai đoạn 2 — Sửa hợp đồng và ghép ngoại tuyến/native (18–19/11)

**Người chịu trách nhiệm giai đoạn** Quốc Hưng. Thanh An phụ trách APK tích hợp đầu tiên.

| Mã | Phụ trách chính | Cùng làm | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- | --- |
| **W4-04** | An | — | Dùng W4-02 để ghép settings theo `effective_revision`, xử lý `409`, tách requested/effective và dùng revision thật khi xác nhận preview Calendar | Ba lần lưu thay đổi có hiệu lực thành công. Bản cũ nhận lỗi đúng và UI tải lại. Buffer ngoài `[1.0, 2.0]` bị chặn. Cấu hình ảnh hưởng lịch tạo proposal |
| **W4-05** | Hưng | An | Tách phiên ProPlan khỏi quyền OAuth Calendar. Hưng phụ trách Backend, An ghép gia hạn, retry và logout trên Mobile theo W4-02. Nhận migration từ W4-06 nếu có | Calendar yêu cầu cấp lại quyền mà phiên app vẫn dùng được. Nhiều request hết hạn cùng lúc chỉ gia hạn một lần. Mất mạng khi gia hạn có thể thử lại. Đổi tài khoản dùng dữ liệu đúng owner |
| **W4-06** | Tín | Hưng | Kiểm cấu hình staging cho CORS, khóa ký/mã hóa, OAuth callback, vòng đời refresh và giới hạn voice. Tín chuẩn bị migration nếu cần, Hưng xử lý phần Backend theo lỗi tái hiện | Checklist cấu hình đã che thông tin nhạy cảm và kết quả kiểm. Nếu đổi refresh lifecycle, có ca rotate/reuse/revoke/logout. Staging chỉ mở công khai sau khi đạt các kiểm soát bắt buộc |
| **W4-07** | Thịnh | Hưng | Thịnh nối cache lịch ngày và outbox theo owner. Hưng phụ trách đối soát phía server khi mất phản hồi Start, clock skew hoặc app khôi phục | Run được server chấp nhận tiếp tục khi mất mạng. Cache còn sau restart. Replay có thứ tự, dùng UTC và không nhân đôi Run/actual. Retry lỗi tạm thời, đối soát conflict và ghi lỗi vĩnh viễn để xử lý |
| **W4-08** | Khải | Thịnh | Khải ghép nhắc theo cấu hình, mặc định 15/10/5 phút, chuyển pha và báo cuối phiên. Thịnh nối trạng thái phục hồi từ W4-07 | Hủy nhắc của lịch cũ, không dồn mốc đã qua, khôi phục bản ghi alarm hợp lệ sau reboot. Đo báo hết phiên sai lệch ≤1 giây. Giữ DND khi chờ CheckIn và khôi phục đúng owner/snapshot chậm nhất `ended_at + 300s` nếu im lặng |

**Điểm kiểm cuối 19/11** Trên APK tích hợp, đăng nhập → lưu cài đặt → giới hạn quyền Calendar → bắt đầu một Run hợp lệ → mất mạng → mở lại app → kiểm lịch cache, timer và báo thức. Nếu offline hoặc native chưa ổn, chuyển toàn bộ hạng mục mở rộng sang backlog. Khải ưu tiên hoàn thành native trước khi nhận việc mở rộng voice.

### Giai đoạn 3 — Ghép Thought, voice UF5 và UF6–UF7 (20–21/11)

**Người chịu trách nhiệm giai đoạn** Thanh An điều phối bản Mobile tích hợp.

| Mã | Phụ trách chính | Cùng làm | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- | --- |
| **W4-09** | Thịnh | An | Thịnh hoàn thiện lưu Thought và replay theo W4-07. An ghép inbox local/server, nhập chữ dự phòng và Hoàn tác khi xóa | File thu ngoại tuyến còn đọc được sau restart. Metadata giữ `source_run_id`. GET/replay không làm hiện lại Thought đã xóa. Thu/lưu thử lại tối đa ba lần rồi mở nhập nhanh bằng chữ. Chuyển thành draft theo thao tác người dùng sau phiên |
| **W4-10** | Khải | An | Khải kiểm lại STT UF1 và ghép câu hỏi UF5 với TTS/STT theo W4-02. An ghép UI nhận câu trả lời qua voice hoặc nút chọn | Chốt timeout và fallback theo tổng thời gian từ thao tác đến kết quả. Có ca provider lỗi, mất mạng, thiết bị thiếu gói `vi-VN` và hủy voice. Lỗi phát/thu vẫn cho gửi CheckIn. TTS và thu âm không phát đồng thời |
| **W4-11** | An | — | Dùng W4-02 và W4-08 để ghép onboarding lưu preferences, xin quyền trong bước chuẩn bị và kiểm proposal khi mở app/quay lại foreground | Onboarding dùng trạng thái riêng và lưu được cài đặt. Banner nhẹ dẫn đến bản so sánh có các thay đổi thêm/sửa/hủy. Khi thời gian hoặc nguồn đổi, bản lên màn hình được tính lại và revision cũ bị từ chối. Lịch và Calendar chỉ cập nhật sau approve |
| **W4-12A** | Trung | Hưng | Hồi quy profile AI, ranh giới Python/LLM và grounded copy UF6 | UF1 hỏi gộp và giữ dữ kiện. UF2 đề xuất sẵn, số phút thiếu do Python tính. UF6 dùng allowlist hoặc fallback đúng trạng thái |
| **W4-12B** | Hưng | Thịnh | Kiểm Pilot A20 theo đặc tả và bàn giao bộ mẫu cho QA | A20 dùng median tỷ số actual/base của ít nhất 5 mẫu trong 15 Step sạch gần nhất, loại trùng/xung đột, làm tròn `0.05` theo `ROUND_HALF_UP`, kẹp `[1.0, 2.0]`, gợi ý từ chênh tuyệt đối `0.20` và giữ ưu tiên cấu hình thủ công |

**Điều kiện khóa tính năng ngày 21/11** APK ứng viên chạy một lượt xuyên UF1–UF7 trên staging thử nghiệm, gồm CheckIn voice, phục hồi offline và đề xuất UF6 khi mở lại app. Từ mốc này nhóm tập trung sửa lỗi, chạy lại và đóng gói. Nếu còn tiêu chí bắt buộc chưa đạt, ghi rõ giới hạn demo hoặc dời mốc phát hành.

### Giai đoạn 4 — Kiểm hồi quy và bàn giao (22–23/11)

**Người chịu trách nhiệm giai đoạn** Doãn Thành.

| Mã | Phụ trách chính | Cùng làm | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- | --- |
| **W4-13** | Tín | — | Chạy suite Backend trên SQLite và PostgreSQL test đã migrate. Kiểm nâng cấp DB mới/cũ, restart/retry và Calendar worker | Log lệnh, DB thực tế, SHA, migration head và kết quả `pass`/`fail`/`skip`/`xfail`. PostgreSQL test tách dữ liệu thật |
| **W4-14A** | Thành | — | Dùng APK W4-15 để kiểm xuyên UF1–UF7, Google thật, offline/reconnect và các ca nghiệp vụ ở mục 4 | Video toàn trình, kết quả từng ca theo APK/SHA và người nhận lỗi còn lại |
| **W4-14B** | Thành | Khải | Nghiệm thu alarm/Doze/reboot, DND và voice trên cùng APK W4-15. Khải cung cấp log thiết bị và kết quả đo | Ma trận máy/OS/quyền/mạng, số lần thử, sai lệch alarm, thời điểm DND khôi phục và độ trễ voice |
| **W4-15** | An | — | Chạy `dart analyze`, `flutter test`, đóng APK và thử cài lại trên thiết bị. Giao APK trước khi W4-14A và W4-14B chạy nghiệm thu | APK có SHA-256, SHA nguồn, log analyze/test, hướng dẫn cài và môi trường Backend đi kèm. APK mới được kiểm lại khi đổi commit |
| **W4-16** | Trung | Thành | Trung tập hợp bản giao. Thành cài theo hướng dẫn của W4-15 và đối chiếu báo cáo W4-13, W4-14A và W4-14B | Biên bản ghi rõ khả năng trên bản cuối, lỗi còn mở, mốc xử lý và giới hạn demo nếu có |

### Cách theo dõi và chốt từng giai đoạn

Cuối ngày, người phụ trách chính cập nhật mã việc, trạng thái, PR/SHA, bằng chứng và mã việc phụ thuộc đang chờ. Dùng các trạng thái **Chưa bắt đầu / Đang làm / Bị chặn / Chờ nghiệm thu / Đã đạt / Dời**. QA bắt đầu từ ngày 17/11. Khi phát hiện lỗi, Thành gửi bước tái hiện và APK/SHA về mã triển khai tương ứng. Người phụ trách giai đoạn tập hợp kết quả, Thành xác nhận điều kiện chuyển tiếp và Trung ghi việc dời kèm người nhận.

## 4. Tiêu chí nghiệm thu chung và gói bàn giao

| Nhóm kiểm | Điều kiện ghi đạt | Bằng chứng |
| --- | --- | --- |
| **Tài khoản, settings và staging** | Google Sign-In trên APK cài mới. Refresh xử lý đồng thời, hết hạn và bị thu hồi theo hợp đồng. Calendar `401` giữ phiên ProPlan. Lưu settings liên tiếp, phân biệt revision cũ/pending/effective. Staging công khai đạt kiểm cấu hình | W4-04–06, log APK với hai tài khoản thử, test API và xác nhận môi trường |
| **UF1–UF2 và Calendar** | UF1 dùng lịch bận thật hoặc cho nhập giờ rảnh thủ công khi chưa cấp quyền. Kiểm đủ bốn trường, gộp câu hỏi về dữ kiện thiếu hoặc mâu thuẫn trong một lượt và lưu nháp nếu người dùng thoát. UF2 có đề xuất mặc định, micro-task 2 phút, rolling horizon và số phút thiếu do Python tính. Xác nhận/duyệt mới ghi Calendar. Retry chống trùng và lỗi xung đột 412 được ghi nhận | W4-12A, W4-13, W4-14A, fixture lịch bận, kiểm DB/receipt, worker log và đối chiếu Calendar thật |
| **UF3–UF4 ngoại tuyến** | Run được server nhận vẫn chạy khi tắt mạng. Lịch ngày và file thu âm còn sau restart. UF4 giữ timer chạy, lưu raw audio và không gọi STT/LLM khi đang Focus. Reconnect đồng bộ đúng owner, xử lý Undo và không nhân đôi dữ liệu | W4-07–09, API trace, kiểm file sau restart, local DB và receipt |
| **Android và UF5** | Nhắc đúng cấu hình. Kiểm alarm khi mở app, chạy nền, khóa màn hình, Doze và sau reboot. Focus giữ 1-Step UI, các màn hình có tối đa 3–5 tương tác chính. Hết giờ vào CheckIn, không tự hoàn thành Session/Step/Task. Đủ năm nhánh, ba chip Skip và Undo 5 giây. DND chỉ khôi phục phần app áp theo snapshot. Ca làm tiếp tuân thủ policy handoff và gap của lịch | W4-08, W4-14A, W4-14B, log thời điểm dự kiến/thực phát, trạng thái DND và kết quả trên từng thiết bị/quyền |
| **Voice UF1/UF5** | Groq Whisper và `edge-tts` hoạt động trên staging. Mất mạng hoặc lỗi provider chuyển sang khả năng trên thiết bị, chữ hoặc nút chọn. Lựa chọn người dùng được giữ khi voice lỗi | W4-10, W4-14B, p50/p95, tỷ lệ lỗi, số mẫu, độ dài audio và điều kiện mạng |
| **UF6–UF7 và A20** | Đóng app giữ lịch cũ. Mở lại kiểm thời gian, nguồn và revisions. Duyệt hợp lệ chỉ thay Task được phép, không sửa Run đang chạy. Cài đặt hiệu lực đúng phiên và cấu hình ảnh hưởng lịch đi qua UF6. A20 đúng mẫu/ngưỡng và chỉ áp dụng khi người dùng chọn | W4-11, W4-12B, W4-14A, snapshot lịch/Run trước–sau, test stale/approve retry và ca ngưỡng A20 |
| **Bản ứng viên** | Suite Backend đúng DB, analyze/test Mobile và smoke trên APK cuối. Có kết quả từng ca và lý do phần bị chặn, chưa chạy hoặc đã dời | W4-13, W4-14A, W4-14B, W4-15, W4-16, SHA nhật ký, migration head, hash APK và báo cáo QA |

**Kịch bản video chính** Đăng nhập Google → liên kết Calendar → tạo việc và chốt kế hoạch tránh khung bận thật → bắt đầu Run → ngắt mạng và cất ghi âm → kết nối lại → nghe câu hỏi UF5 và báo chưa xong → đóng/mở app → xem và duyệt đề xuất đã tính lại → đổi cài đặt cho phiên sau. Quay riêng ca Calendar mất quyền, Skip/Undo, Doze/reboot và im lặng 5 phút để đối chiếu log.

**Gói bàn giao ngày 23/11** Gồm APK và SHA-256, SHA Backend/Mobile, migration head, hướng dẫn cài/chạy và dữ liệu demo, OpenAPI/JSON mẫu, báo cáo QA theo thiết bị/môi trường, video UF1–UF7 và danh sách việc còn mở. Thành xác nhận kết quả trên đúng APK và Backend được giao.
