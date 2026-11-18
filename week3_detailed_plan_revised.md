# Kế hoạch tuần 3 — Hoàn thiện UF5–UF7 và nghiệm thu MVP Android

**Thời gian** 10–16/11/2026, giờ Việt Nam (UTC+7).  
**Mục tiêu** Người dùng đăng nhập bằng Google, liên kết Google Calendar và dùng lịch bận thật để lập kế hoạch. Từ một phiên tập trung thật, người dùng báo tiến độ, xem và duyệt phương án dời lịch, rồi chỉnh cài đặt cho các phiên sau. Bản APK bàn giao chạy xuyên UF1–UF7 trên Android thật.  
**Tích hợp** Minh Trung điều phối `dev` và chốt nghiệp vụ cùng nhóm. Quốc Hưng duyệt tích hợp Backend. Doãn Thành xác nhận kết quả trên đúng bản bàn giao.

Tài liệu này tinh chỉnh bản kế hoạch v3.0 đính kèm theo cách tổ chức của [kế hoạch tuần 2](week2_detailed_plan.md). Quy tắc nghiệp vụ và hợp đồng dùng chung lấy từ [Project.md](../Project.md), [AGENTS.md](../AGENTS.md) và [schema Backend](backend/schema.md). [Handoff Backend tuần 3](backend/WEEK3_SPRINT_HANDOFF.md) cung cấp bối cảnh và phần việc để đối chiếu với mã nguồn.

## 1. Tổng kết tuần trước và phạm vi tuần này

| Phân hệ | Nền quan sát được khi lập kế hoạch | Phần cần tiếp nối |
| --- | --- | --- |
| UF1–UF4 | Có nền lập kế hoạch, timer/Run, Thought, fallback UF2 và luồng Mobile từ tuần 2 | Chạy smoke trên commit tích hợp. Giữ đường UF1–UF4 qua API thật, timer và thu âm ngoại tuyến sau khi Run đã bắt đầu |
| UF5 | Có API đọc/tạo CheckIn theo Run, API phản hồi theo CheckIn và API sửa actual theo Run | Ghép đủ năm kết quả cuối phiên, ba lựa chọn Bỏ qua, Undo, voice hai chiều và khôi phục DND/Focus trên máy thật |
| UF6 | Có model đề xuất và hàm Python tịnh tiến ô lịch. Router UF6 trên bản đối chiếu mới có khung | Hoàn thiện vòng tạo → xem → tính lại → duyệt/từ chối, kiểm phạm vi Task và revisions |
| UF7 | Có API đọc preferences, availability, tạo change request, hàm A20 và bảng `adaptation_suggestions` | Ghép màn hình cài đặt, giữ snapshot Run đang chạy và chuyển đổi lịch tương lai qua UF6. Đánh giá A20 theo quyết định đầu tuần |
| Tài khoản và Calendar | Có OAuth Calendar, đọc dữ liệu bận và worker outbox. Worker chuyển job sang `conflict` khi gặp ETag 412 | Ghép đăng nhập Google trên APK với tài khoản ProPlan. Liên kết Calendar cho tài khoản đã đăng nhập, đưa lịch bận thật vào UF1–UF2 và kiểm đồng bộ phiên sau xác nhận/duyệt |
| Lưu trữ và kiểm thử | Checkout đối chiếu `1ab4a25` vẫn còn biến RAM trong Draft service. `20261108_01` đã được dùng cho Calendar | Ghi SHA nền trên `dev` và xác minh đường lưu thật. Dùng revision mới nếu cần migration. Kết quả test phải gắn với SHA và môi trường đã chạy |

Các nhận định “đã dọn toàn bộ RAM cache” và “319 test pass” trong bản v3.0 cần được xác minh lại trên SHA nhóm chọn. Số test lịch sử dùng để tham chiếu. Mốc nghiệm thu dùng kết quả suite hiện hành và ghi riêng `pass`, `fail`, `skip`, `xfail` theo môi trường. [Biên bản nền tuần 2](week2_baseline_20261106.md) · [Checklist kỹ thuật tuần 2](week2_technical_checklist.md)

| Phạm vi | Kết quả cần bàn giao |
| --- | --- |
| **Bắt buộc** | Google Sign-In trên APK → liên kết Google Calendar → đọc lịch bận của người dùng cho UF1–UF2. UF5 đủ nhánh, UF6 duyệt an toàn, UF7 giữ cấu hình Run hiện tại và xử lý lịch tương lai. Calendar đẩy phiên sau confirm/approve, có bằng chứng xử lý 412. Hồi quy UF1–UF4, PostgreSQL staging, APK và demo trên Android thật |
| **Theo quyết định ngày 10/11** | A20 có thể chạy thử theo yêu cầu nếu nhóm chốt policy pilot |
| **Lùi trước nếu thiếu thời gian** | Worker gợi ý A20 định kỳ, hòa giải nâng cao khi Calendar 412 và preview dời lịch ngoại tuyến. Cache lịch, bảo vệ lịch đã chốt và phát hiện 412 vẫn thuộc phạm vi bắt buộc |

**Luồng tài khoản và lịch bận** Sau khi đăng nhập Google, ứng dụng hiển thị **Kết nối Google Calendar**. Người dùng chủ động cấp quyền đọc và chọn lịch làm nguồn. Backend lấy các khoảng bận trong phạm vi ngày cần lập kế hoạch. UF1 dùng chúng để đối chiếu giờ rảnh khi xác nhận đầu vào, UF2 dùng chúng để loại các khe bận khi xếp phiên. Nếu người dùng bỏ qua cấp quyền hoặc nguồn lịch gặp lỗi, ứng dụng báo tình trạng và cho nhập giờ rảnh thủ công. Quyền ghi được cấp riêng khi người dùng muốn đồng bộ phiên. [Đăng nhập và cấp quyền trên Android](https://developer.android.com/identity/sign-in/legacy-gsi-migration) · [Google Calendar FreeBusy](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query)

## 2. Bảng phân công công việc từng người

Mỗi mã việc ở mục 3 có một người chịu trách nhiệm đến lúc đầu ra được ghép và QA kiểm. Người hỗ trợ bàn giao phần phụ thuộc theo cùng hợp đồng.

| Thành viên | Phần chịu trách nhiệm | Đầu ra bàn giao |
| --- | --- | --- |
| **Minh Trung (`mt`)** | Khóa SHA/phạm vi, chốt nghiệp vụ và hợp đồng cùng nhóm, soạn câu hỏi UF5/lời giải thích UF6, review COGA, điều phối merge vào `dev` | Biên bản quyết định, prompt/microcopy tiếng Việt và bộ tình huống kiểm, commit tích hợp và biên bản bàn giao |
| **Võ Quốc Hưng (`vqh`)** | Phụ trách Google Sign-In từ APK đến phiên ProPlan. Chịu trách nhiệm service UF5–UF7, thuật toán UF6/nguồn giờ rảnh, quy tắc hiệu lực cài đặt và tích hợp Backend | Luồng đăng nhập thật và ca kiểm vòng đời phiên, service và kiểm thử nghiệp vụ, fixtures API, review PR Backend và đường lưu nguyên tử qua DB |
| **Đặng Kim Cương (`kc`)** | Router/DTO UF5–UF7, OpenAPI/JSON mẫu và ánh xạ lỗi. Hỗ trợ Hưng ghép HTTP/DTO đăng nhập và Khải ghép API Calendar | Hợp đồng ghép Mobile và kết quả HTTP integration |
| **Trần Trang Tín (`tn`)** | SQLite/PostgreSQL staging, migration/repository/receipt, xác minh Draft lưu bền. Hỗ trợ Khải về lưu kết nối và Calendar outbox | DB dùng được cho tích hợp, log migration/suite và kết quả kiểm lưu trữ/outbox |
| **Trần Thanh An (`tan`)** | Ba giao diện UF5–UF7, ghép API/Provider/native/cache, Undo và APK release | APK chạy luồng thật, kết quả analyze/profile, mã SHA-256 và hướng dẫn cài |
| **Trần Quang Khải (`kai`)** | Phụ trách Google Calendar từ liên kết, chọn lịch và đọc lịch bận đến kiểm đồng bộ sau duyệt. Chịu trách nhiệm chuông, DND/Wakelock và pipeline STT/TTS | Luồng Calendar trên APK, bằng chứng dùng lịch bận thật và đồng bộ, tích hợp Android/voice và nhật ký đo máy thật |
| **Nguyễn Quốc Thịnh (`qt`)** | Cache lịch/phương án, đồng bộ thời gian, outbox CheckIn/Thought metadata và replay | Local repository, phục hồi ngoại tuyến, kiểm thử đổi giờ/restart/reconnect và chống trùng |
| **Doãn Thành (`thành`)** | Chuẩn bị fixtures và ca từ ngày đầu, kiểm dọc mỗi giai đoạn, nghiệm thu Android/PostgreSQL/Calendar | Kết quả theo mã ca và SHA, báo cáo máy thật, video và danh sách lỗi còn mở |

**Điểm giao kỹ thuật** Cương phụ trách HTTP/DTO → Hưng phụ trách nghiệp vụ, quyền tài nguyên và revision → Tín phụ trách transaction/constraint và lưu trữ. An ghép UI/Provider với giao diện native của Khải và local repository của Thịnh. Hưng duyệt phần Backend trước khi Trung điều phối ghép vào `dev`.

**Điểm giao đăng nhập và Calendar** Hưng bàn giao phiên ProPlan và định danh người dùng đã xác thực cho Khải. Khải dùng phiên đó để liên kết Google Calendar, bàn giao các khoảng bận và phạm vi dữ liệu cho luồng xếp lịch của Hưng. Cương hỗ trợ API, An hỗ trợ giao diện và Tín hỗ trợ lưu kết nối/outbox.

## 3. Đầu việc và điều kiện chuyển giai đoạn

### Giai đoạn 1 — Khóa nền, hợp đồng và chính sách (10/11)

**Người chịu trách nhiệm giai đoạn** Minh Trung.

| Mã | Chính · Hỗ trợ | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- |
| **W3-01** | Trung · Hưng, An, Thành | Chọn SHA nền `dev`, đối chiếu bản v3.0 với code, OpenAPI và kết quả tuần 2 | Ghi SHA, APK smoke và bảng đã có/cần sửa/cần thêm. Mỗi chênh lệch có người nhận và bằng chứng cần bổ sung |
| **W3-02** | Cương · Hưng, An, Thịnh, Thành | Khóa hợp đồng UF5–UF7 theo schema hiện hành. UF5 đi theo Run/CheckIn, UF6 theo `/proposals`, UF7 theo preferences, change requests và suggestions | Danh sách method/path, DTO, enum, quyền, revisions, idempotency và lỗi; có JSON mẫu xuyên luồng cho tạo/đọc/tính lại/duyệt/từ chối proposal. Mọi bổ sung cho Undo/voice có quyết định và fixtures đi kèm |
| **W3-03** | Trung · Hưng, Cương, An, Khải, Thành | Chốt nghĩa của Chọn nghỉ, trả lời muộn, quyền và hạn Undo, handoff DND khi làm tiếp, phạm vi A20 và hợp đồng phiên đăng nhập/luồng cấp quyền Calendar | Ghi bảng quyết định và ca kiểm. Nếu policy handoff hoặc A20 chưa chốt, đánh dấu phần phụ thuộc đang chờ. Giữ kiểm gap 900 giây, quyền ưu tiên cấu hình thủ công và tách kết quả Phiên/Bước/Task |
| **W3-04** | Tín · Hưng, Thành | Dựng PostgreSQL 16 staging trên cổng 5433, kiểm migration với DB mới và dữ liệu cũ, kiểm Draft/receipt qua restart/retry | Log upgrade, đúng một head, dữ liệu cũ được bảo toàn và bộ seed chung. Tách phần fixture/test khỏi đường ghi thật khi đánh giá RAM cache. Chỉ tạo migration/index mới khi xác định thay đổi cần thiết và có cách phục hồi |
| **W3-05** | Thành · Hưng, Khải, An, Tín | Chuẩn bị fixture, thiết bị và hai tài khoản Google thử nghiệm có lịch bận khác nhau. Hưng xác nhận cấu hình đăng nhập và dấu vân tay chứng chỉ ký APK. Khải xác nhận cấu hình OAuth Calendar và URL callback. Chạy smoke tuần 2 và thử phép đo alarm/Doze | Có log smoke, cấu hình và dữ liệu setup, ca kiểm đăng nhập/liên kết Calendar trên APK và ca UF5–UF7. Vướng mắc cấu hình Google có người nhận ngay ngày 10/11 |

**Điều kiện chuyển tiếp** Có SHA nền và fixtures chung, khóa các điểm giao cần cho UF5 → UF6. Hạng mục đang chờ quyết định có người chịu trách nhiệm và đường triển khai độc lập được xác định.

Khi chốt W3-03, nhóm đối chiếu Moving Average 7–10 phiên trong AGENTS với A20 Median Step trong `Project.md`. Nếu chạy pilot A20, thông số tham chiếu là tối thiểu 5 mẫu trong cửa sổ 15 Step sạch, một mẫu mỗi Step, miền `[1.0, 2.0]`, làm tròn 0.05 và ngưỡng chênh tuyệt đối 0.2. Ghi rõ toán tử tại ngưỡng, cooldown và thời điểm chạy trước khi dùng trong ca nghiệm thu.

### Giai đoạn 2 — Chạy dọc UF5 → UF6 (11–12/11)

**Người chịu trách nhiệm giai đoạn** Quốc Hưng. Thanh An chịu trách nhiệm bản APK tích hợp ngày 12/11.

| Mã | Chính · Hỗ trợ | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- |
| **W3-06** | Hưng · Cương, Tín | Hoàn thiện service CheckIn cuối Run và năm kết quả completed, incomplete, busy, skipped, không phản hồi | Hết giờ chỉ mở CheckIn. `ended_at` và actual work/rest có nguồn, thời gian chờ tách riêng. Không phản hồi dùng `no_respond` với `outcome=unknown` nếu chưa xác nhận. Phản hồi muộn cập nhật cùng CheckIn, giữ actual và tránh sinh mẫu học trùng |
| **W3-07** | An · Cương, Hưng, Thịnh, Trung | Ghép UI năm nhánh, ba chip Hoãn phiên/Bỏ phiên/Bỏ bước và SnackBar Hoàn tác 5 giây. Nối lựa chọn ảnh hưởng lịch sang UF6 | Phản hồi UI ngay, hoàn tác đúng phạm vi trong hạn và xử lý retry/restart theo hợp đồng. Có ca Undo đồng thời với replay. Lịch chính thức, báo thức và Calendar chỉ đổi theo quyết định duyệt hợp lệ |
| **W3-08** | Khải · An, Hưng, Thành | Ghép chuông và DND/Focus vào CheckIn. Giữ trong lúc hỏi; khôi phục theo owner/snapshot khi nghỉ, kết thúc, báo bận, bỏ qua hoặc chậm nhất `ended_at+300` nếu im lặng | Log máy thật khi offline, tắt màn hình/Doze và mở lại app. Giữ DND do người dùng bật sẵn. Timeout cũ bỏ qua Run mới; cách xử lý làm tiếp theo quyết định W3-03 |
| **W3-09** | Trung · Khải, An, Hưng | Soạn câu hỏi UF5 dựa trên kết quả đang chờ và câu giải thích UF6 dựa trên dữ liệu đã tính. Ghép STT/TTS ưu tiên Groq Whisper/edge-tts, fallback thiết bị theo pipeline | Có voice hai chiều và đường thao tác bằng chữ. Lời UF6 ngắn, không phán xét và chỉ dùng lý do, số liệu do service cung cấp. Khi AI lỗi có lời giải thích mẫu; kết quả nghiệp vụ và việc duyệt do hệ thống kiểm tra và người dùng quyết định |
| **W3-10** | Hưng · Cương, Tín, Thành | Hoàn thiện service UF6 từ hàm Python có sẵn. Kiểm giờ rảnh sau khi trừ bận, phiên đã chiếm chỗ, thứ tự/gap/deadline, Calendar coverage và phạm vi Task | Tạo đề xuất `pending_approval`, có before/after và số phút thiếu khi thiếu tổng quỹ giờ. Có ca deficit bằng 0 nhưng vẫn thiếu khe phù hợp. Giữ Run đang chạy, lịch đã chốt và các Task ngoài `allowed_task_ids` |
| **W3-11** | Cương · Hưng, An, Thịnh | Ghép API proposal theo hợp đồng, nối màn hình so sánh và phục hồi khi mở lại app | APK gọi API thật để tạo và xem proposal, đóng app giữ lịch, mở lại tính theo thời gian và nguồn hiện hành. Duyệt dựa trên đúng bản đã xem. Approve kiểm quyền, revisions và khả thi rồi lưu lịch, receipt và outbox cùng transaction; reject giữ lịch cũ |

**Điểm kiểm ngày 12/11** Từ một Run thật, chạy nhánh Chưa xong → tạo đề xuất → đóng/mở app → xem bản được kiểm lại → duyệt hoặc từ chối. Thành kiểm thêm hết giờ, không phản hồi, Undo và duyệt bản cũ. Mốc này yêu cầu ít nhất một đường dọc chạy trên APK/API thật. Các nhánh còn lại tiếp tục kiểm trước khi khóa tính năng.

### Giai đoạn 3 — Ghép UF7, phục hồi ngoại tuyến và Calendar (13–14/11)

**Người chịu trách nhiệm giai đoạn** Quốc Hưng.

| Mã | Chính · Hỗ trợ | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- |
| **W3-12** | Hưng · Cương, Tín, An | Ghép cài đặt work/rest, nhắc, giọng đọc và buffer với change request. Tách yêu cầu chờ duyệt và cấu hình hiệu lực | Run đang chạy giữ `settings_snapshot`. Đổi giọng/nhắc áp dụng từ phiên sau. Đổi thời lượng/nghỉ ảnh hưởng lịch đã chốt tạo UF6; khi chưa duyệt, cấu hình hiệu lực và lịch hiện hành giữ nguyên đối với phần đang chờ |
| **W3-13** | An · Trung, Cương, Khải | Hoàn thiện màn hình UF7 và màn hình so sánh UF6. Rà cả ba giao diện mới theo COGA | Có cài đặt, trạng thái chờ duyệt, hạn chót và nội dung trước/sau. Tối đa 3–5 tương tác chính trên mỗi màn hình, nhãn rõ hành động, dùng Hoàn tác cho Bỏ qua và giữ 1-Step UI khi Focus |
| **W3-14** | Thịnh · An, Cương, Khải | Cache lịch đã chốt và proposal, lưu hàng đợi CheckIn/Thought metadata, đối soát native/Dart/server | Offline xem lịch cache và lưu được phản hồi. Khi online, replay cùng khóa chống trùng, xử lý revision conflict và chỉ mở duyệt sau khi backend tính lại bản mới. Trao đổi timestamp có offset, lưu UTC, hiển thị theo múi giờ người dùng. Nếu có preview cục bộ, ghi rõ là bản tạm |
| **W3-15** | Khải · Hưng, Cương, Tín, An, Thành | Ghép nút Kết nối Google Calendar sau đăng nhập, cấp quyền đọc, chọn lịch và ngắt/kết nối lại. Gắn CalendarConnection với user đang đăng nhập, đưa khoảng bận vào UF1–UF2. Dùng outbox hiện có khi đã cấp quyền ghi và xác nhận lịch | Có một sự kiện bận thật bị loại khỏi khe xếp phiên trên APK. Từ chối hoặc thu hồi quyền vẫn cho nhập giờ rảnh, có thông báo tình trạng nguồn. Khi ghi lịch, kiểm retry tránh trùng và 412 chuyển `conflict`. Thay đổi giờ hoặc xóa phiên từ Google đi qua UF6 |
| **W3-16** | Hưng · Cương, An, Tín, Thành | Ghép Google Sign-In trên APK → xác minh Google ID token → cấp phiên ProPlan theo Google `sub`. Triển khai khôi phục phiên, hết hạn và đăng xuất theo hợp đồng W3-03. Bàn giao phiên đã xác thực cho W3-15 | APK cài mới đăng nhập bằng tài khoản Google thật. Có ca token sai, đóng/mở app, phiên hết hạn, đăng xuất và đổi tài khoản. Tài khoản B dùng dữ liệu và kết nối Calendar của B. Từ chối quyền Calendar vẫn giữ phiên ProPlan hợp lệ |
| **W3-17** | Hưng · Trung, Tín, Cương | Nếu chốt pilot, dùng A20 hiện có với mẫu Step hoàn tất sạch, kiểm policy revision, ngưỡng và cooldown; bàn giao đường xem/phản hồi gợi ý | Có ca thiếu mẫu, loại trùng, chênh ngưỡng và từ chối gợi ý. Mọi thay đổi buffer do người dùng chọn. Dùng thông số thử nghiệm theo quyết định W3-03, đồng bộ Project/AGENTS/schema nếu nhóm chốt policy mới |

**Điều kiện khóa tính năng ngày 14/11** Bản ghép chạy qua ba luồng mới và có ca đăng nhập Google → kết nối Calendar → xếp phiên tránh một khung bận thật. QA đã nhận bản ứng viên và có kết quả hồi quy bắt buộc sơ bộ. Hạng mục có điều kiện được đánh dấu đạt hoặc dời kèm người nhận. Sau mốc này ưu tiên sửa lỗi, kiểm lại và đóng gói.

### Giai đoạn 4 — Kiểm hồi quy và bàn giao (15–16/11)

**Người chịu trách nhiệm giai đoạn** Doãn Thành.

| Mã | Chính · Hỗ trợ | Việc cần làm | Bàn giao và cách kiểm |
| --- | --- | --- | --- |
| **W3-18** | Thành · Tín, Hưng, Cương | Chạy backend suite trên SQLite và PostgreSQL staging, kiểm migration và kịch bản Console E2E trên SHA ứng viên | Log lệnh, DB thực tế, SHA, số pass/fail/skip/xfail và kết quả từng bước. Xác nhận test PostgreSQL kết nối server thật. Dùng kịch bản Console 34 bước nếu đã khớp hợp đồng ngày 10/11, cập nhật bước lệch trước khi chạy |
| **W3-19** | Thành · Khải, Thịnh, An | Kiểm máy thật cho UF1–UF7, alarm/Doze, DND, voice, Undo, mất mạng/reconnect và xung đột Calendar | Ma trận máy/OS/quyền/mạng, số lượt đạt/tổng, độ lệch chuông đo từ âm thanh thực, thời điểm cleanup và kết quả từng ca. Ghi thiết bị/chế độ chưa kiểm. Kiểm UF4 chỉ thu/lưu âm thô trong phiên |
| **W3-20** | An · Khải, Thành | Chạy `dart analyze`, đo các chuyển động chính trên thiết bị và đóng APK release | APK và SHA-256 gắn với commit nguồn, log analyze/profile, hướng dẫn cài và kết nối Backend. Kiểm lại trên APK cuối nếu SHA thay đổi |
| **W3-21** | Trung · Thành, Hưng, An | Chốt commit bàn giao, tập hợp bằng chứng và ghi giới hạn. Một thành viên khác cài theo hướng dẫn và chạy demo | Biên bản kết quả theo UF, video toàn trình, danh sách lỗi có người nhận và kết quả cài/chạy lại trên bản giao |

### Cách theo dõi và chốt từng giai đoạn

Cuối ngày, mỗi người cập nhật mã việc, trạng thái, PR/SHA, bằng chứng và phụ thuộc đang chờ. Dùng các trạng thái **Chưa bắt đầu / Đang làm / Bị chặn / Chờ nghiệm thu / Đã đạt / Dời**. Người phụ trách giai đoạn tập hợp kết quả, QA xác nhận các điều kiện chuyển tiếp. Trung ghi quyết định cắt phạm vi và người nhận phần dời.

## 4. Tiêu chí nghiệm thu chung và gói bàn giao

| Nhóm kiểm | Điều kiện ghi đạt | Bằng chứng |
| --- | --- | --- |
| **UF5 — Kết quả và Hoàn tác** | Đủ năm nhánh, ba chip Skip và Undo 5 giây. Hết giờ, chọn nghỉ hoặc im lặng giữ kết quả chưa xác nhận. Phiên cuối vẫn cần hỏi riêng kết quả Bước. Actual và phản hồi muộn đúng hợp đồng | W3-06–09, test HTTP, log và thao tác trên APK |
| **UF6 — Lịch và revision** | Đóng app giữ lịch. Bản chờ mở lại được kiểm theo thời gian/nguồn hiện hành. Từ chối, offline, stale hoặc superseded giữ lịch. Duyệt hợp lệ áp đúng bản, đúng phạm vi và chống trùng khi retry | W3-10–11, W3-14, snapshot trước/sau và log receipt/outbox |
| **UF7 — Hiệu lực cài đặt** | Giữ Run đang chạy. Cấu hình mới có hiệu lực đúng thời điểm. Thay đổi ảnh hưởng lịch đi qua UF6, gồm cả nhánh từ chối hoặc đóng app | W3-12–13, snapshot Run, revisions cài đặt và lịch |
| **Đăng nhập Google** | APK cài mới đăng nhập được, phiên xử lý đúng khi mở lại/hết hạn/đăng xuất. Đổi tài khoản giữ riêng dữ liệu và kết nối Calendar. Từ chối quyền Calendar vẫn dùng được ProPlan | W3-16, ca kiểm hai tài khoản thật trên APK |
| **Calendar** | Sau khi cấp quyền đọc, UF1–UF2 dùng khoảng bận của lịch đã chọn. Dữ liệu bận có nguồn và phạm vi rõ. Kiểm quyền bị thu hồi và nhập giờ rảnh thủ công. Nếu đã cấp quyền ghi, sự kiện Focus sync sau khi duyệt, retry giữ nguyên số sự kiện và 412 được ghi nhận. Đề xuất chờ duyệt giữ Google Calendar nguyên trạng | W3-15, đối chiếu khung bận trên Google và lịch đề xuất, log outbox và sự kiện trên tài khoản thử |
| **Android và hồi quy UF1–UF4** | Timer, lịch cache và thu âm còn dùng được ngoại tuyến sau start. UF4 chỉ gọi STT khi người dùng chủ động tạo việc sau phiên. Kiểm chuông sai lệch ≤1 giây và cleanup DND ở các điều kiện máy thật đã chạy. Kiểm 1-Step UI, 3–5 tương tác chính, analyze và đo 60 FPS trên thao tác đã chọn | W3-18–20, log/mẫu đo và video. Báo tỷ lệ đạt quan sát được cùng số mẫu khi đối chiếu mục tiêu tin cậy 99,9% |
| **Dữ liệu và tích hợp** | Backend suite đạt trên SQLite và PostgreSQL staging theo phạm vi chốt, migration qua DB mới và DB cũ, xác nhận rollback hoặc phương án phục hồi. APK dùng hợp đồng API của đúng SHA | W3-04, W3-18, OpenAPI/JSON mẫu, log staging và commit |

**Kịch bản video chính** Đăng nhập Google → kết nối Google Calendar → nhập việc và xem đề xuất tránh một khung bận thật → xác nhận và chốt lịch → bắt đầu phiên → cất ghi âm khi đang làm → hết giờ và báo chưa xong → đóng/mở lại, xem bản dời lịch cập nhật rồi duyệt → đổi cài đặt cho phiên sau. Minh họa các nhánh từ chối quyền Calendar, Undo, không phản hồi, mất mạng và Calendar 412 bằng ca riêng để đối chiếu kết quả.

**Gói bàn giao ngày 16/11** Gồm APK và SHA-256, SHA commit trên `dev`, hướng dẫn cài/chạy và dữ liệu demo, hợp đồng API/JSON mẫu, báo cáo QA theo môi trường, video UF1–UF7 và danh sách phần chưa đạt/chưa kiểm hoặc đã dời. Thành xác nhận kết quả theo từng luồng và thiết bị đã kiểm.
