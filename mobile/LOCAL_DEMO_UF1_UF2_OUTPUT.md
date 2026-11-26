# Demo local · Đối chiếu đầu ra UF1–UF2

## Khởi tạo database riêng

Từ thư mục gốc repo, dùng môi trường Python đã cài của backend để migrate và chạy API local. Database nằm tại `mobile/.local-demo/uf1_uf2.db` và được Git bỏ qua.

```powershell
powershell -ExecutionPolicy Bypass -File mobile/Start-UfOutputDemo.ps1
```

Chỉ tạo DB và in revision bằng tham số `-MigrateOnly`. API dev tự tạo dữ liệu người dùng phát triển ở request đầu tiên cần người dùng. Mở terminal khác ở thư mục gốc để chạy UI.

```powershell
cd mobile
flutter run -d chrome --web-port=8081 --dart-define=API_BASE_URL=http://127.0.0.1:8011/api/v1
```

Chrome dùng cổng riêng để tách dữ liệu trình duyệt của demo. Với Android Emulator, chọn ID từ `flutter devices` và đổi URL thành `http://10.0.2.2:8011/api/v1`.

## Dữ liệu nhập thử

### Test AI chia việc sau khi merge dev

Mở [ProPlan VA local](http://127.0.0.1:8082/#/home), bật Network → Preserve log và tạo nháp mới cho mỗi ca. Trên màn voice, ô chữ hiện sau ba lần nhận diện thất bại. Giữ nguyên bộ chọn hạn cho các ca nhập hạn trong câu. Các mốc tương đối tính theo giờ Việt Nam tại lúc chạy.

| Ca | Câu để dán | Kết quả mong đợi |
|---|---|---|
| AI-01 · Đủ thông tin | Soạn báo cáo khảo sát. Đầu ra là PDF 3 trang có một biểu đồ. Bắt đầu 08:00 ngày mai, hạn 18:00 ngày kia. Tôi rảnh ngày mai 08:00–17:00. | UF1 nhận đúng phạm vi, hạn và giờ rảnh, cho tạo phương án. UF2 trả ít nhất một bước, mỗi bước có tiêu đề và đầu ra cụ thể. Tổng thể các bước hướng tới PDF có biểu đồ. |
| AI-02 · Chỉ nêu hạn | Soạn báo cáo khảo sát. Đầu ra là PDF 3 trang có một biểu đồ. Hạn 18:00 ngày kia. Tôi rảnh ngày mai 08:00–17:00. | Kiểm tra hồi quy lỗi cũ. Hạn phải được giữ hoặc yêu cầu làm rõ chỉ ra mốc còn thiếu. Nếu response mất cả hạn đã nêu, ghi lỗi UF1 và lưu response. |
| AI-03 · Bỏ đầu ra | Làm báo cáo khảo sát. Bắt đầu 08:00 ngày mai, hạn 18:00 ngày kia. Tôi rảnh ngày mai 08:00–17:00. | Nếu backend báo thiếu scope, UI chỉ hỏi phần còn thiếu và giữ thời gian đã nhận. Bổ sung “PDF 3 trang có một biểu đồ” rồi tạo phương án. Nếu AI tự điền scope, ghi giá trị API trả để phân biệt với nhánh blocker. |
| AI-04 · Giờ rảnh sau hạn | Soạn báo cáo PDF 3 trang. Bắt đầu 08:00 ngày mai, hạn 12:00 ngày mai. Tôi chỉ rảnh ngày mai 14:00–17:00. | API báo xung đột hoặc phương án không khả thi. UI không cho chốt phiên sau hạn. |
| AI-05 · Kiểm tra riêng UF2 | Dùng AI-01. Nếu UF1 không nhận đủ, chọn hạn và giờ rảnh bằng bộ chọn, nhập phạm vi “PDF 3 trang có một biểu đồ”, rồi tạo phương án. | Có thể kiểm tra tách việc riêng khi UF1 lỗi. Ghi rõ đã điền tay. Đối chiếu số bước, thứ tự, tiêu đề và `expected_output` với cùng candidate và revision. |

Ghi riêng **UF1**, **UF2** và **đồng bộ UI–API** cho mỗi lượt. Nếu UF1 chưa cho tạo phương án, ghi UF2 là **Blocked**. Nếu log báo fallback, đánh dấu rõ lượt đó dùng dự phòng. Khi so UI với console, dùng response của chính lần tạo phương án hoặc lệnh GET bên dưới với cùng ID và revision.

### Kiểm thử màn nhập bằng giọng nói

Mở **Thêm việc mới** với một bản nháp trống. Dùng Chrome có quyền mic và hỗ trợ nhận diện tiếng Việt, hoặc Android thật, cho các ca nhận diện thành công. Với ca lỗi, có thể chặn quyền mic hoặc dùng thiết bị không có nhận diện tiếng Việt. Mỗi lần thử bắt đầu bằng một lần bấm mic và kết thúc khi app nhận được câu hoặc báo lỗi.

| ID | Thao tác | Kết quả mong đợi |
|---|---|---|
| VOICE-01 | Mở **Thêm việc mới**. | Trong vùng nhập chỉ có nút mic, chưa có ô nhập chữ. Khi đang nghe, nút đổi thành nút dừng. |
| VOICE-02 | Tạo hai lần nhận diện thất bại liên tiếp. | Sau mỗi lần chỉ hiện một thông báo, lần lượt là 1/3 và 2/3. Mic trở về trạng thái sẵn sàng. Ô nhập chữ vẫn ẩn. |
| VOICE-03 | Tiếp tục tạo lỗi lần thứ ba. | App mở ô nhập chữ. Gõ nội dung và bấm **Phân tích công việc** thì gửi được qua luồng UF1. |
| VOICE-04 | Cho mic thất bại một hoặc hai lần, rồi nói một câu rõ và đợi nhận diện xong. Sau đó tạo thêm một lần lỗi. | App hiện câu vừa nhận diện, có nút phân tích và lưu nháp. Ô nhập chữ vẫn ẩn. Lần lỗi kế tiếp tính lại từ 1/3. |
| VOICE-05 | Bấm mic rồi dừng khi chưa nói. Thử thêm một lượt có lời nói rồi bấm dừng. | Lượt không có nội dung tính một lần lỗi. Lượt có nội dung nhận diện được hiện câu để xem lại. Nút mic không bị kẹt ở trạng thái đang nghe. |
| VOICE-06 | Sau khi nhận diện, nói hoặc dùng một câu có đầu ra, hạn và giờ rảnh. Bấm **Phân tích công việc**. | Trong Network, `raw_input` khớp câu app hiển thị sau nhận diện. Tóm tắt khớp response analyze. Nếu API trả blocker thì UI hiện mục cần bổ sung. Khi tạo được phương án, dùng SYNC-01 để so đầu ra UI với console. |

Ghi **Pass / Fail / Blocked** kèm thiết bị, trình duyệt và ảnh. Nếu trình duyệt không có nhận diện tiếng Việt, vẫn chạy các ca lỗi và chuyển các ca nhận diện thành công sang thiết bị hỗ trợ.

Tạo bản nháp mới cho từng ca. Chọn giờ rảnh còn ở tương lai, chẳng hạn ngày mai 08:00–17:00, và hạn chót lúc 18:00 ngày kia theo giờ Việt Nam. Không chốt lịch giữa các ca để tránh làm thay đổi quỹ giờ rảnh.

| Ca | Dữ liệu / thao tác | Kết quả phải có |
|---|---|---|
| OUT-01 · Đủ đầu ra | Nhập “Soạn báo cáo khảo sát. Đầu ra là PDF 3 trang, gồm tóm tắt và một biểu đồ.” Thêm giờ rảnh và hạn. Bấm **Tạo phương án**. | Tóm tắt hiện phạm vi API trả về. API có `can_plan=true` sau khi đủ dữ liệu. Màn Kế hoạch đề xuất hiện đúng danh sách bước và đầu ra của từng bước trong candidate. |
| OUT-02 · Bỏ đầu ra ở mô tả | Nhập “Làm báo cáo khảo sát”, vẫn có giờ rảnh và hạn. Bỏ trống ô Phạm vi. | Nếu API thực sự trả phạm vi trống, kiểm tra `MISSING_SCOPE`, `field_path=scope`, `blocking=true` và `can_plan=false`. UI hiện câu hỏi bổ sung phạm vi và chưa mở Kế hoạch đề xuất. Nếu API đã điền phạm vi từ mô tả, UI phải hiện đúng giá trị đó. Không dùng kết quả này làm bằng chứng cho nhánh phạm vi trống. |
| OUT-03 · Bổ sung đầu ra | Từ OUT-02 đang bị chặn, điền “PDF 3 trang, có một biểu đồ” vào Phạm vi rồi gửi lại. | API lưu phạm vi mới. Khi đã đủ tất cả trường bắt buộc, `can_plan=true`, blocker phạm vi biến mất và UI mở Kế hoạch đề xuất. Các bước lấy từ candidate mới, không lấy từ bản nháp trước. |
| OUT-04 · Xóa đầu ra một bước | Trong Kế hoạch đề xuất, sửa một bước. Thử để trống rồi thử chỉ nhập dấu cách vào ô kết quả, giữ tên và số phút hợp lệ. | UI báo cần tên, kết quả và số phút hợp lệ. Không có PATCH thành công. Mở lại bước thấy đầu ra cũ. GET candidate vẫn trả giá trị cũ và revision không đổi. |
| OUT-05 · Sửa đầu ra hợp lệ | Sửa đầu ra của cùng bước thành “Bản PDF có biểu đồ đã kiểm tra” rồi lưu. | PATCH thành công, giá trị trên thẻ và ô sửa khớp `expected_output` của đúng `client_step_key` trong revision mới. |
| OUT-06 · AI trả bước thiếu đầu ra | Chỉ áp dụng khi console/log backend thực sự ghi nhận lỗi AI thiếu đầu ra và chuyển sang Python dự phòng. | Đối chiếu candidate cuối mà API gửi. Nếu API tạo candidate thành công, các bước dự phòng có đầu ra và UI hiển thị đúng dữ liệu đó. Nếu không tái hiện được log này, đánh dấu **Blocked** cho ca này. |

## Đối chiếu với console

### Ca chính · SYNC-01

**Câu dùng cho lượt chuẩn:** “Soạn báo cáo khảo sát. Đầu ra là PDF 3 trang có một biểu đồ. Bắt đầu lúc 08:00 ngày mai, hạn 18:00 ngày kia. Tôi rảnh ngày mai từ 08:00 đến 17:00.”

**Chuẩn bị ô chữ:** Mở nháp mới, chặn quyền mic hoặc bấm dừng sau khi mic đã bật mà chưa nói. Đợi mỗi lượt kết thúc rồi mới bấm lại. Lượt 1 và 2 chỉ báo lỗi; lượt 3 mở màn nhập bằng chữ. Nếu đã nhận được giọng nói, bộ đếm về 0 và UI hiện câu nhận diện để phân tích.

1. Dán câu chuẩn ở trên và để trống bộ chọn hạn. Nếu API cần bổ sung giờ rảnh, chọn khung giờ trước hạn và ghi lại thao tác đó. Dùng ca AI-02 riêng để thử câu chỉ có hạn mà không có giờ bắt đầu.
2. Trong DevTools → Network, kiểm tra `POST /drafts/{id}/analyze`. `raw_input` phải còn nguyên câu. Hạn ở `data.summary.time_constraint` và hạn trên Tóm tắt phải cùng là 18:00 ngày kia theo giờ Việt Nam. Phạm vi trên UI phải khớp `data.summary.scope`.
3. Bấm **Tạo phương án**. Lưu response `POST /drafts/{id}/candidates` và ghi mã cùng revision trên màn **Kế hoạch đề xuất**. Dùng lệnh PowerShell bên dưới để lấy lại chính phương án đó.
4. Với từng bước, ghi một dòng theo mẫu `thứ tự | client_step_key | tiêu đề API | đầu ra API | đầu ra UI | Pass/Fail`. Ghép thẻ UI theo thứ tự và tiêu đề của response. So cả số bước và chữ trong từng đầu ra, kể cả khi hai bước có tiêu đề giống nhau.

| Ca đồng bộ | Thao tác | Kết quả mong đợi |
|---|---|---|
| SYNC-01A · Lần chia đầu | So response tạo phương án với màn Kế hoạch đề xuất. | Cùng ID và revision; mọi thẻ giữ đúng thứ tự, tiêu đề và `expected_output`. Khác xuống dòng do chiều rộng màn hình vẫn đạt. |
| SYNC-01B · Sửa một bước | Đổi đầu ra một bước thành “Bản PDF 3 trang đã kiểm tra”, rồi lấy lại candidate. | Thẻ đúng `client_step_key` hiện đầu ra mới và revision khớp console. Các thẻ còn lại hiển thị theo response mới. |
| SYNC-01C · Đầu ra bỏ trống | Xóa đầu ra hoặc chỉ nhập dấu cách khi sửa bước. | UI chặn lưu, candidate và revision cũ giữ nguyên. |

**Pass** khi hạn và phạm vi khớp, cùng mã và revision, và mọi thẻ bước khớp dữ liệu API. Không đặt trước câu đầu ra mà AI phải viết. Nếu API trả đúng nhưng UI sai, ghi **Fail UI** và giữ response cùng ảnh thẻ bước. Nếu request đúng nhưng API thiếu dữ liệu, ghi **Fail API** và xem log NLP/fallback. Nếu chưa tạo được phương án do môi trường hoặc quota, ghi **Blocked** kèm lỗi.

Chạy lại SYNC-01 với câu không nêu đầu ra để kiểm tra nhánh OUT-02. Nếu backend chặn vì thiếu phạm vi, kết quả đúng là UI hỏi bổ sung và chưa có candidate. Khi đã bổ sung và tạo phương án, so đầu ra theo candidate mới.

### Deadline nhập ngay trong câu

Tạo bản nháp mới cho từng ca. Dùng ngày tương lai theo giờ Việt Nam và chọn giờ rảnh trước hạn nếu cần. Với DEAD-01 đến DEAD-03, để trống bộ chọn hạn ở màn nhập việc.

| Ca | Nhập ở ô văn bản | Kết quả mong đợi |
|---|---|---|
| DEAD-01 | “Soạn báo cáo khảo sát, đầu ra PDF 3 trang có biểu đồ, hạn 18:00 ngày 15/12/2026.” Thay ngày nếu đã qua. | Request analyze giữ nguyên câu. Khi API trả mốc 18:00 đúng ngày, UI hiện đúng hạn và không hỏi lại. Nếu API không trích được mốc rõ, ghi Fail ở bước phân tích. |
| DEAD-02 | “Soạn báo cáo khảo sát, đầu ra PDF 3 trang, hạn 18:00 ngày mai.” | Quy đổi “ngày mai” theo giờ Việt Nam tại lúc chạy. So ngày và giờ ở response và UI, nhất là khi chạy gần nửa đêm. |
| DEAD-03 | “Soạn báo cáo khảo sát, đầu ra PDF 3 trang, hạn vào cuối tuần.” | Ghi lại mốc cụ thể API trả hoặc blocker thời gian nếu API cần làm rõ. UI phải hiển thị đúng trạng thái đó. |
| DEAD-04 | Dùng câu DEAD-01 rồi chọn thủ công một hạn khác. | Kiểm tra mốc gửi trong `summary.time_constraint`. UI theo mốc cuối API xác nhận. Nếu API báo xung đột, hiển thị yêu cầu làm rõ trước khi tạo phương án. |

Đối chiếu ba điểm theo thứ tự:

1. Request analyze thiếu phần deadline trong `raw_input` so với câu đã nhập → lỗi gửi dữ liệu phía mobile.
2. Request đúng nhưng response analyze thiếu hoặc trả sai `data.summary.time_constraint.deadline_at` hoặc `finish_by_at` → kiểm tra trích xuất NLP, log fallback và cách backend hợp nhất kết quả. Nếu log NLP đúng nhưng response sai, lỗi nằm sau bước NLP.
3. Response đúng mà hạn trên UI sai → lỗi ánh xạ hoặc hiển thị mobile. Sau khi tạo phương án, tiếp tục so `expected_output` theo cùng `candidate_id`, `review_revision` và `client_step_key`. Ghi riêng kết quả hạn chót và kết quả đầu ra.

Ca hồi quy mobile `candidate_output_test.dart` dùng JSON giả lập với đầu ra hợp lệ, thiếu key, null, chuỗi rỗng và chỉ có dấu cách. Với dữ liệu lỗi, thẻ bước hiện **Chưa có kết quả đầu ra**. Backend hiện yêu cầu đầu ra hợp lệ, nên các payload lỗi này được kiểm tra riêng trong widget test.

Chạy từ `mobile/` bằng `flutter test test/candidate_output_test.dart test/task_summary_required_fields_test.dart`.

1. Trên Chrome mở DevTools → Network, bật **Preserve log**. Với OUT-02, xem response `POST /drafts/{id}/analyze` để đối chiếu `data.summary.scope`, `can_plan` và `clarification_issues`.
2. Với ca đã tạo phương án, lấy `candidate_id` từ response `POST /drafts/{id}/candidates`. Mã và revision cũng hiện trên màn Kế hoạch đề xuất.
3. Đọc lại đúng candidate từ PowerShell. Sau khi sửa, chạy lại lệnh để lấy revision mới.

```powershell
$candidateId = 'DAN_ID_TU_RESPONSE'
$candidate = (Invoke-RestMethod "http://127.0.0.1:8011/api/v1/candidates/$candidateId").data
$candidate | Select-Object candidate_id, review_revision, decomposition_state
$candidate.steps | Sort-Object order_index |
    Select-Object order_index, client_step_key, title, expected_output |
    ConvertTo-Json -Depth 4
```

Một ca đồng bộ đạt khi UI và console dùng cùng `candidate_id` và `review_revision`, khớp số bước, thứ tự, tiêu đề và đầu ra. Câu AI sinh có thể khác giữa hai lần chạy, vì vậy hãy so dữ liệu của cùng một lần tạo phương án. Ghi **Pass / Fail / Blocked** kèm ID, revision và ảnh UI cho từng ca.
