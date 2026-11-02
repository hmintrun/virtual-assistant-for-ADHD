# Kế hoạch tuần 1 — Nền tảng và UF1–UF2

Cập nhật ngày 03/11/2026 theo Backend Docs thẻ 04/05 và code hiện tại. Tuần 1 là phạm vi công việc của nhóm; thẻ 04 còn dùng nhãn giai đoạn cũ “Tuần 4”. Trình tự dưới đây dùng Ngày 1–7, không tự đặt lại ngày hoặc hạn giao cho nhóm.

## 1. Mục tiêu và trạng thái hiện tại

**Mục tiêu:** User nhập công việc, thời gian và giờ rảnh trong một yêu cầu voice/text → review/xác nhận tóm tắt UF1 → nhận phương án UF2 → review → xác nhận lưu kế hoạch.

**Hiện tại:** UF1 Backend đã lưu/ghép/kiểm và xác nhận giờ riêng trong summary.availability. UF2 có lõi với nguồn cũ nhưng **chưa nối nguồn mới**; guard trả 409 PLAN_NOT_CONFIRMABLE/next_action=wait_for_configuration. Chưa nghiệm thu toàn luồng mới trên Mobile hoặc Google thật.

```text
Voice/text (công việc + thời gian + giờ rảnh)
 → lưu Draft → AI trích xuất/hỏi rõ → user sửa/xác nhận UF1
 → [I02 còn chờ nối nguồn giờ riêng + Context đúng Draft]
 → AI chia Parts/Steps → Algorithm tính/xếp → user review
 → confirm UF2 → lưu Task/Parts/Steps/Sessions/Version/Receipt
```

## 2. Nguồn tài liệu và quy tắc làm việc

- [01–05 Backend Docs](https://docs.google.com/document/d/1X1zIWny1nOWCfkS4zLEdn71uZLhYLu5OkHlUPKcdoxE/edit), [schema local](backend/schema.md), [cấu trúc Backend](backend/backend_structure.md), [môi trường](backend/backend_testing_setup.md).
- Mã P theo 05; mã A/I/V/C theo 04. Mỗi module có một role sở hữu; người thực hiện do nhóm phân công. Chưa có mã riêng thì không tự thêm mã mới.
- Cập nhật code dev vào nhánh phụ trách backend/mobile, rồi tạo nhánh công việc. Không đưa thay đổi Mobile vào commit Backend.
- Thay contract phải thống nhất trước khi code. Docs/Sheet online còn cần đồng bộ nguồn mới; không tự đổi endpoint trong từng nhánh.

## 3. Công việc và đầu ra tuần 1

| Mã | Role | Đầu ra cần bàn giao | Trạng thái/phần còn lại |
|---|---|---|---|
| P01/C01 | Backend Lead | Contract UF1–UF2: dữ liệu giờ rảnh, revision, lỗi, khóa nguồn, xác nhận và retry | Chốt nguồn Draft → Context → Candidate → Task; đồng bộ tài liệu/API Sheet và bên sử dụng. |
| P02 | Database | Model/migration, owner/FK/unique, tách Draft/Task và Session/Run | Nền đã có; review cách giữ nguồn giờ riêng khi I02 được nối. UF1 mới dùng JSON hiện có, chưa cần migration. |
| P03 | Backend | App, router, DB session, lỗi và môi trường cài lại từ lockfile | Đã có nền chạy được; giữ kiểm tra phù hợp, không dựng lại scaffold. |
| P04/V01 | Voice | Audio do user chủ động gửi → text/lỗi rõ | API có; cần kiểm mic/STT thật và bàn giao interface cho Mobile. Provider theo cấu hình hiện hành, không cố định Groq Whisper từ kế hoạch cũ. |
| P05/A01/A02.1 | AI + Prompt Engineer | Nội dung, mục tiêu, phạm vi, thời gian, giờ rảnh và câu hỏi đúng trường | Adapter/merge có; cần kiểm ngữ nghĩa tiếng Việt, thêm/thay/xóa giờ và nhiều lượt bằng provider thật. |
| P05/A04 | AI + Prompt Engineer | Parts chứa Steps, đầu ra/thứ tự/ước lượng gốc; giữ phần chưa chia | Còn ánh xạ một Step thành một Part; chưa hoàn thiện phạm vi chưa phân rã. |
| P06/A02.2/A05/A06/A08/A09/A10 | Algorithm | Kiểm đầu vào, quỹ rảnh, buffer một lần, chia/xếp Sessions và report đúng phạm vi | Có lõi; phải nhận đúng nguồn mới do Backend cấp. Không coi pass nguồn cũ là đã tích hợp nguồn mới. |
| P06/A07 | Algorithm | Thứ tự ổn định theo thời hạn, mức quan trọng và phụ thuộc trong phạm vi cho phép | Chưa đủ bộ ưu tiên; không dời Task đã chốt ngoài quyền. |
| A20 — thiết kế nghiên cứu | Algorithm | Nguồn bằng chứng, đề cương pilot, miền k và điều kiện lấy mẫu | Chưa đủ bằng chứng hoàn thành; không coi k=1.2 là kết luận khoa học. |
| A22.1 | Backend tích hợp Calendar | Kết nối/chọn lịch/quyền và lịch bận thật có nguồn/độ mới cho A06 | Chưa hoàn thiện adapter Google thật. Không gộp toàn bộ worker CRUD giai đoạn sau vào mục này. |
| P07/A03/I01 | Backend | Draft lưu/mở lại, merge, vấn đề làm rõ, revision và confirm UF1 | Đã sửa các lỗi issue, title dài, thời gian và giờ riêng; chưa đồng nghĩa giao diện đã nghiệm thu. |
| P07/I02 | Backend | Điều phối AI → Algorithm → Candidate, giữ nguồn và phần còn lại | Ưu tiên nối giờ riêng và Context, sau đó bỏ guard với đủ kiểm tra. |
| P07/A18 — confirm UF2 | Backend | Transaction nguyên bộ, kiểm lịch/revision và receipt chống trùng | Lõi nguồn cũ đã có; cần kiểm lại với nguồn mới sau I02. |
| P08 | Mobile | Nhập chung, hỏi rõ, sửa/xác nhận, bước 0, review lịch/phần chưa xếp | Cập nhật theo contract mới; kiểm thao tác cảm ứng và phục hồi trên thiết bị. Website test là công cụ riêng. |
| Kiểm chứng tuần 1 | QA | Tình huống, bằng chứng và bước tái hiện theo task được kiểm | Tách fake/nguồn thật; kiểm xuyên luồng mới sau I02, không tự tạo mã A ngoài thẻ 04. |

## 4. Trình tự thực hiện Ngày 1–7

Đây là trình tự phụ thuộc; không mô tả tất cả bước dưới đây là chưa bắt đầu hoặc đã hoàn thành.

| Mốc | Việc | Điều kiện chuyển tiếp |
|---|---|---|
| Ngày 1 | P01 chốt contract; P02/P03 xác nhận nền và dữ liệu mẫu | Hai phía đọc cùng schema; cài môi trường từ lockfile được. |
| Ngày 2 | Voice/AI/Algorithm/Mobile làm song song theo mẫu đã chốt | Có input/output, nhánh lỗi và người sở hữu module. |
| Ngày 3 | Ghép và kiểm UF1: giờ riêng, hỏi rõ, mở lại và confirm | User mới không cần Settings; không mất issue hoặc ghi đè chỉnh sửa. Phần Backend đã có bằng chứng nguồn giả. |
| Ngày 4 | I02 nối nguồn Draft và Context; A22.1 phát triển nguồn bận | Không trộn nguồn giữa hai Draft, không giả Google lỗi là lịch trống. |
| Ngày 5 | Hoàn thiện A04/A07 và ghép Candidate/confirm nguồn mới | Đủ phạm vi, lịch cụ thể hợp lệ, nguồn thay đổi bị phát hiện. |
| Ngày 6 | Hồi quy, tích hợp Mobile, kiểm voice/AI/Calendar thật riêng | Ghi môi trường/input/kết quả; không dùng mock để nghiệm thu dịch vụ thật. |
| Ngày 7 | Demo xuyên UF1–UF2, đối chiếu tồn đọng | Chỉ ghi Đạt cho phần có bằng chứng; nêu rõ nguồn hoặc tính năng còn giới hạn. |

## 5. Quy tắc nghiệp vụ cần giữ

1. **UF1:** Nhập công việc, đầu ra, thời gian và giờ rảnh trong cùng voice/text. Chưa rõ thì hỏi trên bảng tóm tắt; không tự gán giờ 08–18 hoặc buộc vào Settings trước.
2. **Giờ rảnh:** summary.availability theo yêu cầu hiện tại; ngày cụ thể thay quy tắc tuần của ngày đó. Giờ chung cũ không tự đè lên giờ riêng. Google chỉ bổ sung bận; chưa đọc Google không được hiểu là đã xác nhận không có bận.
3. **Hai lần xác nhận:** UF1 chỉ xác nhận đầu vào, chưa có Task. UF2 confirm mới lưu Task/Parts/Steps/Sessions. Không dùng endpoint schedule/confirm cũ thay endpoint candidates/{candidate_id}/confirm hiện hành.
4. **Chia việc:** User có thể nêu ý tưởng bước 0 hoặc để trợ lý đề xuất. AI trả đầu ra/ước lượng gốc, không tự tính lịch cuối cùng. Giữ phần chưa chia/chưa xếp.
5. **Thời lượng:** Buffer phần làm chính đúng một lần; không nhân nghỉ hoặc micro-task. Session gồm các quãng work/rest theo cấu hình effective, không chốt cứng 25/5 cho mọi user. Micro-task không cộng duration; hoàn thiện A12.0 thuộc giai đoạn UF3.
6. **Lịch:** Horizon chi tiết tối đa 14 ngày trong thời gian cho phép, không sửa deadline gốc. Giữ gap ít nhất 15 phút giữa hai Sessions kế tiếp, kể cả khác Task; không đòi xen Task khác.
7. **Khả thi:** Chỉ feasible khi có lịch cụ thể cho verification_scope. deficit_seconds là cận dưới thiếu thời lượng làm chính; 0 không bảo đảm xếp được. search_inconclusive không có nghĩa đã chứng minh vô nghiệm.
8. **Đồng thời/retry:** Nguồn/revision cũ phải được phát hiện; cùng mã/cùng payload không áp dụng trùng; cùng mã/khác payload bị từ chối. Mạng lỗi không có nghĩa chắc chắn thao tác chưa lưu.
9. **Lưu nháp:** Backend giữ Draft bền vững. Autosave/phục hồi local và trạng thái đồng bộ là phần giao diện cần kiểm thực tế, không chỉ gọi request khi đóng app rồi coi đã chắc chắn lưu.
10. **Phạm vi:** Không gộp I03 lập tiếp Task đã lưu, A12.0, timer/DND/Run, check-in hoặc toàn UF6–UF7 vào nghiệm thu tuần 1.

## 6. Tiêu chí kiểm chứng và nghiệm thu

- Nhập text/voice thật, kể cả ngày mơ hồ và nhiều khung; bổ sung mà không mất yêu cầu trước.
- Mở lại Draft đúng dữ liệu; sửa trường khác không xóa câu hỏi chưa giải quyết; hạn đã qua bị chặn nhưng nháp còn nguyên.
- Giữ/sửa giờ cũ đúng ý user; sửa sau confirm mất xác nhận cũ; Draft A không đổi giờ Draft B.
- Sau I02: user chưa có Settings đi được từ UF1 đến Candidate và confirm nguồn mới; không chọn nhầm Context.
- Part nhiều Steps, phần chưa chia không bị mất; AI lỗi không trả ba bước mẫu giả thành công. Chốt riêng đường chỉnh thủ công nếu AI lỗi khi chưa có Candidate.
- Lịch nằm trong giờ khả dụng, đúng thứ tự/thời hạn/gap; phân biệt thiếu quỹ với chưa tìm được lời giải.
- Confirm UF2 lưu nguyên bộ hoặc rollback; gửi lại không sinh trùng. Job pending không chứng minh Google đã có sự kiện.
- Kiểm Google/STT/AI thật và Mobile cảm ứng riêng; ghi provider/cấu hình/mạng/thiết bị cùng kết quả. Ngưỡng độ trễ phải đo và thống nhất, không giữ cam kết 2 giây chưa được kiểm chứng.
- Kiểm tra mới trong đợt này đặt ở `_temp` theo yêu cầu Quốc Hưng; không thêm vào thư mục tests chính. Giữ test đã có và không sửa cấu hình cảnh báo chỉ để làm báo cáo đẹp.

**Bằng chứng gần nhất:** Ngày 02/11/2026, 175 ca Backend/review đạt bằng DB tạm/provider giả; lint/type đạt. Chưa nghiệm thu toàn luồng nguồn mới, AI/STT/Google thật hoặc Mobile. Lượt cập nhật tài liệu 03/11 không chạy lại bộ test. Hệ quản trị PostgreSQL thật chưa được xác nhận chỉ từ các ca SQLite.

## 7. Bàn giao và Git

Mỗi role bàn giao module, DTO/ví dụ, nhánh lỗi và bằng chứng trong phạm vi mình sở hữu. Commit tiếng Anh theo [COMMIT_CONVENTION.md](COMMIT_CONVENTION.md); không commit .env, credential, DB, audio hoặc _temp. Backend và Mobile tách thay đổi; đưa về dev theo quy trình PR của nhóm. Không ghi đã cập nhật Docs/Sheet online nếu mới sửa file local.
