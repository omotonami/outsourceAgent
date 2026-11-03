# outsourcing-agents

Spec-driven multi-agent framework cho outsourcing delivery (SG).

- `specs/` người viết · `knowledge/` kinh nghiệm người dùng · `src/` code · `data/ eval/ tests/`.
- Kiến trúc: `specs/00_overview.md` và `CLAUDE.md`. Hai vòng học: `docs/training-loops.md`.

## Chạy
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q                       # test offline (mock), không cần API key
export ANTHROPIC_API_KEY=sk-... # để chạy agent thật
```

## Hai vòng training
- Gate feedback (`src/feedback/`): người duyệt sửa ở gate → đề xuất skill personal (cần xác nhận).
- Reconcile (`src/reconcile/`): đóng dự án → ghi actual vào historical DB → lần sau item thành FACT.
- Effort là DB-driven: `src/agents/a2_estimation.enrich` tra `src/store/historical`, không để LLM đặt số.

## Đẩy lên GitHub
```bash
# tạo repo rỗng trên github.com rồi:
git remote add origin https://github.com/<user>/outsourcing-agents.git
git branch -M main
git push -u origin main
# hoặc dùng GitHub CLI:
gh repo create outsourcing-agents --private --source=. --push
```

## Dashboard trực quan
```bash
uvicorn src.server.app:app --reload   # mở http://localhost:8000
```
Data flow Blackboard · routing · gate duyệt · token/chi phí. Chi tiết: docs/dashboard.md
