"""E1 PM reporting (chỉ đọc). Tổng hợp + cảnh báo cho PM người. KHÔNG ghi Blackboard, KHÔNG quyết."""

def report(board):
    ps = board.get("project_status") or {}
    alerts = []
    if ps.get("budget_used_ratio", 0) >= 0.8:
        alerts.append({"severity": "warn", "msg": f"Budget đã dùng {int(ps['budget_used_ratio']*100)}%", "ref": "budget"})
    if ps.get("open_risks", 0) > 0:
        alerts.append({"severity": "warn", "msg": f"{ps['open_risks']} risk chưa xử lý", "ref": "risk_register"})
    if ps.get("overdue_questions", 0) > 0:
        alerts.append({"severity": "critical", "msg": f"{ps['overdue_questions']} open_question quá hạn", "ref": "requirements"})
    if ps.get("build_loops", 0) >= 3:
        alerts.append({"severity": "critical", "msg": "Build loop chạm trần 3 — cần người", "ref": "build_results"})
    done, total = ps.get("tasks_done", 0), ps.get("tasks_total", 1)
    summary = f"Phase {ps.get('phase','?')} · {done}/{total} task · gates {', '.join(ps.get('gates_signed',[])) or 'chưa'}"
    recs = []
    if any(a["severity"] == "critical" for a in alerts):
        recs.append("PM người nên xem các mục critical trước khi duyệt gate kế.")
    # KHÔNG ghi Blackboard — chỉ trả cho PM người
    return {"summary": summary, "alerts": alerts, "recommendations": recs}
