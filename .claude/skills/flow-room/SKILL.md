---
name: flow-room
description: Mở dashboard và Phòng 3D để người theo dõi team agent realtime và ký gate qua gate_ui. Dùng khi người muốn "xem phòng", "mở dashboard", hoặc khi một dự án đang chờ gate.
---

# Mở phòng 3D theo dõi

1. Chạy server (để chạy nền, người dừng bằng Ctrl+C):
   `uvicorn src.server.app:app --port 8000`
2. Đưa người link:
   - **Control Room (1 trang)**: `http://localhost:8000/` — Phòng 3D tất cả dự án + tab Chat CoS · Việc · Gate · Sự cố · Token
     (`docs/control-room.md`). Chạy nền mọi dự án: `python -m src.ops.scheduler run` (terminal khác).
   - Phòng 3D một dự án: `http://localhost:8000/room?project=P-...` · dashboard cũ: `/legacy`
   - Danh sách dự án: `http://localhost:8000/api/projects`
3. Server ĐỌC event store (ghi chỉ khi NGƯỜI gõ tên + XAC NHAN: ký gate, gỡ sự cố, tạo dự án, giao việc phụ) (`config/runtime.yaml` / `OA_EVENTSTORE_PATH` — phải trùng file mà
   `src.ops.project` ghi). Dự án chạy ở terminal khác (`python -m src.ops.project run ...`) sẽ hiện LIVE.
4. **Gate**: Control Room tab "Gate" (`http://localhost:8000/?tab=gate&project=P-...`) — người xem dữ liệu trình gate, gõ tên
   mình + `XAC NHAN`, Duyệt / Trả lại. ESCALATED: tab "Sự cố" (người bấm Retry / Quay về / Huỷ). Phòng 3D hiện gate
   đang chờ (thanh vàng). **Claude không bấm/ký thay, không gọi `POST /api/projects/*/gate`** (curl/http/WebFetch
   đều deny), không gõ tên/`XAC NHAN` thay. Đường thứ hai: người tự gõ `python -m src.ops.gate sign|reject ...`
   trong terminal của họ.

## Sự cố
- Phòng 3D trắng: Three.js tải từ CDN `cdn.jsdelivr.net` → mạng công ty chặn thì 3D không vẽ; dashboard
  và `/api/*` vẫn chạy. Báo người (vendor Three.js là việc còn mở, cần chủ repo duyệt).
- Không LIVE / SSE không chạy: kiểm cùng đường dẫn store; proxy công ty có thể đệm SSE → trang tự rơi về
  poll 2s; thử `http://127.0.0.1:8000`.
- Cổng 8000 bận: `--port 8010`.

Không đổi repo → không cần ghi docs/history.
