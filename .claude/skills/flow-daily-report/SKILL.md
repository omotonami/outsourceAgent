---
name: flow-daily-report
description: Báo cáo tiến độ hằng ngày các dự án FLOW từ event store (phase, task, gate chờ, risk High/Critical, BLOCKED/escalate, token & chi phí, hoạt động 24h, việc cần người quyết). Dùng khi được hỏi "báo cáo hôm nay", "tiến độ dự án", hoặc cần bản tóm tắt mang ra ngoài account công ty (--redact).
---

# Báo cáo tiến độ hằng ngày

1. Bản nội bộ (ở lại account công ty):
   `python -m src.ops.report daily --all --out docs/reports/$(date +%F).md`
   hoặc một dự án: `python -m src.ops.report daily --id P-...`
   `docs/reports/` đã gitignore — **không commit, không dán ra ngoài**.
2. Bản mang ra ngoài (chỉ khi người cần chia sẻ ra khỏi account công ty):
   `python -m src.ops.report daily --all --redact --out docs/reports/$(date +%F)-redact.md`
   Bản này chỉ có mã project + trạng thái + số liệu. Trước khi đưa ra ngoài: đọc lại file, nếu thấy tên client,
   nội dung brief/spec, đường dẫn code → **không đưa**, báo người. Chỉ bản `--redact` được ra ngoài.
3. Tóm tắt cho người theo khuôn:
   - Đã xong: … · Kết quả chính: (state từng dự án, token/chi phí) · Cần người quyết / duyệt: (copy mục
     "Việc cần người quyết").
4. Không tự quyết thay người các mục trong "Việc cần người quyết" (ký gate, ESCALATED, đặt budget, Clarify).
   Gate: người ký qua dashboard (tab Gate dự án) hoặc tự gõ `python -m src.ops.gate ...`; Claude không chạy.

Chi phí trong báo cáo là ƯỚC TÍNH theo `config/pricing.yaml`, không phải hoá đơn.
Báo cáo không đổi repo → không cần ghi docs/history.
