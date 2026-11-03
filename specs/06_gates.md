# Human gates G1–G4

| Gate | Ai duyệt | Chặn gì | Màn hình hiện gì trước |
|---|---|---|---|
| G1 Quote/Proposal | Sales Lead | Gửi giá/proposal ra client | ASSUMPTION, WBS lệch > 30%, exclusions, skill đã dùng |
| G2 Scope | Client + PM | Bắt đầu build | scope_delta, open_questions còn mở |
| G3 Merge | Tech Lead | Merge vào nhánh chính | Important còn mở, file ngoài scope, dependency mới |
| G4 Release | Tech Lead + bảo mật | Release ra client | finding High/Critical, QC Critical, UAT |

Chữ ký gate chỉ ghi qua `src/gate_ui` (Blackboard.sign). Từ chối → Orchestrator.backtrack().
