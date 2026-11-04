# outsourcing-agents

Spec-driven multi-agent framework cho outsourcing delivery (SG).

- `specs/` người viết (nguồn sự thật) · `knowledge/` kinh nghiệm người dùng · `src/` code · `config/ data/ eval/ tests/`.
- Kiến trúc: `CLAUDE.md` (10 luật), `specs/00_overview.md`, `docs/SPEC.md` (workflow, Blackboard, bot team v3, cấu trúc `src/`).
- Bàn giao context: `docs/HANDOFF.md`. Quyết định kiến trúc: `docs/adr/`. Lịch sử thay đổi + quyết định của chủ repo: [`docs/history/`](docs/history/README.md).

## Vận hành hằng ngày (account công ty)
Xem [`START_HERE.md`](START_HERE.md) · CLI `python -m src.ops.project|report|prune|doctor` · skills `.claude/skills/` · runbook `docs/ops/runbook-daily.md`.

## Chạy
```bash
python -m venv .venv && source .venv/bin/activate      # Python ≥ 3.10
pip install -r requirements.txt
pytest -q                                   # 545 test offline (mock), không cần API key
python -m src.factory.agent_lint --all      # 14/14 agent đạt chuẩn Card v5 / schema / routing / zone
python -m src.tools.checks.history_check --base main   # luật 9: đổi src/ specs/ config/ phải có docs/history/ (CI)
python -m src.demo.dry_run                  # cả 4 phase với LLM mock
export ANTHROPIC_API_KEY=sk-...             # để chạy agent thật (python -m src.demo.live_one)
```

## Thành phần
| Lớp | Ở đâu | Tài liệu |
|---|---|---|
| 14 agent (a1…d1, e1_pm, e2_cos, factory_interviewer) | `specs/02_agents/` + `src/agents/` | `docs/TOOL_GUIDE.md` |
| Orchestrator (state machine, guard, gate) | `src/orchestrator/` | `docs/SPEC.md` §3 |
| Blackboard (zone + quyền + proxy) | `src/blackboard/` ← `specs/05_blackboard.yaml` | `docs/SPEC.md` §5 |
| Gateway (routing, PII, budget, trace) | `src/gateway/` ← `specs/03_routing.yaml`, `config/` | `docs/SPEC.md` §6 |
| H1 Agent Factory | `src/factory/` | `docs/factory.md` |
| H2 Build Dispatcher | `src/orchestrator/dispatcher/` | `docs/dispatcher.md` |
| H3 Huddle | `src/agents/huddle.py`, `src/orchestrator/huddle_trigger.py` | `docs/huddle.md` |
| H4 Autonomy Ladder (L0–L2) | `src/orchestrator/autonomy.py` | `docs/autonomy.md` |
| H5 Chief of Staff + Team feed + Figma | `src/agents/e2_cos.py`, `src/orchestrator/team_feed.py` | `docs/cos.md`, `docs/team-feed.md` |
| Dùng chung | `src/common/` (config, jsonl) · `src/governance/confirm.py` · `src/orchestrator/events.py` | `docs/SPEC.md` §13 |
| Ba vòng học | `src/feedback/` · `src/reconcile/` · `src/learning/` | `docs/training-loops.md` |

## Ba vòng training
- Gate feedback (`src/feedback/`): người duyệt sửa ở gate → đề xuất skill personal (cần xác nhận).
- Reconcile (`src/reconcile/`): đóng dự án → ghi actual vào historical DB → lần sau item thành FACT.
- Revision log (`src/learning/`): mọi lượt user sửa → reflection trong phiên → distiller đề xuất skill learned.
- Effort là DB-driven: `src/agents/a2_estimation.enrich` tra `src/store/historical`, không để LLM đặt số.

## Tạo agent mới (Factory)
```bash
python -m src.factory.new --spec my_agent.yaml --confirmed-by <người>   # dry-run
python -m src.factory.new --spec my_agent.yaml --write --create-branch  # nhánh factory/<id>; người gõ tên + "XAC NHAN" trong terminal (luật 10)
```

## Dashboard trực quan
```bash
uvicorn src.server.app:app --reload   # mở http://localhost:8000
```
Data flow Blackboard · routing · gate duyệt · token/chi phí · Team feed. Chi tiết: `docs/dashboard.md`.

## Đẩy lên GitHub
```bash
# tạo repo rỗng trên github.com rồi:
git remote add origin https://github.com/<user>/outsourcing-agents.git
git branch -M main
git push -u origin main
# hoặc dùng GitHub CLI:
gh repo create outsourcing-agents --private --source=. --push
```
