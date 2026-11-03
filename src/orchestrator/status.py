"""project_status: Orchestrator (Code) tự tính tiến độ từ Blackboard. KHÔNG phán đoán, chỉ đo.
PM agent và dashboard ĐỌC vùng này."""
import datetime


def compute(board, state, budget_ratio=0.0):
    tb = board.get("task_backlog") or []
    br = board.get("build_results") or {}
    done = sum(1 for t in tb if t.get("status") == "merged") if tb else (1 if br.get("all_checks_pass") else 0)
    rr = board.get("risk_register") or []
    oq = 0
    req = board.get("requirements") or {}
    today = datetime.date.today().isoformat()
    for q in req.get("open_questions", []):
        if q.get("due") and q["due"] < today:
            oq += 1
    sig = [g for g, v in getattr(board, "_signatures", {}).items() if v.get("valid")]
    return {
        "phase": state, "state": state,
        "tasks_done": done, "tasks_total": len(tb) or 1,
        "gates_signed": sig,
        "budget_used_ratio": round(budget_ratio, 2),
        "open_risks": sum(1 for r in rr if r.get("status") not in ("closed", "resolved")),
        "overdue_questions": oq,
        "build_loops": br.get("loops", 0),
    }


def update(board, state, budget_ratio=0.0):
    board.write("project_status", compute(board, state, budget_ratio), agent="orchestrator")
    return board.get("project_status")
