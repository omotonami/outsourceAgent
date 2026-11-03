"""Ghi nhận lịch sử chỉnh sửa từng lượt (revision_log) để Reflection học TRONG PHIÊN.
Khác gate_feedback (chỉ ở gate) và reconcile (chỉ khi đóng dự án): cái này bắt MỌI lượt user
sửa output trong lúc làm. Là nguồn cho lớp skill 'learned'."""
import time, datetime


def record(board, agent, role, before, after, user_note="", kind="edit"):
    """kind: 'edit' (agent sai, học được) | 'preference' (user đổi ý, coi là nhiễu)."""
    entry = {"ts": time.time(), "at": datetime.datetime.now().isoformat(timespec="seconds"),
             "agent": agent, "role": role, "kind": kind,
             "before": before, "after": after, "note": user_note}
    board.append("revision_log", entry, agent=agent)
    return entry


def history(board, agent=None, role=None, kinds=("edit",)):
    """Lịch sử sửa (mặc định chỉ 'edit' — bỏ 'preference' là nhiễu)."""
    log = board.get("revision_log") or []
    out = []
    for e in log:
        if kinds and e.get("kind") not in kinds:
            continue
        if agent and e.get("agent") != agent:
            continue
        if role and e.get("role") != role:
            continue
        out.append(e)
    return out


def reflection_context(board, agent):
    """Text ngắn để nhét vào Reflection: 'lần trước user đổi X→Y, đừng lặp'."""
    h = history(board, agent=agent)
    if not h:
        return ""
    lines = [f"- user đổi \"{e['before']}\" → \"{e['after']}\"" + (f" ({e['note']})" if e.get("note") else "")
             for e in h[-5:]]
    return "Tránh lặp lại các chỗ user đã sửa trong task này:\n" + "\n".join(lines)
