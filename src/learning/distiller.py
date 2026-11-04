"""Distiller: đọc revision_log qua NHIỀU task, đếm pattern user sửa lặp lại,
cô đọng thành skill 'learned' trạng thái PROPOSED. KHÔNG tự vào prompt —
phải qua eval regression + người duyệt mới promote lên team (đúng meta-rule v5)."""
import re, datetime
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parents[2]
KN = ROOT / "knowledge"


def _signature(entry):
    """Khoá gom nhóm: role + ý chính của thay đổi (rút gọn after)."""
    after = re.sub(r"\s+", " ", (entry.get("after") or "")).strip().lower()
    return (entry.get("role"), after[:40])


def distill(revision_entries, min_count=3):
    """Trả các pattern lặp >= min_count lần → đề xuất 1 dòng skill mỗi pattern."""
    edits = [e for e in revision_entries if e.get("kind") == "edit"]
    counts = Counter(_signature(e) for e in edits)
    proposals = []
    for (role, sig), n in counts.items():
        if n >= min_count:
            sample = next(e for e in edits if _signature(e) == (role, sig))
            proposals.append({
                "role": role, "count": n,
                "heuristic": f"User thường sửa thành: \"{sample['after']}\" (gặp {n} lần). Cân nhắc áp dụng.",
                "status": "proposed",
            })
    return proposals


def write_proposed(proposal, kn_base=None):
    """Ghi vào knowledge/<role>/learned/PROPOSED.md — CHƯA dùng, chờ eval + duyệt."""
    base = Path(kn_base) if kn_base else KN
    p = base / proposal["role"] / "learned" / "PROPOSED.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ("" if p.exists() else
              f"---\nname: {proposal['role']}-learned\nrole: {proposal['role']}\nlayer: learned\n"
              f"status: proposed\ninjects_into: \"MODULE 3 / Additional guidance\"\n"
              f"note: \"Tự rút từ revision_log. CHƯA dùng — cần eval regression + người duyệt.\"\n---\n\n"
              f"## Heuristic học được (proposed)\n")
    with p.open("a", encoding="utf-8") as f:
        if header:
            f.write(header)
        f.write(f"- [{datetime.date.today().isoformat()}] (x{proposal['count']}) {proposal['heuristic']}\n")
    return str(p)
