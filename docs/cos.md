# Chief of Staff (H5) — một cửa cho người

Đề án `docs/proposals/0002-bot-team-v3.md` mục H5. Agent `e2_cos` (role `cos`) sinh bằng Factory
(`zone: none`, L0). ID có số vì Factory bắt dạng `<chữ><số>_<role>`; E2 = cạnh E1 PM.

## Làm gì
- `e2_cos.ask(board, request, ctx)` → `{understanding, clarifications[], plan[], assignments[], decisions_needed[]}`
  (= Hiểu việc · Cần làm rõ · Kế hoạch · Phân công · Cần quyết định).
- Đọc: `project_status`, risk còn mở, `decision_records`, `trace_log` qua Team feed (chỉ summary, ≤ 20 event).
- Code kiểm: `assignments[].agent` phải có spec, không phải `gate_ui`/chính cos → sai = `FORBIDDEN`.

## Quyền (enforce bằng Code + Blackboard)
| Hành động | Kết quả |
|---|---|
| ký / thu hồi gate | `PermissionDenied` (CosBoard; `e2_cos` là danh tính máy với `Blackboard.sign`) |
| ghi/append bất kỳ zone | `PermissionDenied` (CosBoard read-only; `e2_cos` không là writer zone nào) |
| `lead_brief` mới | chỉ khi `confirm(..., confirmed_by=<người>)`, chưa có lead_brief; ghi bằng writer `intake` + schema |
| `clarify_answers` | chỉ khi đã xác nhận và câu hỏi đang `clarify.pending`; ghi qua `clarify.resolve` |

Xác nhận dùng cơ chế chung `src/governance/confirm.py` (cùng Factory, Autonomy, `Blackboard.sign`):
`confirmed_by` phải là người (`require_human` — không phải writer zone, agent có spec, hệ thống, `gate_ui`, `auto*`),
`payload_hash` khớp — sửa payload sau khi xác nhận → bị chặn. Gate vẫn chỉ qua `gate_ui`.
`CosBoard` = `ReadOnlyBoard` (`src/blackboard/proxy.py`): không giữ tham chiếu board thật ở thuộc tính.

## Figma → B3 ("làm 5% đầu")
1. Người làm **design system** (style màu/chữ/bóng có tên) + **1 màn mẫu** trên Figma.
2. `FIGMA_TOKEN` (chỉ đọc) → `figma.b3_input(FigmaClient(), file_key, sample_frame_id, design_spec)`.
3. Ra `figma_tokens {tokens, tokens_css}` (đúng dạng `ui_system`) + `sample_screen` → input `b3_ui`.
4. B3 dùng token + cấu trúc màn mẫu để nhân ra các màn còn lại; G2 người duyệt như cũ.
Adapter chỉ GET (`src/tools/integrations/figma.py`), transport inject được; test không gọi mạng.

## Không làm
Không ký gate, không L3, không nối Huddle vào state machine, không ghi zone sản phẩm, không đổi mục tiêu.
