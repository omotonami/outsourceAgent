# Dashboard trực quan (FastAPI + engine Python thật)

Hiển thị data flow qua Blackboard, model routing, gate duyệt/trả lại, token & chi phí — số thật từ engine.

## Chạy
```bash
source .venv/bin/activate
pip install -r requirements.txt          # có fastapi, uvicorn
uvicorn src.server.app:app --reload
```
Mở http://localhost:8000

- **▶ Chạy / Từng bước / Reset**: điều khiển pipeline.
- **Blackboard**: vùng sáng khi được ghi; viền đậm = đang đọc, teal = đang ghi.
- **Model routing**: mỗi bước hiện tier (Haiku/Sonnet/Opus) + lý do, lấy từ `router.pick_tier` thật.
- **Gate**: tới G1..G4 pipeline dừng, bấm Duyệt/Trả lại. Trả lại → backtrack về đầu phase, huỷ chữ ký.
- **Token & chi phí**: tích luỹ theo tier (giá minh hoạ).
- Ô **Task build high-risk**: đổi routing Dev/QC giữa Opus và Sonnet để thấy logic.

## Mock vs thật
Mặc định mock (không tốn token). Gọi model thật: sửa `src/server/app.py` để gọi `src/agents/*` 
với `ANTHROPIC_API_KEY`, thay số token mô phỏng bằng `usage` thật từ `eval/runs/trace.jsonl`.
