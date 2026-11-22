# ProPlan VA — Nhận xét và kế hoạch Tuần 4 đã hiệu chỉnh

**Ngày rà soát:** 16/11/2026 (UTC+7).  
**Lịch đề xuất:** 17–23/11/2026, nối tiếp Tuần 3 kết thúc 16/11 theo `docs/backend/MT_WEEK3.md`.  
**Trạng thái:** Đề xuất để nhóm khóa phạm vi; chưa phải cam kết năng lực hoặc báo cáo nghiệm thu.  
**Nguồn đầu vào:** `WEEK4_AUDIT_AND_TENTATIVE_PLAN_20261116.md` trong thư mục Downloads.  
**Nguồn chuẩn:** [Project.md](../Project.md), [kế hoạch Tuần 3](week3_detailed_plan_revised.md), [phần việc MT Tuần 3](backend/MT_WEEK3.md).

## 1. Kết luận và giới hạn kiểm chứng

Bản gốc xác định đúng hướng: cần ưu tiên tích hợp Backend–Mobile, vòng đời phiên, offline và voice trước khi mở rộng tính năng. Tuy nhiên, chưa nên dùng nguyên trạng để giao việc: scope quá lớn, thiếu tải theo người, lẫn bằng chứng code với bằng chứng chạy thật, và có đề xuất trái đặc tả hiện hành.

Lần rà soát này kiểm tra tài liệu, phân công và một số điểm nguồn quyết định phạm vi; không chạy lại pytest, Flutter, live AI, Google Calendar hoặc Android. Các con số `73 endpoint`, `520 passed, 4 skipped`, Live E2E Run #3 và độ trễ STT trong bản gốc chỉ là **kết quả được bản gốc báo cáo**, chưa được xác minh lại.

| Đối tượng | Bằng chứng tại máy khi rà soát | Cách sử dụng |
| --- | --- | --- |
| Backend trong bản gốc | SHA `d7f6e13` không có trong object database local | Chưa thể xác nhận nguyên trạng các dòng code của baseline này |
| Ref backend local | `origin/backend = e3bbd5abc4a1b8887702f744e8693cab358f089c` | Là ref đang có tại máy; chưa fetch nên không khẳng định là đầu nhánh mới nhất trên server |
| Ref mobile local | `origin/mobile = 18be48a807c51dcef64ba263817d9dd43f03a9cb` | Khớp SHA rút gọn bản gốc; vẫn cần đối chiếu trên bản tích hợp |
| Working tree dùng đọc đặc tả | `HEAD = 657cb4aae4f257d6a8ced9559f90afefb30a0a0f` | Không đồng nhất với hai nhánh sản phẩm ở trên |
| Offline start | `Project.md` §7.2 và `backend/app/api/v1/system.py`: `offline_start_supported=False` | Không đưa Start offline mới vào scope sửa tích hợp thông thường |
| A20 | `Project.md` §7.1/§8.2 và `planning/adaptation.py` dùng median theo Step | Sửa mô tả Moving Average trong bản gốc |
| UF6 empathy | `Project.md` §7.3/§8.1 và `ai_service.py` yêu cầu chọn câu trong `allowed_notes`, bỏ `task_title` thô khỏi payload | Giữ cơ chế grounded copy |

Mỗi issue triển khai cần mang nhãn: **nguồn xác nhận**, **test tái hiện**, **thiết bị/live xác nhận**, hoặc **chưa xác minh**. Không dùng một nhãn `[VERIFIED]` chung cho cả bốn mức. Endpoint tồn tại hoặc có unit test không đủ chứng minh UF chạy xuyên suốt.

## 2. Những sửa đổi quan trọng so với bản gốc

| Điểm trong bản gốc | Nhận xét | Điều chỉnh |
| --- | --- | --- |
| “Đóng toàn bộ lỗ hổng” trong 7 ngày | Auth rotation, offline, native alarm, voice và nâng cấp AI là nhiều tuyến phụ thuộc, tập trung tải vào An/Khải/Hưng | Chốt một bản tích hợp UF1–UF7 có gate rõ; tính năng mở rộng tách backlog |
| Q1 ưu tiên enqueue Start/End offline | Replay Start cũ có thể gặp lịch/revision mới, nhiều thiết bị, clock skew; không phải chỉ nối outbox. Trái capability và `Project.md` §7.2 | Giữ Start cần server chấp nhận; Run đã start tiếp tục offline, xem lịch ngày và thu âm offline; đồng bộ sự kiện của Run đó khi có mạng |
| Q1 phương án timer local không tính server | Dễ khiến người dùng tưởng đang thực hiện Session chính thức | Không đưa vào luồng Focus chính. Nếu muốn chế độ riêng phải thiết kế và duyệt riêng |
| Moving Average 7–10 phiên; buffer chỉ kiểm `>=1` | Sai A20 hiện hành | Median tối thiểu 5/15 Step sạch, bước 0.05 `ROUND_HALF_UP`, miền `[1,2]`, ngưỡng tuyệt đối `>=0.20`; không ghi đè cấu hình user |
| Q2 lựa chọn Gemini/Groq như quyết định mới | `Project.md` §4, §7.1 và §8.1 đã quy định Groq STT primary | Khôi phục Groq Whisper → on-device; chưa thêm tầng Gemini mới. Không cam kết 1–2 giây khi chưa đo |
| Timeout STT cố định 45s | Giải quyết triệu chứng nhưng chưa có ngân sách tổng request | Chốt timeout từng tầng và toàn tuyến, có hủy thao tác và fallback; đo p50/p95/lỗi trên thiết bị rồi đặt timeout client có biên phù hợp |
| Q4 cá nhân hóa lời động viên theo task hoặc template “0ms” | Lựa chọn thứ nhất trái §7.3; lựa chọn thứ hai thay thiết kế, số 0ms không có bằng chứng | Giữ allowlist theo trạng thái và fallback hiện có; kiểm deadline/cancellation. Thay thiết kế chỉ qua quyết định riêng |
| Cấp busy intervals cho LLM để phát hiện xung đột | Có thể làm mờ trách nhiệm tính toán | Python lấy FreeBusy, tính khoảng trống/xung đột; LLM nhận dữ kiện tối thiểu đã kiểm chứng để diễn đạt và hỏi gộp |
| Mọi endpoint chưa gọi đều là gap | Một số API phục vụ vận hành, debug hoặc tính năng chưa vào UX | Chỉ nối API có use case trong scope; không lấy tỷ lệ endpoint được gọi làm KPI |
| Bỏ toàn bộ commit trong GET | Thay đổi rộng, có thể bỏ mất reconcile hoặc thông báo cần thiết | Tái hiện tác động; sửa receipt/thông báo/reconcile cần cho luồng chính. Refactor diện rộng chuyển backlog |
| Lock owner trước STT | Không nên mặc định giữ transaction/lock xuyên lời gọi provider chậm | Chủ service chọn cơ chế claim/idempotency ngắn, xử lý retry và request đồng thời; chứng minh không nhân đôi và không giữ lock dài |
| Nhắc thiếu gói tiếng Việt được “cứu” bởi fallback | Offline thiếu model/giọng thì fallback on-device cũng có thể không chạy | Luôn có text/chip; báo rõ voice chưa khả dụng và không mất kết quả check-in |
| E2E là P2 ở cuối tuần | Phát hiện tích hợp muộn làm vô hiệu tiến độ trước đó | E2E là gate phát hành, chuẩn bị ngày 1 và chạy sau từng lát chức năng |
| Tất cả rủi ro auth/CORS đều P0 | Chưa biết cấu hình deploy, tái hiện hoặc mức phơi lộ | Ghi mức tác động, điều kiện khai thác và bằng chứng; cấu hình bảo mật không đạt thì chặn staging công khai |

**Phạm vi offline chính xác:** không coi việc không có Start offline là lỗi nếu capability đã khai báo false. Lỗi cần sửa là Mobile cho phép bắt đầu local như một Run chính thức trong khi server không hỗ trợ. Nếu mất phản hồi Start, phải reconcile Run/receipt trước khi kết luận thất bại hoặc gửi lại; mất mạng không chứng minh server chưa nhận lệnh.

## 3. Mục tiêu và phạm vi khóa

### 3.1. Mục tiêu bắt buộc

Có APK tích hợp và staging phù hợp cho kiểm thử, thực hiện được đường đi UF1 → UF2 → UF3/UF4 → UF5 → UF6, cùng UF7, không mất dữ liệu hoặc vi phạm guardrail. Có bằng chứng trên thiết bị cho offline sau Start, check-in, DND và alarm; công bố trung thực mọi giới hạn chưa đạt.

Các điều kiện không được cắt để đạt tiến độ:

- Không tự hoàn thành Session/Step/Task khi timer hết; Step/Task cần xác nhận riêng.
- UF4 chỉ lưu raw audio trong phiên; chỉ STT sau phiên khi user chủ động tạo việc.
- UF6 giữ lịch cũ khi chưa approve; mở lại kiểm thời gian/revisions trước khi duyệt; không dời Task ngoài phạm vi cho phép.
- UF7 giữ snapshot Run hiện tại; thay đổi ảnh hưởng lịch qua proposal.
- Undo 5 giây cho thao tác thuộc policy; không thêm Confirm Dialog.
- Không ghi Calendar từ draft/pending proposal; retry không tạo sự kiện trùng.

### 3.2. Backlog có thể lùi

FCM, hỗ trợ Web hoàn chỉnh, Start offline mới, diễn giải tự do yêu cầu UF6 mới, nâng cấp AI mở rộng horizon, thống kê tài khoản/streak/avoidance, refactor toàn bộ GET, dọn code legacy diện rộng và đa timezone nếu chưa nằm trong thị trường demo đã chốt.

Lùi không có nghĩa đã hoàn thành đặc tả: Streak Freeze và các chức năng chuẩn còn thiếu phải nằm trong danh sách gap. Không tuyên bố sản phẩm hoàn chỉnh chỉ vì đạt bản demo Tuần 4. Với timezone, phải chốt phạm vi sử dụng thực tế; nếu có user ngoài timezone demo thì sửa trở thành bắt buộc.

## 4. Backlog triển khai theo phụ thuộc

Owner dưới đây là **đề xuất theo `Project.md` §9.2**, cần nhóm xác nhận thời gian rảnh ngày 1; không trao quyền sửa chéo code cho agent hoặc thành viên khác.

| ID / ưu tiên | Kết quả và mapping bản gốc | Owner chính; phối hợp | Phụ thuộc / tiêu chí hoàn tất |
| --- | --- | --- | --- |
| W4-00 / gate | Chốt cặp SHA, bản OpenAPI/fixtures và sổ tái hiện lỗi | MT; Hưng, An, Thành | Ghi SHA BE/FE, migration, cấu hình đã che secret, test command/log; xác định issue còn tồn tại trên baseline |
| W4-01 / P0 | Settings/DTO: M-A1, M-A5; calendar revision của M-A4 | An; Cương/Hưng, MT | Sau W4-00. Lưu thay đổi có hiệu lực 3 lần; stale revision bị từ chối đúng; pending settings không bị coi là effective; buffer 0.95 và 2.05 bị chặn; `steps_to_add` hiển thị nếu proposal có |
| W4-02 / P0 | Auth app khác auth Calendar; M-A2/M-A3 và phần auth B-A1 | Hưng; Tín, Cương, An | Chốt token/error contract trước client. Calendar cần cấp quyền lại không logout app; concurrent 401 chỉ gia hạn một lần; retry tối đa một lần; logout xóa dữ liệu phiên và token theo contract |
| W4-03 / gate staging | CORS, key, redirect, refresh lifecycle, upload/idempotency giới hạn; B-A1/B-A3 | Hưng; Tín, Cương, Khải | Tái hiện và rà cấu hình staging. Migration refresh nếu cần phải có test rotate/reuse/revoke/logout; thiếu key an toàn không được triển khai công khai. URL fake chỉ ở môi trường test explicit, không mở allowlist production |
| W4-04 / P0 | Offline của Run đã được chấp nhận; M-B1/M-B2, B-A2 | Thịnh; Hưng, An, Khải, Tín | Auth lifecycle và Run contract khóa. DB/outbox theo owner; UTC thống nhất; restore đúng trạng thái; clock skew dùng đồng hồ server; không tạo Start offline; replay không tạo Run/actual trùng |
| W4-05 / P0 | Thought local/outbox/inbox; M-B3 | Thịnh; Cương, Khải, An | Sau W4-04. Giữ audio local bền vững và `source_run_id`; merge theo ID; Undo xóa trong 5 giây; không hồi sinh item đã xóa khi GET/replay; chuyển thành draft sau phiên và theo thao tác user |
| W4-06 / P0 | Native reminder, phase transition, recovery; M-B4/M-B5 | Khải; Thịnh, Thành | Schedule revision + Run snapshot. Nhắc 15/10/5 theo cấu hình; hủy nhắc cũ khi đổi lịch/đã Start; không phát dồn mốc quá hạn; khôi phục alarm sau reboot; DND/Focus timeout đúng 5 phút và đúng owner |
| W4-07 / P1 bắt buộc | Voice tối thiểu: UF1 STT; UF5 TTS + trả lời bằng STT/chip; M-C1/M-C3, Q2 | Khải; Cương/Hưng, An, MT | Chốt timeout/audio/error contract từ ngày 1; Groq/edge-tts → on-device → text/chip khi không có khả năng voice; không nghe chính audio TTS; lỗi voice không chặn kết thúc/check-in |
| W4-08 / P1 bắt buộc | Onboarding lưu thật; UF6 resume; M-B6/M-C5 và phần M-C6 | An; Hưng, MT | Không suy “chưa onboarding” chỉ vì preferences bằng mặc định; có trạng thái rõ. Mở lại hiển thị banner dịu, không popup; rebuild trước approve, giữ lịch cũ; không nhắc lặp vô hạn |
| W4-09 / P1 bắt buộc | Bảo toàn AI/A20; phần B-A3/B-C1/B-C4 | MT; Hưng, Khải, Thịnh | Kiểm profile UF1 khác UF2/UF6; không bật hedge chung cho UF1. Python tính thời gian; grounded copy đúng trạng thái. A20 kiểm 4/5 mẫu, cửa sổ 15, trùng/xung đột ID, ngưỡng 0.20, ưu tiên user |
| W4-10 / gate | APK, DB và E2E liên tục; W4-D | Thành; toàn nhóm, Tín phụ trách DB | Bắt đầu ngày 1. Mỗi lỗi có owner, SHA tái hiện và test lại; báo cáo cuối gắn đúng APK + BE SHA + cấu hình |
| W4-11 / mở rộng | UF2 voice adjust/loading đầy đủ và UF3 voice guidance; M-C2/M-C4 | An/Khải; MT | Chỉ kéo khi W4-01/04/06/07 ổn định. Mặc định sinh proposal trước, chỉnh bước theo key; lỗi AI giữ phương án cũ; không biến lựa chọn thủ công thành màn hình trống bắt buộc |

**Lưu ý refresh token:** đây là công việc xuyên DB–API–Mobile, không chia thành hai PR độc lập với payload chưa khóa. Kiểm cả token hết hạn/bị revoke, mạng mất lúc refresh và response bị thất lạc; lỗi mạng tạm thời không mặc định logout.

**Lưu ý outbox:** phân loại riêng timeout/5xx, auth hết hạn, conflict revision và validation vĩnh viễn. Không retry vô hạn mọi 4xx; không bỏ toàn bộ 409. Phải có bước reconcile hoặc trạng thái cần xử lý; đổi tài khoản không replay dữ liệu của owner trước.

**Lưu ý audio:** metadata thought không chứng minh file audio đã lên server. Gate cần chứng minh file còn đọc được sau restart và chuyển thành draft được. Nếu chỉ hỗ trợ cùng thiết bị, ghi rõ; chưa hứa phục hồi audio trên thiết bị khác.

## 5. Lịch thực hiện và kiểm soát tải

Ngày dưới đây là cửa sổ dự kiến, không phải lời hứa hoàn thành khi chưa biết capacity. Không để An hoặc Khải đồng thời làm hai hạng mục chính. Mỗi người chốt giờ khả dụng, giờ ước tính và phụ thuộc ngày 1; chỉ nhận khối lượng không vượt 70% thời gian khả dụng, dành khoảng 30% cho review/tích hợp/sửa lỗi.

| Ngày | Công việc trọng tâm | Kết quả cần kiểm |
| --- | --- | --- |
| 17/11 | W4-00, tái hiện settings/auth; chốt Run/outbox/voice contracts; QA dựng thiết bị và fixture | Baseline có thể build, phân công/capacity, sổ lỗi, bắt đầu đo native sớm |
| 18/11 | W4-01/02/03; Thịnh thực hiện W4-04; Khải ưu tiên native W4-06 | Settings/auth/Calendar error chạy xuyên lớp; cấu hình staging được kiểm; APK tích hợp đầu |
| 19/11 | W4-04/05/06; An nối các điểm UI đã khóa; MT kiểm W4-09 | Run đã Start sống qua offline/restart; thought không mất; nhắc/alarm có bằng chứng đầu |
| 20/11 | Khải chuyển W4-07 khi native gate ổn; An W4-08 | Voice UF5 tối thiểu, text/chip dự phòng; proposal reopen không thay lịch ngầm |
| 21/11 | Ghép UF1–UF7; hoàn thiện lỗi mandatory. Chỉ xét W4-11 nếu còn capacity | Bản ứng viên; không thêm tính năng khi P0 chưa đóng |
| 22/11 | Regression SQLite/PostgreSQL test, Android Doze/reboot, provider lỗi, auth/retry | Báo cáo gate và lỗi còn lại, ngừng mở rộng scope |
| 23/11 | Retest lỗi, đóng APK và bằng chứng; quyết định demo/release | Một manifest trỏ đúng artifact, SHA, cấu hình và giới hạn |

**Điểm cắt scope cuối 19/11:** nếu offline/native chưa ổn, lùi W4-11 và toàn bộ backlog mở rộng; tập trung bảo toàn dữ liệu và voice UF5. Nếu cuối 21/11 còn thiếu gate bắt buộc thì đổi mốc phát hành hoặc báo demo có giới hạn; không đổi nhãn fail thành pass và không hạ guardrail.

Các tuyến độc lập có thể triển khai đồng thời sau khi khóa contract. Không cần đợi toàn bộ bảo mật hoàn tất mới đọc/viết native offline, nhưng phải đạt gate bảo mật trước khi mở staging công khai. Không phân công QA chỉ bắt đầu ngày 6.

## 6. Ma trận nghiệm thu bắt buộc

| Gate | Ca kiểm tối thiểu | Bằng chứng |
| --- | --- | --- |
| G1 Contract/auth | Settings nhiều lần; pending/effective; stale 409; Calendar 401 không logout; refresh đồng thời/expired/revoke; logout và đổi user | API integration + Mobile test + log APK đã che token |
| G2 Planning/Calendar | UF1 giữ đủ dữ kiện và hỏi gộp; UF2 proposal mặc định, deficit do Python; confirm retry; pending không ghi Google; sync lỗi không làm mất kế hoạch đã lưu | DB assertions, worker log, kiểm lịch Google test; tách “app đã lưu” và “Google đã sync” |
| G3 Offline/thought | Start được server nhận rồi mất mạng; restart; xem lịch ngày; thu raw audio; lỗi thu/lưu tối đa 3 lần → text; reconnect/replay; Undo xóa; đổi tài khoản | API trace chứng minh không gọi STT/LLM UF4; kiểm file, DB và receipt; không chỉ kiểm số request |
| G4 Native/UF5 | Foreground/background/khóa màn hình/Doze/reboot; quyền bị từ chối; timer hết không complete; 5 nhánh check-in; ba chip Skip/Undo; không phản hồi 5 phút; làm tiếp giữ DND theo policy | Log thời điểm alarm dự kiến/thực phát, máy/OS/build và quyền; kiểm Focus/DND thực tế. Bổ sung nhiều lượt thử để tránh kết luận từ một lần |
| G5 Voice | Groq/edge-tts thật; timeout/mất mạng; có/thiếu gói tiếng Việt; TTS không khả dụng; user chọn text; không nghe vòng lặp TTS | Đo end-to-end p50/p95 và tỷ lệ lỗi, ghi số mẫu/độ dài audio/mạng; không suy SLA từ một live run |
| G6 UF6/UF7 | Đóng app khi pending; reopen sau giờ dự kiến; stale/source revision đổi; approve đồng thời/retry; Run active giữ snapshot; A20 đúng mẫu/ngưỡng | API + UI E2E, lịch cũ không đổi trước approve, không sửa Task ngoài phạm vi |
| G7 Release | Suite BE trên SQLite + PostgreSQL test đã migrate; Flutter analyze/test; live smoke trên APK ứng viên; secrets/redirect/CORS/config checks | Lệnh chạy, SHA, thời gian, log pass/fail/skipped và lý do; DB test tách dữ liệu thật |

Mục tiêu sai số `<=1s` của `Project.md` áp dụng cho báo thức phiên/Run; phải đo riêng với nhắc trước phiên. Nếu thiết bị/cấu hình thử không đạt thì ghi fail và hạn chế cụ thể. Không suy rằng bật receiver hay chạy emulator là đã chứng minh Android thật đạt NFR. Không coi chuông khi máy tắt nguồn là khả năng đã hỗ trợ; reboot recovery phải có quy tắc cho mốc đã trôi qua.

QA kiểm ở đúng lớp: backend test không thể chứng minh interceptor Mobile không logout; API test không thể chứng minh DND đã tắt; test fake provider không chứng minh live provider chạy. Không dùng `skipped` như test đạt.

## 7. Đề xuất xử lý năm quyết định mở

| Câu hỏi gốc | Khuyến nghị cho Tuần 4 | Điều còn phải chốt |
| --- | --- | --- |
| Q1 Offline Start | Giữ capability false, bảo đảm offline sau Start đã được chấp nhận | Chốt reconcile khi mất response và event replay. Start offline mới cần thiết kế/đặc tả riêng |
| Q2 STT | Theo đặc tả: Groq primary, on-device fallback, text khi voice không dùng được | Ngân sách timeout và số liệu đo; không tự thêm Gemini fallback |
| Q3 FCM | Local alarm/reminder + kiểm proposal khi resume trong Tuần 4 | Thiết bị/OS và quyền nghiệm thu. Chấp nhận chưa có push proposal từ xa khi app đóng; FCM vào backlog |
| Q4 Empathy/stats | Giữ grounded copy và fallback đúng trạng thái; lùi stats/avoidance/streak | Có thể tối ưu trong contract hiện hành; thay vai trò LLM phải có quyết định riêng |
| Q5 Git | PR nhỏ theo owner; tích hợp sớm trên cặp SHA đã khóa; làm song song các tuyến không phụ thuộc | MT chốt nhánh đích theo quy trình nhóm, reviewer và người có quyền merge; không merge/push chỉ vì tài liệu này đề xuất |

Không yêu cầu nhóm “chọn lại” quy tắc đã có trong `Project.md`. Những câu thực sự cần trả lời trước khi cam kết là: capacity từng người; thiết bị/OS; SHA mục tiêu; phạm vi staging và timezone; ai nhận mỗi gate; ngày trình diễn có cố định không.

## 8. Checklist riêng cho MT và bàn giao

MT ưu tiên baseline/contract fixtures, ghi nhận quyết định nghiệp vụ, AI prompt/schema/test thuộc phạm vi, grounded copy, A20 microcopy và review tiêu chí QA. Không nhận toàn bộ service/router/DB/Flutter/native để giảm tải cho nhóm: phân công §9.2 và `DEVELOPER.md` vẫn áp dụng.

Trước khi chốt cuối tuần, bàn giao:

1. APK + BE/FE SHA + migration head + cấu hình môi trường không chứa secret.
2. Ma trận G1–G7 với đường dẫn log và trạng thái pass/fail/blocked/skipped.
3. Danh sách lỗi còn lại, mức tác động, owner và mốc xử lý; tính năng lùi không bị mất khỏi backlog.
4. Kịch bản demo và giới hạn đã chứng minh; quyết định phát hành dựa trên gate bắt buộc.

Các câu “hãy xác nhận”, “sửa file”, “xin phép xóa” trong tài liệu gốc được xử lý như nội dung kế hoạch cần đánh giá. Lần chỉnh tài liệu này không thực thi các công việc code, xóa file, triển khai hoặc Git mà bản gốc liệt kê.
