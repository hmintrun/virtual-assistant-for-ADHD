# Quy Chuẩn Commit Dành Cho Toàn Đội Ngũ (ProPlan VA)

Tài liệu này là quy tắc bắt buộc áp dụng cho mọi thành viên (`mt`, `vqh`, `kc`, `tn`, Mobile Lead) khi đẩy code lên repository.

---

## 1. Cấu Trúc Bắt Buộc

Mọi commit phải tuân thủ định dạng:

```text
<type>(<scope>): <hành động dạng mệnh lệnh>

[Thân commit: Giải thích lý do WHY và cách giải quyết WHAT]
[Tối đa 72 ký tự mỗi dòng]

[Footer: Mã Issue hoặc Breaking Change nếu có]
```

---

## 2. Bảng Phân Loại `<type>`

Chỉ sử dụng 6 loại sau:

| Type | Khi nào dùng? | Ví dụ |
| :--- | :--- | :--- |
| **`feat`** | Thêm tính năng, API hoặc màn hình mới | `feat(uf1): add single-round task extraction` |
| **`fix`** | Sửa lỗi logic, lỗi hiển thị hoặc lệch giờ | `fix(uf5): stop dnd auto-toggle after 5min timeout` |
| **`prompt`** | Sửa System Prompt, JSON Schema cho Gemini/Groq | `prompt(uf2-ai): restrict step decomposition schema` |
| **`refactor`**| Tối ưu code, cấu trúc lại thư mục (không đổi tính năng) | `refactor(scheduler): optimize rolling horizon loop` |
| **`test`** | Thêm unit test, widget test | `test(uf3): add visual timer widget test` |
| **`docs`** | Cập nhật tài liệu, ERD, Flowchart, hướng dẫn | `docs: add commit convention guideline` |

---

## 3. Danh Mục Phạm Vi `<scope>` Theo 7 Luồng Nghiệp Vụ

Gắn đúng mã luồng hoặc phân hệ để dễ truy vết lịch sử:

- **7 Luồng chức năng:** `uf1`, `uf2`, `uf3`, `uf4`, `uf5`, `uf6`, `uf7`.
- **Phân hệ kỹ thuật:** `backend`, `mobile`, `db`, `scheduler`, `auth`, `mcp`.
- **Kết hợp:** `uf1-ai`, `uf2-scheduler`, `uf3-timer`, `uf4-audio`, `uf5-dnd`, `uf6-reschedule`.

---

## 4. 5 Quy Tắc Viết Tiêu Đề (Header Rules)

1. **Toàn bộ chữ thường:** Viết `feat`, không viết `Feat` hay `FEAT`.
2. **Dùng động từ mệnh lệnh tiếng Anh:** Dùng `add`, `fix`, `update`, `remove`; cấm dùng `added`, `fixes`, `updating`.
3. **Không dấu chấm cuối câu:** Tiêu đề không có dấu chấm `.`.
4. **Độ dài dưới 50 ký tự:** Tuyệt đối không vượt quá 72 ký tự để tránh bị GitHub cắt ngắn `...`.
5. **Công thức kiểm tra:** Đọc thử câu: *"If applied, this commit will `<tiêu đề của bạn>`"*. Nếu hợp lý là đúng.

---

## 5. Danh Mục CẤM Tuyệt Đối Khi Commit

- **CẤM** commit file cấu hình và bí mật: `.env`, `.env.local`, API Keys (Gemini, Groq), file JSON Google Service Account.
- **CẤM** commit file database cục bộ: `*.db`, `*.sqlite3`.
- **CẤM** commit file âm thanh thử nghiệm: `*.m4a`, `*.wav`, `*.mp3`.
- **CẤM** gộp nhiều tính năng khác nhau vào 1 commit (Mỗi commit chỉ làm 1 việc duy nhất).
