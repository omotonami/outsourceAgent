"""Vòng training 1 — feedback ở gate.
Khi người duyệt sửa/bác một mục ở G1..G4: (a) ghi 1 mẫu eval, (b) đề xuất 1 dòng skill
personal. Dòng đó CHỈ được thêm vào knowledge/<role>/personal/<user_id> sau khi người
xác nhận (accept_suggestion). Không bao giờ đụng _core (specs). Đây là 'door 1' của học có kiểm soát."""
import datetime
import time
from pathlib import Path

from src.common import config, jsonl

ROOT = config.ROOT
LOG = ROOT / "eval/runs/gate_feedback.jsonl"
KN = ROOT / "knowledge"


def record_edit(gate, agent, role, field, before, after, reviewer, user_id,
                board=None, base_log=None, task_type=None):
    """task_type (tuỳ chọn): loại việc của output bị sửa. Có → Autonomy chỉ hạ đúng cặp (agent, task_type);
    không có → hạ mọi task_type của agent (thận trọng, hành vi cũ)."""
    log = Path(base_log) if base_log else LOG
    entry = {"ts": time.time(), "at": datetime.datetime.now().isoformat(timespec="seconds"),
             "gate": gate, "agent": agent, "role": role, "field": field,
             "before": before, "after": after, "reviewer": reviewer, "user_id": user_id}
    if task_type:
        entry["task_type"] = str(task_type)
    jsonl.append(log, entry)
    if board is not None:
        board.append("risk_register",
                     {"risk": f"Gate {gate}: người duyệt sửa '{field}'", "severity": "Low",
                      "status": "noted", "phase": "gate"}, agent="gate_ui")
    return {"logged": True, "suggestion": _suggest(field, before, after)}


def _suggest(field, before, after):
    return (f"Ở '{field}': người duyệt đã đổi \"{before}\" thành \"{after}\". "
            f"Cân nhắc áp dụng cho dự án tương tự.")


def _header(role, user_id):
    return (f"---\nname: {role}-personal-{user_id}\n"
            f"description: Kinh nghiệm cá nhân (sinh từ feedback ở gate)\n"
            f"role: {role}\nlayer: personal\nowner: {user_id}\nversion: 0.1.0\n"
            f"applies_when: {{}}\ninjects_into: \"MODULE 3 / Additional guidance\"\n---\n\n## Heuristic\n")


def accept_suggestion(role, user_id, suggestion, kn_base=None):
    """Chỉ gọi khi người dùng xác nhận. Append vào SKILL personal, KHÔNG đụng _core."""
    base = Path(kn_base) if kn_base else KN
    p = base / role / "personal" / str(user_id) / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(_header(role, user_id), encoding="utf-8")
    with p.open("a", encoding="utf-8") as f:
        f.write(f"- {suggestion}\n")
    return str(p)
