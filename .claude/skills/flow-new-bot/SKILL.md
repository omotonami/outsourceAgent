---
name: flow-new-bot
description: Tạo agent/bot mới cho FLOW bằng Agent Factory (src/factory). Dùng khi người muốn thêm một bot chuyên môn mới. Claude chỉ soạn nháp và kiểm; NGƯỜI xác nhận và ghi trong terminal tương tác.
---

# Tạo bot mới bằng Factory

Luật 10: danh tính người chỉ vào qua terminal tương tác do chính người gõ. Claude **không** điền
`--confirmed-by`, `confirmed_by`, `approved_by`, `reviewer`, và **không** gõ `XAC NHAN` thay người.

1. Làm rõ với người (≤7 câu, `docs/factory.md`): id `<chữ><số>_<role>`, việc duy nhất + done_when,
   anti_jobs/forbidden (≥1), input (+untrusted), output + zone, tools, phase/trigger/risk/tier.
   Đề xuất theo khuôn: Tên bot · Một việc duy nhất · Anti-job · Output kỳ vọng.
2. Soạn `data/runtime/factory/<id>.yaml` (answers, các khoá như `ANSWERS` trong
   `tests/contract/test_factory.py`). Mọi nội dung Claude soạn là RECOMMENDATION — người sửa/duyệt.
   Không đặt số budget/rate mới (luật 6: số nằm ở `config/`, `data/`).
3. **DỪNG — người tự chạy trong terminal của họ** (Claude đưa lệnh, không chạy thay):
   ```
   python -m src.factory.new --spec data/runtime/factory/<id>.yaml --confirmed-by <tên người>   # dry-run
   python -m src.factory.new --spec data/runtime/factory/<id>.yaml --write --create-branch     # người gõ tên + "XAC NHAN"
   ```
   `--write` bắt TTY; Claude chạy sẽ bị từ chối — đúng thiết kế, không tìm cách lách.
4. Sau khi người báo đã ghi (nhánh `factory/<id>`): Claude chạy
   `python -m src.factory.agent_lint <id>` và `python -m pytest -q`, báo kết quả.
5. Ghi `docs/history/` (file đợt + dòng mục lục; ghi rõ người nào xác nhận theo lời người, không tự điền)
   rồi `python -m src.tools.checks.history_check --base <ref>`. Commit cục bộ khi người đồng ý. Không push/PR.
   Agent mới mặc định L0; không chạy thật trước khi người duyệt.
