# 2026-11-04 — Sửa lỗi chạy thực tế ở account công ty (field fixes)

- Nhánh: `feat/v3-bot-team` · từ `59785eb` (481 test) → các commit của đợt (prefix `fix(...)`/`feat(...)`).
- Giao việc: Chief of Staff của chủ repo (Daipc) giao bot "Gỡ" — sửa 2 lỗi BACKEND thấy khi chạy dự án thật,
  model thật ở máy công ty (ảnh chụp từ máy công ty). KHÔNG sửa UI html (`room.html`/`index.html`) — bên công ty
  đã sửa UI, merge sau.

## Lỗi báo về (nguyên văn ý chính từ đề bài giao việc)
| Mã | Hiện tượng |
|---|---|
| A | Presales ESCALATED ngay ở A1: `a1_bu: SCHEMA_FAIL — 'business_context' is a required property [output 4735 ký tự, mở đầu bằng chữ, có '{': True, có ```: False, kết thúc bằng '}': True]`. Sau đó dự án kẹt ESCALATED, người không có cách gỡ. |
| B | Phòng 3D không thấy agent nào "đang làm" ở presales/discovery ("Agent 14 · 0 đang làm"), nhân vật không di chuyển theo bước thật. |

## Ranh giới (đề bài)
Không sửa room.html/index.html; không nới gate/guard; không cho máy gỡ escalate/ký; không auto-ký; không push/PR/remote;
không tự điền danh tính người. Được phép sửa phần MÔ TẢ output trong `specs/02_agents/*.md` cho khớp schema
(sửa lỗi lệch spec↔schema; không đổi luật/Forbidden).

## Mục 1 — runtime parse JSON chịu văn bản/fence + retry nêu khoá gốc (lỗi A)
**Nguyên nhân gốc:**
1. `runtime._extract_json` chỉ bóc code fence khi output BẮT ĐẦU bằng ```; văn bản dẫn trước JSON → `json.loads` lỗi.
2. Model không bao giờ thấy schema output: system prompt chỉ có câu tóm tắt ở MODULE 4. Với A1 tóm tắt
   `{business_context, domain, modules…}` đọc như "cả object là business_context" → model bỏ khoá gốc
   `business_context` (hoặc lồng các khoá khác vào trong nó).
3. Lệch spec↔schema ở nhiều agent: 9 agent (a1, a3, b1, b2, b4, c1, c2, c3, d1) được prompt bảo ghi
   `reflection.issues` trong khi schema `additionalProperties: false` không có `reflection` → model làm đúng
   prompt vẫn SCHEMA_FAIL. MODULE 4 của a2/c1/c2 mô tả khoá khác schema; c3/d1 bị cắt cụt ở dấu `|` (`{verdict: pass`).
4. Lượt retry chỉ nói "Output không hợp lệ: <str(ValidationError)>" — chuỗi dài gồm cả instance (raw output) và
   không nêu rõ khoá gốc nào bắt buộc.

| Đã đổi gì | File chính |
|---|---|
| `_extract_json(text, schema=None)`: quét mọi `{` bằng `json.JSONDecoder.raw_decode`, gom object cấp ngoài; có schema → object khớp schema xuất hiện cuối; không có → object lớn nhất (hoà → cái sau). Không eval; không tự bọc/bỏ bọc khoá gốc. Huddle dùng chung hàm (tương thích) | `src/agents/runtime.py` |
| System prompt = prompt spec + khối `OUTPUT JSON SCHEMA (specs/04_schemas/<id>.out.json)` nguyên văn + danh sách khoá gốc bắt buộc (Code chèn sau Module 4; không phải knowledge, không đụng Module 1/2/4/Forbidden) | `src/agents/runtime.py` |
| Retry: thông báo lỗi = message + vị trí; khoá gốc BẮT BUỘC; thiếu ở cấp gốc; khoá thừa (schema cấm); "phải nằm ở CẤP GỐC, không lồng trong X"; yêu cầu "CHỈ MỘT object JSON… không văn bản trước/sau, không code fence" | `src/agents/runtime.py` |
| Số lượt retry từ `config/runtime.yaml → agent_runtime.schema_retries` (mặc định giữ như cũ = 1); `run(max_retries=None)` đọc config | `config/runtime.yaml`, `src/agents/runtime.py` |
| Lý do SCHEMA_FAIL (vào risk_register khi escalate) = message + hình dạng output (số ký tự, mở đầu bằng chữ, có `{`, có ```, kết thúc `}`) — KHÔNG có raw output; kết quả lỗi thêm `attempts` | `src/agents/runtime.py` |
| Spec (chỉ phần mô tả output, cho KHỚP schema): a1 Card Output + MODULE 4 (nêu rõ 6 khoá gốc anh em, `business_context` là object bắt buộc ở cấp gốc); a2 MODULE 4 (draft WBS `{id,item,role,size,module?}` + `reflection`, không trả effort/cost — Card giữ vì mô tả estimate sau Code enrich); c1/c2 Card + MODULE 4 theo schema; c3/d1 sửa tóm tắt bị cắt cụt; 9 agent không có `reflection` trong schema: câu Reflection đổi thành "fix any failure… schema has NO `reflection` field: do not add it". Forbidden/Done when/luật không đổi | `specs/02_agents/{a1_bu,a2_estimation,a3_proposal,b1_ba,b2_ux,b4_tech_lead,c1_dev,c2_tester,c3_qc,d1_soc}.md` |
| Test cũ `test_factory_interviewer_uses_spec_prompt_and_routing`: so system == prompt → đổi thành system bắt đầu bằng prompt spec + có khối schema | `tests/contract/test_factory.py` |

Test mới (`tests/contract/test_runtime_extract.py`, 18): văn bản + JSON, JSON + văn bản, ```json giữa đoạn, fence
không ngôn ngữ, ngoặc lẻ; ưu tiên object khớp schema; không eval / dict Python bị từ chối; không bọc/bỏ bọc;
thiếu khoá gốc → retry nêu `business_context`; bọc → retry nói CẤP GỐC; khoá thừa `reflection`; reason không chứa
raw output; số retry từ config; system có schema; mọi MODULE 4 có tóm tắt nêu đủ khoá gốc bắt buộc; không spec nào
đòi `reflection.issues` khi schema cấm.

Số test sau mục 1: **499**.

## Việc còn mở
- (cập nhật ở cuối đợt)
