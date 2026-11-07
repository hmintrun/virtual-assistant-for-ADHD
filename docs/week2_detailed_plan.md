# Kế hoạch tuần 2 — Hoàn thiện UF3–UF4 và hình thành MVP Android

**Thời gian:** 03–09/11/2026, giờ Việt Nam (UTC+7).  
**Mục tiêu:** Có một bản APK chạy trên điện thoại Android thật, đi được từ lịch đã xác nhận đến phiên tập trung, cất suy nghĩ bằng ghi âm và xem lại sau phiên.

Kế hoạch tập trung vào nội dung mỗi người cần làm, output phải bàn giao và kết quả chung khi ghép tất cả phần việc. Quy tắc nghiệp vụ theo [Project.md](../Project.md), [AGENTS.md](../AGENTS.md) và [schema Backend](backend/schema.md). Vị trí code, điều kiện tích hợp và quy trình đo nằm trong [checklist kỹ thuật tuần 2](week2_technical_checklist.md); các mục trong checklist là việc cần kiểm, chưa phải kết quả đã đạt.

## 1. Tổng kết tuần trước

Phần tổng kết ghi nhận công việc tuần 1 đã thực hiện đến ngày 03/11. Các phần chưa hoàn tất được tiếp nối trong tuần này để phục vụ MVP.

| Phân hệ | Kết quả đã có | Phần cần tiếp nối |
| --- | --- | --- |
| Backend và Database | Có nền FastAPI, DTO, ORM/migrations; các model Task, Step, Session, SessionRun, CheckIn, Thought và receipt | Hoàn thiện API/service thực thi UF3–UF4, lưu bền và kiểm tra dữ liệu khi gửi lại |
| UF1 — Nhập và xác nhận việc | Có lưu/mở/sửa nháp, trích xuất và hỏi làm rõ; nhận giờ rảnh riêng trong Summary, xác nhận đầu vào chưa tạo Task | Kiểm toàn luồng Mobile–Backend theo contract chung bằng dữ liệu thật; hoàn thiện phần xử lý còn thiếu nếu có |
| UF2 — Chia việc và chốt lịch | Có scheduler, kiểm khả thi, buffer, segments, revisions và lưu kế hoạch với nguồn giờ cũ | Nối giờ riêng từ Draft sang nguồn tính lịch để nhập việc mới có thể đi tiếp đến lịch chính thức |
| AI và giọng nói | Có xử lý trích xuất/STT; đã sửa đường gọi fallback Python UF2 và bổ sung test timeout/lỗi kết nối | Đưa bản sửa vào nền tích hợp; kiểm chuyển ghi âm thành nháp sau phiên và bảo đảm không gọi AI/STT trong UF4 |
| Mobile UF1–UF2 | Nhánh dev có giao diện nhập việc, review tóm tắt/phương án, lịch và adapters gọi API | Kế thừa UI đã có, thêm màn hình Focus/Hộp suy nghĩ và nối API thực thi |
| UF3–UF4 | Có khung file và một phần model dữ liệu | Xây phần chạy thật: timer, alarm, DND, ghi âm, cache, đồng bộ và phục hồi |

**Điểm xuất phát:** `dev` là nhánh tích hợp chung của Mobile và Backend; API contract và JSON mẫu đã được dùng chung. Tuần 2 kế thừa nền này, triển khai phần UF3–UF4 còn thiếu và cập nhật contract chung khi tính năng mới cần bổ sung. Đầu giai đoạn 1, Trung cùng Hưng đối chiếu `dev`, nhánh Backend và thay đổi local, chỉ review/tích hợp phần còn thiếu rồi ghi commit nền để nhóm rẽ nhánh. Bản sửa fallback và tài liệu local không mặc định đã lên remote; không merge lại thay đổi đã có hoặc gom phần chưa được review.

**Bằng chứng ban đầu:** 33 test cùng 2 subtest AI/DTO/scheduler đạt khi sửa fallback riêng. Kết quả kiểm lại toàn bộ Backend sau tích hợp và commit nền được ghi trong [biên bản nền tuần 2](week2_baseline.md). Mobile trên máy MT chưa build/analyze vì chưa có Flutter SDK; theo lựa chọn người dùng, chốt nền Backend và chờ người có SDK kiểm Mobile. Đây chưa phải nghiệm thu giai đoạn 1 hoặc Android.

## 2. Bảng phân công công việc từng người

Mỗi công việc có đúng một người chịu trách nhiệm chính đến khi đầu ra được ghép và kiểm tra đạt. Người này chủ động tổ chức phối hợp, yêu cầu phần hỗ trợ, tổng hợp kết quả, xử lý hoặc báo vướng mắc và bàn giao. Người hỗ trợ chịu trách nhiệm phần được giao; kết quả chung của công việc vẫn do người phụ trách chính theo dõi đến cùng.

| Thành viên | Nội dung công việc cần làm | Output phải bàn giao |
| --- | --- | --- |
| **Minh Trung (mt)** — BA, Prompt và Git | Điều phối review và ghép phần việc tuần 2 vào nhánh chung `dev`; rà nghiệp vụ nguồn giờ rảnh, mốc nhắc, trạng thái Run/Thought, kết thúc và Undo dựa trên contract đang dùng chung.<br><br>Rà microcopy và gợi ý micro-task đúng ngữ cảnh; kiểm việc bảo vệ dữ liệu user khi AI đề xuất khác.<br><br>Phối hợp kiểm mọi đường ghi âm/retry/đồng bộ để không gọi STT/LLM trong phiên; chỉ chuyển ghi âm sau phiên khi user chọn.<br><br>Review nghiệp vụ, điều phối ghép code; chuẩn bị hướng dẫn demo, tổng hợp kết quả và phần còn lại cho tuần 3. | Các thay đổi tuần 2 được tích hợp trên `dev`; quyết định nghiệp vụ và phần bổ sung contract/JSON mẫu được cập nhật vào tài liệu chung cùng người phụ trách Backend/Mobile.<br><br>Microcopy/gợi ý khởi động dùng được trên UI; bằng chứng không có STT/LLM trong UF4.<br><br>Bản nguồn tích hợp xác định được commit, hướng dẫn cài/chạy/demo, ghi chú giới hạn và danh sách tồn đọng có người nhận. |
| **Quốc Hưng (vqh)** — Backend Lead và service nghiệp vụ | Hoàn thiện nguồn giờ rảnh UF1 → UF2 và lưu kế hoạch chính thức.<br><br>Chịu trách nhiệm service UF3: start/active/detail/events/end, owner/thiết bị/revision, một live Run/user, device/reminder và chống trùng.<br><br>Chịu trách nhiệm service UF4: vòng đời Thought, điều kiện xóa/chuyển nháp, kiểm quyền trước STT và chống tạo trùng Draft; phối hợp Khải/Trung về pipeline giọng nói.<br><br>Làm CheckIn cuối phiên tối thiểu và lazy timeout khi đọc/start sau hạn, giữ ended_at/actual đúng; không suy hoàn thành từ nút Chọn nghỉ; review contract và bàn giao interface service cho Cương, dùng repository của Tín. | Service UF3–UF4 có kiểm thử nghiệp vụ, lỗi nghiệp vụ rõ để router ánh xạ.<br><br>Luồng kế hoạch/Run/Thought không tự complete, dời lịch hoặc gọi STT trong phiên; gửi lại không tạo trùng.<br><br>Service được Cương ghép vào API thật, hướng dẫn chạy Backend và kết quả hồi quy tương ứng. |
| **Kim Cương (kc)** — API router UF3–UF4 | Chịu trách nhiệm router UF3: start/active/detail/events/end của Run và API thiết bị/lời nhắc; router UF4: lưu/đọc/xóa metadata Thought và chuyển Thought thành Draft.<br><br>Triển khai thêm endpoint phản hồi CheckIn tối thiểu phục vụ kết thúc UF3; đăng ký router vào ứng dụng; hoàn thiện request/response DTO theo contract chung, validation đầu vào, dependency xác thực/lấy user, nhận revision/khóa idempotency và ánh xạ lỗi HTTP.<br><br>Gọi service của Hưng, không viết lại state machine hoặc truy vấn DB trực tiếp trong router. Phối hợp mở rộng reminder offsets 1–3 mốc; cập nhật OpenAPI/JSON mẫu và ghép API với An/Thịnh.<br><br>Kiểm thử endpoint qua HTTP: thành công, payload sai, thiếu quyền, revision cũ, retry; điều phối ghép toàn luồng UF4. | Router UF3–UF4 được đăng ký, gọi được qua ứng dụng thật và trả đúng DTO/status/error.<br><br>OpenAPI/JSON mẫu dùng chung với Mobile, test endpoint chứng minh truyền đúng ngữ cảnh user/revision/idempotency xuống service.<br><br>APK gọi được API thật của cả UF3 và UF4; lỗi tích hợp được xử lý, chuyển nháp không tạo Task ngoài ý muốn. |
| **Trang Tín (tn)** — Database và tầng lưu trữ | Chịu trách nhiệm schema SQLAlchemy, migration, index/constraint và repository cho nguồn kế hoạch, Run, Thought, device/reminder, Draft và receipt; kế thừa model đã có.<br><br>Cung cấp thao tác lưu/truy vấn và transaction cho service của Hưng; kiểm revision, khóa duy nhất, rollback, quan hệ linked_draft_id/converted_task_id và phục hồi.<br><br>Chuẩn bị dữ liệu thử, hỗ trợ đổi default reminder mà không ghi đè lựa chọn user; review mọi thay đổi DB và đối soát dữ liệu đồng bộ.<br><br>Kiểm lưu bền sau restart, gửi đồng thời/gửi lại và xóa; phối hợp Thịnh về ánh xạ dữ liệu local–server. | Schema/migration/repository dùng được qua service của Hưng và router của Cương, dữ liệu thử tái lập được.<br><br>Kiểm thử transaction, constraint, rollback và phục hồi chứng minh dữ liệu không mất/trùng.<br><br>Báo cáo tương thích trên hệ quản trị đã kiểm thực tế; không suy PostgreSQL đạt từ SQLite. |
| **Quang Khải (kai)** — Android, Focus và ghi âm | Kiểm quyền, alarm/Doze và DND trên máy Android thật ngay đầu tuần; chọn cơ chế native đáp ứng chuông kết thúc và dọn tài nguyên.<br><br>Triển khai DND, Wakelock, alarm/receiver; giữ owner và snapshot để chỉ khôi phục phần app đã áp.<br><br>Hoàn thiện recorder giữ/nhả/hủy, finalize và kiểm file trước khi báo Đã cất; ba lượt lỗi thì mở text fallback qua UI.<br><br>Phối hợp xử lý alarm tới lúc đang thu, mất quyền, app background/process death và timeout cuối phiên.<br><br>Phối hợp chuyển âm sau phiên và kiểm dịch vụ giọng nói thật. | Module native/alarm/DND và recorder được ghép vào APK, có trạng thái quyền/apply/restore rõ ràng.<br><br>Audio thô lưu trong vùng riêng của app, phát lại được khi offline; timer không bị ngắt khi thu.<br><br>Chuông và khôi phục DND có log/video trên thiết bị được hỗ trợ, kể cả khi tắt màn hình.<br><br>Không tắt DND vốn user bật; có bằng chứng nhánh lỗi ba lượt và cleanup sau timeout. |
| **Quốc Thịnh (qt)** — Timer, thuật toán thời gian và offline | Hoàn thiện logic mốc nhắc, kiểm start, grace và gap giữa Session theo contract đã chốt; không dùng LLM tính lịch/thời gian.<br><br>Xây timeline work/rest từ segments, clock/checkpoint và phục hồi; không dựa vào số lần callback Dart để đếm thời gian.<br><br>Làm cache lịch/ngữ cảnh, trạng thái Run đã start, dữ liệu local và outbox events/Thought metadata; phối hợp Thanh An ghép vào Provider/UI.<br><br>Đối soát thời gian native–Dart–Backend, đổi giờ/reboot, reconnect và replay; giữ ended_at/actual có nguồn đúng. | Timer/clock và local repository dùng được qua interface chung; có unit test với clock giả.<br><br>Run đã start tiếp tục chạy khi mất mạng; mở lại có đúng segment, thời gian còn lại và ngữ cảnh.<br><br>Queue gửi lại không nhân bản; thời gian chờ check-in, micro-task và rest không bị tính vào actual work.<br><br>Không tự tạo Run mới offline khi capabilities chưa hỗ trợ. |
| **Thanh An (tan)** — Mobile và APK | Kế thừa Mobile dev, dựng và ghép Lịch hôm nay → Chuẩn bị phiên → Focus; có APK nhìn thấy được từ đầu tuần.<br><br>Làm Focus 1-Step UI, deadline, timer/segments, nút bắt đầu rõ thời lượng, trạng thái quyền và thẻ phục hồi ngữ cảnh.<br><br>Ghép recorder/Thought local, thông báo Đã cất, Undo và text fallback tại chỗ; giữ timer liên tục.<br><br>Làm Hộp suy nghĩ sau phiên: nghe lại, xóa có Undo và user chủ động tạo nháp UF1.<br><br>Nối API/state/cache/native, xử lý lỗi UI, kiểm chữ lớn/offline và build bản nghiệm thu. | APK chạy trên máy thật, đi được từ lịch chính thức đến Focus và Hộp suy nghĩ.<br><br>UI tiếng Việt, tối đa 3–5 thao tác chính mỗi màn hình; Focus chỉ có bước/phiên hiện tại.<br><br>Nghe được file thật, phục hồi được sau gián đoạn; không dùng dữ liệu gắn cứng trong đường demo chính.<br><br>Bản APK cuối cùng kèm mã kiểm tra, commit nguồn và hướng dẫn cài/cấu hình Backend. |
| **Doãn Thành (thành)** — QA và nghiệm thu | Chuẩn bị tình huống kiểm thử và fixtures dùng chung; kiểm song hành từ đầu tuần, không đợi ghép xong mới test.<br><br>Kiểm API/state, owner/revision, double tap/retry, offline/restart, lỗi thu/lưu ba lượt, Undo và chuyển nháp.<br><br>Đo máy thật cho alarm/Doze, DND, quyền bị từ chối, thu âm và phục hồi; kiểm không có STT/LLM trong UF4.<br><br>Ghi bug có bước tái hiện, giao người xử lý và chạy lại sau sửa.<br><br>Chạy kịch bản cuối trên đúng APK/commit, xác nhận phần đạt/chưa đạt. | Bộ ca và dữ liệu kiểm thử dùng chung; bug report có bước tái hiện và bằng chứng.<br><br>Báo cáo thiết bị ghi model/OS/quyền/mạng/commit, số lần đạt trên tổng số lần và sai lệch alarm quan sát được.<br><br>Video demo và biên bản kết luận riêng cho UF3, UF4 và MVP xuyên luồng; không ghi Pass cho phần chưa kiểm thật. |

**Ranh giới Backend–Database:** Nhóm có 8 thành viên. Cương chịu trách nhiệm tầng HTTP/router UF3–UF4, Hưng chịu trách nhiệm service và quy tắc nghiệp vụ, Tín chịu trách nhiệm schema/migration/repository và tính toàn vẹn dữ liệu. Luồng bàn giao: **Router (Cương) → Service (Hưng) → Repository/DB (Tín)**. Cương kiểm dữ liệu request và dependency xác thực; Hưng kiểm quyền sở hữu tài nguyên, revision, trạng thái và idempotency trong nghiệp vụ; Tín bảo đảm transaction/constraint hỗ trợ thực thi nguyên tử. Mỗi người kiểm thử tầng mình, cùng kiểm tích hợp; local repository/outbox trên Mobile vẫn do Thịnh triển khai.

### Những điểm cần phối hợp ngay đầu tuần

Các điểm phối hợp này nhằm triển khai và ghép UF3–UF4 trên nền `dev` đã dùng chung, không phải thiết lập lại API contract hay JSON từ đầu. Mỗi dòng có một người chịu trách nhiệm chính đến khi đầu ra được kiểm tra đạt. Nếu cần bổ sung contract, người phụ trách cập nhật bản chung cùng DTO/adapter liên quan; quyết định nghiệp vụ vẫn phải tuân thủ Project/AGENTS và được review theo phân hệ.

| Việc cần chốt | Người chịu trách nhiệm chính | Người hỗ trợ và phần hỗ trợ | Đầu ra người phụ trách chính phải bảo đảm |
| --- | --- | --- | --- |
| **Tích hợp phần việc tuần 2 vào dev** | **Minh Trung** | Quốc Hưng kiểm phần Backend/fallback; Thanh An kiểm Mobile/UI và khả năng build | Các phần việc được review và ghép vào `dev`, có commit dùng để build/smoke và nghiệm thu. Minh Trung theo dõi lỗi tích hợp và bảo đảm tính năng đã có vẫn chạy sau khi ghép |
| **Hoàn thiện nguồn giờ rảnh UF1 → UF2** | **Quốc Hưng** | Trang Tín phụ trách cách lưu và revision nguồn; Quốc Thịnh xác nhận thuật toán nhận đúng nguồn giờ rảnh | Triển khai đầy đủ đường Draft → Context/Candidate → Task theo contract/JSON chung, kiểm nguồn và revision. Quốc Hưng chịu trách nhiệm chạy được đường nhập việc mới → chốt lịch thật, không dùng nhầm giờ chung |
| **Mở rộng mốc nhắc UF3** | **Kim Cương** | Quốc Hưng làm service/default và logic tạo/hủy lời nhắc, review contract; Trang Tín hỗ trợ default/lưu trữ; Minh Trung cập nhật nghiệp vụ; Quốc Thịnh tính mốc; Quang Khải kiểm phát nhắc; Thanh An ghép adapter/UI | Cương bàn giao router/DTO nhận 1–3 mốc, mặc định `[900, 600, 300]`; contract và Mobile nhất quán, giữ lựa chọn user đã lưu. Kiểm tạo/hủy/chống trùng và phát nhắc thực tế khi Doze |
| **Điểm giao timer/native/recorder/local repository với UI** | **Thanh An** | Quang Khải bàn giao trạng thái/sự kiện alarm, DND và recorder; Quốc Thịnh bàn giao timeline, clock và local repository | Interface và luồng sự kiện đủ để ghép vào Provider/UI: start, chuyển segment, thu/lưu, alarm và cleanup. Thanh An chịu trách nhiệm chứng minh các module phối hợp được trong APK, gồm lúc alarm tới khi đang thu |
| **Vòng đời Thought, Undo và chuyển nháp** | **Kim Cương** | Trang Tín phụ trách repository/transaction; Quốc Hưng làm service Thought/chuyển nháp và kiểm trạng thái Run; Quốc Thịnh làm outbox; Thanh An ghép UI/Undo; Minh Trung kiểm nghiệp vụ; Quang Khải bảo vệ file và hỗ trợ giọng nói | Cương chịu trách nhiệm ghép từ cất/đồng bộ đến nghe/xóa/Undo và tạo Draft. Cất mới Undo thì hủy gửi và dọn file; xóa bản có sẵn giữ khả năng phục hồi trong 5 giây. Không STT trong phiên, retry không trùng; Tín đối soát dữ liệu server |
| **Thiết bị, dữ liệu thử và bằng chứng nghiệm thu** | **Doãn Thành** | Quang Khải cung cấp cấu hình/quyền và cách đo native; Thanh An cung cấp APK; Quốc Hưng/Kim Cương cung cấp Backend; Trang Tín cung cấp dữ liệu; Quốc Thịnh hỗ trợ đối soát thời gian | Ma trận thiết bị và ca kiểm thử, dữ liệu dùng chung, cách ghi bằng chứng và kết quả theo đúng APK/commit. Doãn Thành theo dõi ca chưa đạt và xác minh lại sau sửa; tác giả từng phần tự kiểm trước khi bàn giao QA |

Nhánh tính năng ngắn tách từ `dev` (ví dụ `feat/uf3-timer`), cập nhật từ `dev` mỗi ngày. Rebase dùng cho nhánh cá nhân; nhánh có nhiều người cùng làm phải thống nhất cách cập nhật, không tự viết lại lịch sử chung. Mỗi PR cần ít nhất một review chuyên môn từ người khác và các kiểm tra liên quan đạt trước khi Minh Trung merge; nếu Trung là tác giả vẫn cần reviewer độc lập. Sau xử lý conflict hoặc sửa code theo review, chạy lại kiểm tra bị ảnh hưởng. Thay đổi contract bàn giao cùng DTO, JSON mẫu và adapter liên quan.

### Các quyết định đã chốt sau góp ý

Đây là yêu cầu cần triển khai và nghiệm thu trong tuần 2, chưa phải xác nhận code hiện tại đã hỗ trợ. Không đưa mục chọn sẵn kiến trúc native vào danh sách quyết định; yêu cầu chuông đúng hạn, DND và kiểm thử Android của Project vẫn được giữ.

| Nội dung | Người chịu trách nhiệm chính | Việc đã chốt và đầu ra cần có |
| --- | --- | --- |
| **Làm sớm phần nền của UF3** | **Thanh An** | Trong 03–04/11, Khải xây module native, Thịnh xây timer/clock theo interface chung; An dựng 1-Step UI với state/API giả đúng contract. Cuối 04/11 phải ghép thử UI + timer + native. Ngày 05/11 chuyển sang API thật và kiểm các nhánh lỗi/phục hồi |
| **Nhắc trước phiên 15/10/5 phút** | **Kim Cương** | Default mới là `[900, 600, 300]`; nhận 1–3 số nguyên giây không âm, không trùng, chuẩn hóa thứ tự giảm dần. Cập nhật cả input/output DTO, default DB/service, capabilities liên quan, tài liệu và Mobile; không ghi đè cấu hình user đã lưu. Hủy mốc không còn hợp lệ khi Run bắt đầu/lịch thay đổi, không phát dồn các mốc đã trôi qua. Kiểm riêng lịch nhắc và chuông kết thúc trên máy thật; không mặc định cơ chế alarm đã chọn hỗ trợ chính xác mọi mốc khi Doze |
| **Chuông đến khi đang thu âm** | **Quang Khải** | Phát chuông dịu đúng mốc kết thúc, dung sai ≤1 giây; không chờ thả nút hoặc hoãn thêm 15 giây. Bảo vệ dữ liệu đang thu, xử lý audio focus và finalize file; phần hỏi check-in có thể chờ thao tác thu kết thúc nhưng không lùi `ended_at`, cộng thêm actual hay lùi timeout DND. Bàn giao bằng chứng file nghe lại được và chuông thực sự phát đúng hạn |
| **Undo qua outbox lưu bền** | **Kim Cương** | Cương phụ trách router và ghép toàn luồng; Hưng phụ trách service và điều kiện xử lý; Tín phụ trách transaction/dữ liệu server, Thịnh triển khai outbox/local repository, An ghép Undo, Khải hỗ trợ vòng đời file. Cất mới: lưu bền file/metadata trước khi báo Đã cất; giữ metadata chờ 5 giây rồi mới được gửi. Undo còn hiệu lực thì đánh dấu hủy nguyên tử, hủy gói gửi và dọn file. Xóa bản có sẵn: ẩn ngay nhưng giữ dữ liệu để khôi phục trong 5 giây; chỉ thực hiện xóa sau khi hết hạn, không còn phụ thuộc cần giữ. Lưu hạn gửi/trạng thái/ID chống trùng để phục hồi sau đóng app; worker gửi và Undo phải dùng cùng cơ chế kiểm trạng thái để không vừa hủy vừa gửi |
| **Nhánh ngắn và review hằng ngày** | **Minh Trung** | Thực hiện quy trình Git/review ở trên; phần ghép có commit và kết quả kiểm tương ứng. Không tự duyệt PR, không dồn toàn bộ tích hợp vào cuối tuần |

Với Undo, hết hạn thì nút không còn nhận thao tác; request Undo và worker gửi tại ranh giới 5 giây phải được phân xử nguyên tử. Nếu crash giữa hủy gói và xóa file, mở lại tiếp tục dọn file từ trạng thái đã lưu. Gửi lại sau mất phản hồi giữ cùng ID để không nhân bản; metadata không đồng nghĩa được phép gọi STT/LLM trong phiên.

## 3. Các giai đoạn thực hiện tuần tự

**Quyết định MT ngày 07/11/2026:** CheckIn tối thiểu tuần 2 chỉ có Nghỉ ngay và Báo bận; nghỉ ngay giữ kết quả chưa xác nhận, báo hoàn thành phiên/bước/việc để tuần 3. Payload, revision, timeout/offline và các ca kiểm đã được ghi tại [Contract CheckIn tuần 2](backend/MT_WEEK2.md). Đây là chốt nghiệp vụ để triển khai, chưa phải nghiệm thu API hoặc APK.

**Đầu ra MT ngày 07/11/2026:** [báo cáo MT: microcopy, micro-task, điểm chặn và kịch bản demo](backend/MT_WEEK2.md). Nền tuần 2 hiện dùng Gemini Transcribe; cần đối chiếu với mục tiêu Groq Whisper và kiểm provider thật. Cất suy nghĩ trong phiên chỉ lưu audio thô. Các tài liệu là đầu vào ghép/kiểm thử, chưa chứng minh UI hoặc UF3–UF4 đã hoạt động đầy đủ.

Cả nhóm thực hiện theo thứ tự: **Có lịch thật trên app → Chạy được phiên tập trung → Cất và xem lại suy nghĩ → Kiểm thử và bàn giao MVP**. Bảng phân công ở mục 2 xác định trách nhiệm cả tuần; các bảng dưới đây xác định phần việc cần ưu tiên và bàn giao trong từng giai đoạn.

Trong cùng một giai đoạn, các thành viên làm song song và ghép từng phần vào `dev`, tiếp tục dùng API contract/JSON chung. Mỗi giai đoạn có **một người chịu trách nhiệm chính về kết quả chung**; người này theo dõi các phần phụ thuộc, điều phối hỗ trợ và bảo đảm đủ điều kiện chuyển tiếp. Minh Trung điều phối tiến độ toàn tuần và review/merge; Doãn Thành kiểm tra bằng chứng nghiệm thu.

Ngày dưới đây là mốc dự kiến. **Chỉ chuyển giai đoạn khi đầu ra chung đã đạt**, không tự chuyển vì đã đến ngày. Nếu bị chặn, người phụ trách ghi rõ lỗi, người sửa và mốc kiểm lại; thành viên đã xong hỗ trợ xử lý hoặc chuẩn bị phần độc lập của giai đoạn sau, nhưng chưa coi nhóm đã chuyển giai đoạn. Việc chuẩn bị không được thay thế xử lý lỗi đang chặn.

### Giai đoạn 1 — Có lịch chính thức trên ứng dụng (03–04/11)

**Người chịu trách nhiệm chính: Quốc Hưng.** Mục tiêu là có dữ liệu đầu vào thật để cả nhóm xây UF3–UF4, đồng thời thử sớm các phần Android có thể ảnh hưởng tiến độ.

| Thành viên | Việc cần làm trong giai đoạn | Output cần bàn giao |
| --- | --- | --- |
| Minh Trung | Cùng Hưng đối chiếu `dev`/Backend/local, review và ghép phần còn thiếu trước khi ghi commit nền; đồng bộ quyết định nghiệp vụ và payload Chọn nghỉ tối thiểu | Commit nền đã kiểm chạy, danh sách thay đổi được ghép và ví dụ payload không tự gán hoàn thành |
| Quốc Hưng | Hoàn thiện nguồn giờ UF1 → UF2; thống nhất interface service UF3–UF4 với Cương và repository với Tín; bắt đầu service Run/device/reminder | Lịch thật đúng nguồn/revision; interface service, lỗi nghiệp vụ và điều kiện xử lý đủ cho Cương dựng router |
| Kim Cương | Dựng/đăng ký router UF3–UF4, DTO và dependency theo contract chung; mở rộng validation reminder offsets, chuẩn bị JSON mẫu và gọi service giả trong test | Các route xuất hiện trong OpenAPI; request/response/lỗi mẫu đủ cho Mobile ghép thử; test routing/validation với service giả được ghi rõ |
| Trang Tín | Rà schema/migration/repository và receipt; kiểm FK/unique source_thought_id đã có, ánh xạ linked_draft_id/converted_task_id; chuẩn bị dữ liệu và hỗ trợ default reminder | Tầng lưu trữ bàn giao cho Hưng/Cương, kiểm constraint thực tế không tạo trùng; cách ánh xạ Thought–Draft–Task rõ ràng |
| Quang Khải | Xây module native và ghép UI/timer ngày 04/11; thử sớm chuông khi đang giữ mic, giới hạn thời lượng thu theo capabilities, kiểm audio focus và file sau finalize | Bản ghép thử và bằng chứng chuông/file trên máy thật; ghi rõ hạn chế, không coi rung/UI thay chuông là đạt |
| Quốc Thịnh | Kiểm nguồn giờ; xây timeline/clock/checkpoint và nền cache theo interface chung; ghép UI/native với API giả ngày 04/11 | Timer chạy trong bản ghép thử, có kiểm thử clock và interface local repository |
| Thanh An | Build từ `dev`, kiểm UF1–UF2; đọc lịch thật và dựng 1-Step UI với state/API giả đúng contract, ghép timer/native ngày 04/11 | APK cài được ngày 03/11; cuối 04/11 có lịch thật và bản thử Focus đã ghép UI + timer + native, ghi rõ Run còn giả lập |
| Doãn Thành | Chuẩn bị máy, tài khoản/dữ liệu thử và ca kiểm; chạy đường tạo lịch, ghi lỗi và xác minh sau sửa | Bộ dữ liệu/ca kiểm chung, kết quả tạo lịch thật và biên bản thử native ban đầu |

**Điều kiện chuyển tiếp:** Trên APK từ commit chung, nhập việc với giờ rảnh riêng → xác nhận đầu vào → chốt phương án → thấy đúng Session trên lịch. Mở lại vẫn đọc được kế hoạch đã lưu. Đã có kết quả thử alarm/DND/recorder trên máy thật và hướng xử lý các vướng mắc phát hiện được. Dữ liệu mẫu chỉ dùng để dựng màn hình nội bộ, không thay đường nghiệm thu này.

Đồng thời, bản thử Focus dùng API giả đã ghép UI + timer + native theo contract chung. QA ghi rõ phần giả lập và lỗi còn lại; kết quả này chuẩn bị cho giai đoạn 2, chưa được tính là UF3 chạy thật.

### Giai đoạn 2 — Chạy trọn một phiên tập trung UF3 (05/11)

**Người chịu trách nhiệm chính: Thanh An.** Đầu vào là lịch thật đã đạt ở giai đoạn 1; mục tiêu là ghép Backend, timer và native thành một phiên chạy được trên điện thoại.

Kế thừa bản ghép thử ngày 04/11 để nối API thật; ngày 05/11 dành cho hoàn thiện tích hợp, lỗi quyền/API và phục hồi, không bắt đầu xây toàn bộ module từ đầu.

| Thành viên | Việc cần làm trong giai đoạn | Output cần bàn giao |
| --- | --- | --- |
| Minh Trung | Rà 1-Step UI, nhãn nút, micro-task và quy tắc kết thúc; review phần ghép | Nội dung UI đúng nghiệp vụ, không tự start hoặc tự hoàn thành khi hết giờ |
| Quốc Hưng | Hoàn thiện service UF3; end trả CheckIn cuối phiên, xử lý phản hồi và lazy timeout nguyên tử khi đọc/start sau hạn; giữ actual và kiểm start như cũ | Chọn nghỉ/báo bận đóng chờ đúng payload; quá hạn chuyển no_respond, không còn Run cũ chặn vì trạng thái lỗi thời; test retry/đồng thời/trả lời muộn đạt |
| Kim Cương | Nối router UF3 và PUT /api/v1/check-ins/{checkin_id}/response với service thật; bàn giao ID/revision, payload Chọn nghỉ/báo bận và lỗi cho Mobile | Endpoint UF3/CheckIn tối thiểu chạy qua HTTP, test quyền/revision/retry; chưa triển khai Voice/3-chip Skip/UF6 |
| Trang Tín | Bảo đảm Run/events/receipt lưu bền và nhất quán khi gửi lại hoặc restart | Dữ liệu Run đọc lại được, transaction và chống trùng có kiểm thử |
| Quang Khải | Ghép alarm, DND, Wakelock và cleanup; giữ snapshot/owner khi khôi phục | Bật Focus/DND khi start, chuông cuối phiên và khôi phục đúng phần app đã áp |
| Quốc Thịnh | Ghép mốc nhắc, timeline work/rest, clock/checkpoint và cache; tính actual từ nguồn thời gian hợp lệ | Timer phục hồi đúng, Run đã start vẫn chạy khi mất mạng, không cộng rest/micro-task/chờ check-in vào actual |
| Thanh An | Nối API/Provider/timer/native; Chọn nghỉ/báo bận gửi phản hồi CheckIn, offline lưu outbox cùng Thịnh; khôi phục DND cục bộ ngay đúng owner | Luồng kết thúc cập nhật cả thiết bị và Backend khi có mạng, không chờ API mới tắt DND; retry không nhân bản phản hồi |
| Doãn Thành | Kiểm start/retry, timer, screen off/reopen, offline sau start, alarm và DND cuối phiên | Bằng chứng luồng UF3 và kết quả từng nhánh bắt buộc để chuyển tiếp |

**Điều kiện chuyển tiếp:** Từ Session thật, nhận nhắc theo chính sách đã chốt, thấy micro-task và bấm bắt đầu một chạm để chạy Run/Focus/DND. Tắt mạng sau start và tắt/mở màn hình vẫn giữ đúng timer. Hết giờ có chuông, không tự complete Session/Step/Task; chọn nghỉ khôi phục DND đúng owner, không phản hồi thì cleanup chậm nhất `ended_at+300` giây. Double tap/retry không sinh Run trùng. Kiểm phản hồi CheckIn, offline rồi đồng bộ, đọc/start sau timeout và trả lời muộn: không kéo dài actual, không suy completed từ Chọn nghỉ, không bỏ điều kiện giờ/gap/quyền của start. Thiết bị tự khôi phục DND đúng hạn kể cả Backend không nhận request. QA có bằng chứng trên máy thật; đo lặp và hồi quy mở rộng tiếp tục ở giai đoạn 4.

### Giai đoạn 3 — Hoàn thiện UF4 trong phiên tập trung (06–07/11)

**Người chịu trách nhiệm chính: Kim Cương.** Đầu vào là phiên UF3 đã chạy trọn; mục tiêu là giữ nhất quán file trên thiết bị, metadata, đồng bộ và nháp mới sau phiên.

| Thành viên | Việc cần làm trong giai đoạn | Output cần bàn giao |
| --- | --- | --- |
| Minh Trung | Rà điều kiện gọi STT/LLM, microcopy, Undo và ba lượt thu/lưu; kiểm luồng chuyển ghi âm sau phiên | Quy tắc được thể hiện đúng trên app; bằng chứng không gọi STT/LLM trong UF4 |
| Quốc Hưng | Hoàn thiện service Thought/xóa/chuyển Draft và kiểm quyền trước STT; phối hợp Cương bỏ cache idempotency RAM trùng sau khi kiểm receipt DB, giữ hồi quy Run | Chuỗi STT → tạo Draft an toàn qua restart/retry, không gọi STT trong phiên hoặc tạo trùng Draft; không xây lại receipt đã có |
| Kim Cương | Nối router UF4 và chuỗi transcriptions → thoughts/{id}/draft với service thật; kiểm metadata Thought đã có trên server, quyền/revision và lỗi từng bước; ghép outbox/Undo cùng Thịnh/An | Hai API chạy đúng thứ tự theo schema, mỗi bước có khóa retry riêng; tạo Draft lỗi có thể thử lại bằng transcript đã lưu, không buộc gọi STT lại |
| Trang Tín | Hoàn thiện repository/transaction Thought → Draft, liên kết nguồn Run và receipt; kiểm rollback, gửi đồng thời, xóa và phục hồi | Dữ liệu giữ đúng liên kết, không trùng Thought/Draft; kiểm thử tính toàn vẹn và rollback, hỗ trợ Hưng/Cương đối soát lỗi dữ liệu |
| Quang Khải | Ghép recorder giữ/nhả/hủy, finalize file, lỗi ba lượt và chuông đúng hạn khi đang thu | File thật nghe lại được; chuông không bị hoãn 15 giây, timer/ended_at/timeout DND không bị kéo dài |
| Quốc Thịnh | Làm outbox lưu bền chờ 5 giây, phối hợp Cương/An về API/UI và Tín về dữ liệu server để xử lý Undo cất mới và xóa bản cũ; kiểm tranh chấp gửi/hủy và replay | Không gửi trước hạn; đóng app/mất mạng không mất dữ liệu; Undo và worker không vừa hủy vừa gửi, retry không trùng |
| Thanh An | Ghép Cất suy nghĩ/Undo/fallback/Hộp suy nghĩ; sau phiên đồng bộ metadata trước, gọi STT rồi tạo Draft theo yêu cầu user; chủ trì demo offline ngày 06/11 | APK giữ được file/transcript và trạng thái từng bước khi mất mạng; demo offline có bằng chứng, luồng chuyển nháp không trùng |
| Doãn Thành | Kiểm file thật, offline, ba lượt lỗi, Undo, alarm trong lúc thu, restart/replay và chuyển nháp | Bằng chứng UF4, danh sách lỗi có người sửa và kết quả kiểm lại |

**Điểm kiểm giữa giai đoạn, ngày 06/11 — Thanh An chịu trách nhiệm chính**, Khải/Thịnh hỗ trợ, Thành ghi bằng chứng; Cương theo dõi điểm nối server và chịu trách nhiệm toàn giai đoạn đến 07/11. Demo lịch thật → Focus → cất một ghi âm khi mất mạng; timer vẫn chạy và file nghe lại được. Nếu chưa đạt, tập trung sửa đường này trước khi mở rộng Hộp suy nghĩ.

**Điều kiện chuyển tiếp:** Chạy được toàn luồng cất audio → kết thúc phiên → nghe lại/xóa/Undo → user chủ động chuyển thành nháp UF1. Có bằng chứng ba lượt lỗi dẫn đến text fallback, Undo 5 giây, mất mạng/reconnect không mất hoặc nhân bản dữ liệu, không STT/LLM trong phiên. Chuông đến lúc đang thu vẫn được xử lý. Kết quả UF3 của giai đoạn trước vẫn đạt sau khi ghép recorder.

### Giai đoạn 4 — Ổn định và nghiệm thu MVP (08–09/11)

**Người chịu trách nhiệm chính: Doãn Thành.** Đầu vào là bản đã chạy xuyên UF1–UF4; khóa tính năng, tập trung sửa lỗi, kiểm chứng và bàn giao.

| Thành viên | Việc cần làm trong giai đoạn | Output cần bàn giao |
| --- | --- | --- |
| Minh Trung | Điều phối review/merge các bản sửa, đối chiếu phạm vi và hoàn thiện hướng dẫn/demo/tồn đọng tuần 3 | Commit nguồn cuối, tài liệu bàn giao và danh sách giới hạn có người phụ trách |
| Quốc Hưng | Sửa lỗi service kế hoạch/Run/Thought, review tích hợp Backend và chạy hồi quy nghiệp vụ; tổng hợp cấu hình cùng Cương | Service UF1–UF4 đúng với bản nghiệm thu, kiểm thử nghiệp vụ và hướng dẫn chạy tái lập được |
| Kim Cương | Sửa lỗi router/DTO/HTTP của UF3–UF4, chạy kiểm thử endpoint trên bản cuối; cùng Mobile và QA tái hiện lỗi xuyên luồng | OpenAPI/JSON mẫu khớp APK, endpoint tests và kết quả tích hợp UF3–UF4 có bằng chứng |
| Trang Tín | Kiểm dữ liệu sau restart/replay/Undo/chuyển nháp và sửa lỗi lưu trữ | Kết quả kiểm tính toàn vẹn, migration/dữ liệu thử khớp bản cuối |
| Quang Khải | Cùng QA đo alarm/Doze/DND, quyền và ghi âm trên máy thật; sửa lỗi native | Log/video đo thực tế, kết quả cleanup và giới hạn thiết bị được ghi rõ |
| Quốc Thịnh | Kiểm hồi quy clock, actual, offline và queue; hỗ trợ đối soát sai lệch thời gian | Kết quả đo và kiểm thử phục hồi/replay trên bản cuối |
| Thanh An | Sửa lỗi UI/tích hợp, build APK nghiệm thu ngày 08/11 và bản cuối sau kiểm lại | APK kèm SHA-256/commit, cài được theo hướng dẫn và không lỗi UI chặn demo |
| Doãn Thành | Chạy Nghiệm thu chung và checklist đo chuông; phân biệt Doze cưỡng bức/tự nhiên, callback/âm thanh thực tế; kiểm lại lỗi, demo/cài bởi người khác ngày 09/11 | Báo cáo đúng APK/commit, trạng thái thiết bị và phương pháp đo; video, số mẫu đạt/tổng và kết luận UF3/UF4/MVP rõ ràng |

**Điều kiện hoàn tất tuần:** Đủ sản phẩm và tiêu chí ở mục 4 trên đúng APK/commit bàn giao; người khác cài và chạy lại theo hướng dẫn. Lỗi chặn luồng, mất/trùng dữ liệu, gọi STT trong phiên hoặc khôi phục DND sai phải được sửa và kiểm lại. Phần chưa kiểm hoặc chưa đạt phải ghi rõ, không ký hoàn tất toàn bộ MVP chỉ vì đến ngày 09/11.

### Cách theo dõi và chốt từng giai đoạn

Cuối mỗi ngày, từng người cập nhật ngắn: **đã bàn giao gì và ở commit/PR nào; đang vướng gì, cần ai hỗ trợ; đầu ra tiếp theo là gì**. Người phụ trách giai đoạn tổng hợp một trạng thái chung: Chưa bắt đầu / Đang làm / Đang bị chặn / Chờ nghiệm thu / Đã đạt.

Khi đề nghị chuyển giai đoạn, người phụ trách đưa bản ghép trên `dev` để demo; Doãn Thành ghi kết quả đối chiếu điều kiện chuyển tiếp và bằng chứng; Minh Trung kiểm việc review/merge cùng các quyết định nghiệp vụ. Người phụ trách giai đoạn chịu trách nhiệm đóng việc và bàn giao đầu vào cho người phụ trách giai đoạn tiếp theo. Mọi tiêu chí trên là yêu cầu dự kiến, chưa phải xác nhận nhóm đã thực hiện đạt.

## 4. Kết quả cuối cùng của cả nhóm trong tuần 2

### Sản phẩm chung sau khi ghép xong

Cả nhóm bàn giao **MVP Android v0.2 cho thử nghiệm nội bộ**, có trải nghiệm thực thi thực tế thay vì chỉ có giao diện lập kế hoạch.

| Kết quả chung | Người dùng thực hiện được gì? | Điều kiện để ghi hoàn tất |
| --- | --- | --- |
| **Luồng từ việc mới đến lịch chính thức** | Nhập công việc, thời gian và giờ rảnh riêng; review/xác nhận đầu vào, xem phương án và chốt lịch | Lịch tạo qua API thật, đúng nguồn/revision; không gắn cứng Session hoặc tự lấy giờ chung thay nguồn mới |
| **UF3 — Phiên tập trung** | Nhận nhắc và gợi ý micro-task, bấm bắt đầu một chạm, làm việc với timer/Focus/DND và chuông kết thúc | Run thật, đúng Session; timer/segments/phục hồi/alarm chạy trên Android; kết thúc không tự complete Step/Task |
| **UF4 — Cất suy nghĩ** | Giữ nút để thu, nhả để cất; tiếp tục tập trung; sau phiên nghe lại/xóa/Undo hoặc tạo nháp mới | File audio thật phát lại được; ba lượt lỗi có text fallback; không STT/LLM trong phiên, chỉ chuyển sau phiên theo lệnh user |
| **Hoạt động khi mất mạng** | Tiếp tục Run đã bắt đầu, xem lịch cache, ghi âm và phục hồi ngữ cảnh | Cache/checkpoint/outbox hoạt động, gửi lại không nhân bản; không tự start Run mới offline |
| **Kết thúc an toàn** | Nghe chuông, chọn nghỉ/báo bận hoặc chưa trả lời check-in | ended_at/actual có nguồn đúng; khôi phục phần DND/Focus app đã áp khi nghỉ hoặc chậm nhất end+300 giây nếu chưa phản hồi; không tự dời lịch |
| **Bản demo tái lập được** | Người khác cài APK và làm theo hướng dẫn để chạy cùng luồng | APK, mã kiểm tra, commit nguồn, Backend, dữ liệu thử, video và test report khớp nhau |

### Những gì phải có trong gói bàn giao

- APK MVP v0.2, mã SHA-256 của APK và SHA commit nguồn tương ứng.
- Hướng dẫn cài APK, chạy Backend, cấu hình kết nối và chuẩn bị quyền/dữ liệu demo.
- API contract và JSON mẫu chung được cập nhật theo phần UF3–UF4 thực tế đã bàn giao.
- Video demo cùng báo cáo kiểm thử; ghi rõ nguồn thật/giả lập và thiết bị đã chạy.
- Danh sách lỗi/giới hạn còn lại, người phụ trách và điểm bàn giao cho tuần 3.

### Nghiệm thu chung

| Nhóm kiểm tra | Kết quả bắt buộc |
| --- | --- |
| Start và retry | Double tap hoặc mất phản hồi sau khi server lưu không tạo nhiều Run; sai owner/thiết bị/revision bị chặn |
| Timer và phục hồi | Work/rest đúng segments; micro-task không có timer/Run riêng, không cộng duration/actual; không nhân buffer lại |
| Offline | Run đã được Backend chấp nhận tiếp tục chạy khi mất mạng; timer, alarm, lịch cache và UF4 vẫn dùng được |
| Nhắc trước phiên | Cấu hình mới mặc định 15/10/5 phút; nhận 1–3 mốc hợp lệ không trùng, giữ cấu hình cũ. Kiểm tạo/hủy/revision, không phát nhắc thừa sau start hoặc phát dồn mốc đã qua; đo riêng khi Doze |
| Alarm và DND | Đo thời điểm âm thanh thực sự phát ở foreground, screen off/background, Doze và lúc đang thu; dung sai ≤1 giây, không hoãn 15 giây. File thu vẫn hợp lệ. Chưa phản hồi cuối phiên thì khôi phục DND đúng owner chậm nhất end+300 giây, không dời theo lúc finalize file |
| Thu/lưu lỗi | Tổng cộng ba lượt thu/lưu; hai lượt đầu cho thử lại, lượt ba mở text tại chỗ; không báo Đã cất nếu file/metadata chưa hợp lệ |
| Undo | Cất mới lưu bền rồi giữ metadata tại outbox 5 giây; Undo hợp lệ hủy gửi và dọn file. Xóa bản có sẵn giữ khả năng khôi phục trong 5 giây. Kiểm đóng app ở giây 3, Undo sát hạn gửi, crash khi dọn file và reconnect/retry: không mất/trùng hoặc gửi bản đã hủy |
| STT/LLM | Không có lời gọi trong UF4, kể cả retry/reconnect; chuyển âm sau phiên phải do user chủ động chọn và qua kiểm quyền trước provider |
| Thought → Draft | Nghe được file offline; metadata tồn tại server trước STT; kiểm chuỗi transcriptions → tạo Draft, retry từng bước và restart. Cùng ý định chỉ có một Draft UF1, không tự xác nhận Task/ghi Calendar; FK/unique và receipt DB thực tế được kiểm |
| Kết thúc và dữ liệu | End có CheckIn cuối; phản hồi đóng chờ đúng nghiệp vụ, Chọn nghỉ không tự completed. Lazy timeout khi đọc/start xử lý Run quá hạn nguyên tử, trả lời muộn cập nhật cùng CheckIn. Offline/outbox/retry không kéo dài actual/ended_at; DND tự khôi phục ở thiết bị dù không có request Backend |
| UI | Focus chỉ có bước/phiên hiện tại, deadline rõ, nhãn tiếng Việt cụ thể; không overflow với chữ lớn, không hộp thoại hỏi chắc chắn xóa |

Tối thiểu có một Android thật để nghiệm thu; mục tiêu kiểm thêm máy khác OS/OEM. Báo cáo ghi model/OS/quyền/mạng, số mẫu đạt/tổng mẫu và sai lệch quan sát được. Mục tiêu ít nhất 30 lần chuông trên mỗi máy thuộc ma trận theo checklist kỹ thuật, phân loại điều kiện từng mẫu; có ít nhất một phiên đủ thời lượng cấu hình rút USB, mục tiêu thêm một phiên đối chiếu. Xác nhận trạng thái Doze thay vì suy từ việc rút cáp; log callback và bằng chứng âm thanh được ghi riêng; mẫu kiểm thử ngắn không thay toàn bộ phiên thật. Không suy đã đạt độ tin cậy 99,9% hoặc hỗ trợ mọi Android từ số mẫu nhỏ.

Kết luận cuối tách rõ **UF3 đạt/chưa đạt**, **UF4 đạt/chưa đạt** và **MVP xuyên luồng đạt/chưa đạt**. Có code riêng hoặc screenshot chưa đủ: phần việc phải ghép được, có test/bằng chứng và reviewer. Không ghi phần STT/Google thật đã đạt chỉ từ provider giả hoặc job pending.

### Kịch bản chứng minh kết quả cả nhóm

> Cài APK → nhập việc và giờ riêng → chốt lịch → mở phiên → thấy micro-task → bấm bắt đầu khi có mạng → tắt mạng → cất một suy nghĩ bằng audio → tắt/mở màn hình và tiếp tục đúng timer → nghe chuông → chọn nghỉ, DND được khôi phục → mở Hộp suy nghĩ, nghe lại/xóa/Undo → bật mạng, chủ động chuyển ghi âm thành nháp UF1 → mở lại app và dữ liệu vẫn còn, không trùng.

Video chính có thể dùng cấu hình demo rút ngắn, tách rõ khỏi cấu hình sử dụng thật. Các nhánh Doze, ba lượt lỗi và timeout DND có bằng chứng riêng; không giả đã kiểm đủ chỉ từ một lượt demo.

Sau tuần này, đồ án có nền thực thi gồm Run, thời gian thực tế, trạng thái kết thúc và Hộp suy nghĩ để nối sang tuần 3. Toàn bộ Voice Check-in/Skip của UF5, đề xuất/duyệt UF6, Settings/học buffer UF7, Google Calendar thật và Auth production tiếp tục nằm ở phần sau; không đưa chúng vào phần đã hoàn thành nếu chưa triển khai.

**Đích cuối:** một ứng dụng Android có thể dùng để thực hiện một phiên tập trung và cất/lấy lại suy nghĩ, kèm bằng chứng chạy chung của cả nhóm.
