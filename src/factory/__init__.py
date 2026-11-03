"""H1 Agent Factory ("Eggbot"): phỏng vấn → spec dict → scaffold (Code) → agent_lint.

- interview.py : bộ ≤7 câu hỏi + build_spec(answers) + draft_from_llm (RECOMMENDATION, chờ người).
- scaffold.py  : Code thuần, sinh đủ bộ file cho một agent mới; mặc định dry-run, không ghi đè.
- agent_lint.py: chặn agent thiếu Card v5 / Forbidden / schema / zone / routing / runner / test.
- new.py       : CLI `python -m src.factory.new --spec spec.yaml [--write]`.
Agent do Factory sinh mặc định autonomy L0 và phải qua branch/PR + người duyệt.
"""
