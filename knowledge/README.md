# knowledge/ — kinh nghiệm người dùng thêm + hệ học được

Bốn lớp skill, thứ tự nạp: personal → learned → team → _core (core cuối, hiệu lực cao nhất).

| Lớp | Nguồn | Ai tạo | Trạng thái |
|---|---|---|---|
| `_core` (ở specs) | Luật | admin | khoá |
| `team/` | Kinh nghiệm đã duyệt (gồm skill base internet đã vet) | người, qua review | dùng |
| `personal/<user>/` | Cá nhân tự nhập | từng người | dùng |
| `learned/` | **Hệ tự rút từ revision_log** | distiller | proposed → eval → promote lên team |

## Lớp learned (mới)
- Mọi lượt user sửa output được ghi vào Blackboard `revision_log` (src/learning/revision.py).
- Reflection đọc log NGAY TRONG PHIÊN để không bắt user sửa cùng lỗi nhiều lần.
- `distiller` (src/learning/distiller.py) đếm pattern sửa lặp ≥ 3 lần → ghi `learned/PROPOSED.md`.
- PROPOSED **chưa dùng**: phải qua eval regression + người duyệt mới đổi tên thành `learned/SKILL.md`.
- Phân biệt `kind`: 'edit' (agent sai → học) vs 'preference' (user đổi ý → nhiễu, bỏ qua).

Ba điều bắt buộc (mọi lớp): chỉ THÊM vào Module 3, không nới Forbidden/Output; con số → data/; không chứa dữ liệu client thật.
