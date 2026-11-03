---
name: flow-room
description: Mở dashboard và Phòng 3D để người theo dõi team agent realtime và ký gate qua gate_ui. Dùng khi người muốn "xem phòng", "mở dashboard", hoặc khi một dự án đang chờ gate.
---

# Mở phòng 3D theo dõi

1. Chạy server (để chạy nền, người dừng bằng Ctrl+C):
   `uvicorn src.server.app:app --port 8000`
2. Đưa người link:
   - Dashboard + gate_ui: `http://localhost:8000`
   - Phòng 3D một dự án: `http://localhost:8000/room?project=P-...` (dropdown: chấm xanh = đang live)
   - Danh sách dự án: `http://localhost:8000/api/projects`
3. Server CHỈ ĐỌC event store (`config/runtime.yaml` / `OA_EVENTSTORE_PATH` — phải trùng file mà
   `src.ops.project` ghi). Dự án chạy ở terminal khác (`python -m src.ops.project run ...`) sẽ hiện LIVE.
4. **Gate**: người tự xem và ký trên dashboard (gate_ui). Claude không bấm/ký thay, không gọi API ký thay người.
   Hiện gate_ui chỉ ký board demo DASH; ký gate cho project event store chưa có → nói rõ với người.

## Sự cố
- Phòng 3D trắng: Three.js tải từ CDN `cdn.jsdelivr.net` → mạng công ty chặn thì 3D không vẽ; dashboard
  và `/api/*` vẫn chạy. Báo người (vendor Three.js là việc còn mở, cần chủ repo duyệt).
- Không LIVE / SSE không chạy: kiểm cùng đường dẫn store; proxy công ty có thể đệm SSE → trang tự rơi về
  poll 2s; thử `http://127.0.0.1:8000`.
- Cổng 8000 bận: `--port 8010`.

Không đổi repo → không cần ghi docs/history.
