---
name: ui-team-design
description: Skill thiết kế UI hợp nhất cho B2b UI Designer. Nền chung (token/nhất quán/anti-slop/a11y) + 5 công cụ bổ sung, trục DESIGN.md. Dùng khi dựng tokens/component/mockup.
role: ui
layer: team
owner: <skill-owner>
version: 0.2.0
status: candidate
applies_when: {}
injects_into: "MODULE 3 / Additional guidance"
sources:
  - {name: hallmark,        author: Nutlope,          url: https://github.com/Nutlope/hallmark,                    role: "đa dạng cấu trúc + cổng slop"}
  - {name: taste-skill,     author: Leonxlnx,         url: https://github.com/Leonxlnx/taste-skill,                role: "thẩm mỹ (dials) + kiểm trình duyệt", license: MIT}
  - {name: awesome-design-md,author: VoltAgent,       url: https://github.com/VoltAgent/awesome-design-md,         role: "catalog DESIGN.md theo brand"}
  - {name: ui-ux-pro-max,   author: nextlevelbuilder, url: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill, role: "thư viện pattern/UX rule"}
  - {name: open-design,     author: nexu-io,          url: https://github.com/nexu-io/open-design,                 role: "workspace render/preview"}
note: "Diễn giải nguyên tắc, KHÔNG chép nguyên văn repo. Kiểm LICENSE từng repo trước khi dùng thương mại."
---

## Trục chung: DESIGN.md
Mỗi dự án có MỘT `DESIGN.md` (mẫu ở `data/templates/DESIGN.md`) — nguồn sự thật về thiết kế.
Mọi screen/component phải theo nó. 5 công cụ dưới đều feed vào file này, không làm riêng lẻ.

## A. Nền chung (áp LUÔN — nói một lần, không lặp giữa các skill)
- **Token-only**: màu/type/spacing/radius định nghĩa trong DESIGN.md + tokens.css; component chỉ tham chiếu token, không màu cứng inline.
- **Nhất quán**: mọi màn theo cùng DESIGN.md; không tự phát sinh phong cách rời.
- **Anti-generic**: tránh mẫu AI hay mắc (hero + 3 card generic, số bịa, hình minh hoạ chung chung, bo góc/đổ bóng đồng loạt).
- **A11y**: tương phản WCAG AA, focus order rõ, mọi trạng thái tương tác thấy được.
- **Audit-first khi redesign**: soi hiện trạng theo DESIGN.md trước khi đổi.

## B. Bổ sung — mỗi công cụ một vai (chỉ gọi khi task cần)
| Khi cần | Dùng | Đóng góp vào DESIGN.md |
|---|---|---|
| Đa dạng bố cục + cổng chống slop lúc emit | **hallmark** | macrostructure cho mỗi màn + 65-gate self-critique |
| Định hướng thẩm mỹ, kiểm trên trình duyệt, ảnh tham khảo | **taste-skill** | 3 dials (layout/motion/density) + distill reference |
| Chọn/áp/audit theo một brand có sẵn | **awesome-design-md** | DESIGN.md khởi điểm theo brand |
| Tra pattern/UX rule cụ thể (component, flow) | **ui-ux-pro-max** | pattern để điền component |
| Dựng & xem prototype thật | **open-design** | môi trường render/preview |

## Luồng gọn (simple)
1. Chọn/khởi tạo DESIGN.md (awesome-design-md nếu có brand mẫu; không thì từ template).
2. Taste: đặt 3 dials + distill ảnh tham khảo vào DESIGN.md.
3. hallmark: gán macrostructure từng màn (không lặp) + chạy slop-test.
4. ui-ux-pro-max: tra pattern khi cần component/flow cụ thể.
5. open-design: render preview để kiểm mắt + a11y trên trình duyệt.
6. Xuất `tokens_css` + `components` + `screens` + `design_md` vào vùng `ui_system`.

## Nhãn & giới hạn
Đề xuất visual do skill gợi ý là RECOMMENDATION — người UI duyệt. Chưa qua eval regression → candidate.
Chi tiết từng công cụ xem repo gốc (liệt kê ở examples/SOURCE.md); ở đây chỉ dùng NGUYÊN TẮC.
