"""H3 — quyết định CÓ huddle hay không. Code thuần, KHÔNG LLM.

- PRESALES: len(business_context.ambiguities) ≥ huddle.presales_ambiguity_threshold (config, mặc định 3).
- DISCOVERY: xung đột phát hiện bằng Code giữa requirements (B1) ↔ design_spec (B2) ↔ NFR:
  * story không có screen nào trỏ tới (screen.story_refs chứa story.id / "S<n>" / i_want),
  * screen trỏ tới story không tồn tại,
  * NFR không được story nào phủ (story.nfr_refs chứa id, hoặc text NFR xuất hiện trong story).
- Không trigger lại nếu decision_records đã có record cùng (phase, trigger).
Trả dict {phase, trigger, question, context} để truyền cho `src.agents.huddle.run`, hoặc None.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
DEFAULT_THRESHOLD = 3


def _cfg():
    cfg = yaml.safe_load((ROOT / "config/budgets.yaml").read_text(encoding="utf-8")) or {}
    return cfg.get("huddle") or {}


def _already(board, phase, trigger):
    return any(r.get("phase") == phase and r.get("trigger") == trigger
               for r in board.get("decision_records") or [])


def presales(board, threshold=None):
    bc = board.get("business_context") or {}
    amb = bc.get("ambiguities") or []
    th = threshold if threshold is not None else (_cfg().get("presales_ambiguity_threshold") or DEFAULT_THRESHOLD)
    if len(amb) < th or _already(board, "presales", "ambiguities"):
        return None
    return {"phase": "presales", "trigger": "ambiguities",
            "question": f"Brief còn {len(amb)} điểm mơ hồ — hướng giải pháp và giả định nào để estimate?",
            "context": {"ambiguities": amb, "domain": bc.get("domain"), "modules": bc.get("modules", [])}}


def _story_keys(i, s):
    keys = {f"S{i + 1}"}
    for k in ("id", "i_want"):
        if s.get(k):
            keys.add(str(s[k]).strip().lower())
    return {k.lower() for k in keys}


def _nfrs(board):
    out = []
    for zone in ("requirements", "architecture", "business_context"):
        for n in (board.get(zone) or {}).get("nfr") or []:
            out.append({"id": n, "text": n} if isinstance(n, str) else
                       {"id": str(n.get("id") or n.get("text")), "text": str(n.get("text") or n.get("id"))})
    return out


def conflicts(board):
    """Danh sách xung đột story ↔ screen ↔ NFR (chuỗi mô tả). Rỗng = không xung đột."""
    stories = (board.get("requirements") or {}).get("stories") or []
    screens = (board.get("design_spec") or {}).get("screens") or []
    out = []
    if stories and screens:
        refs = {str(r).strip().lower() for sc in screens for r in sc.get("story_refs") or []}
        all_keys = set()
        for i, s in enumerate(stories):
            keys = _story_keys(i, s)
            all_keys |= keys
            if not keys & refs:
                out.append(f"story '{s.get('id') or s.get('i_want') or f'S{i + 1}'}' không có screen tương ứng")
        for sc in screens:
            for r in sc.get("story_refs") or []:
                if str(r).strip().lower() not in all_keys:
                    out.append(f"screen '{sc.get('id')}' trỏ tới story không tồn tại '{r}'")
    for n in _nfrs(board):
        covered = False
        for s in stories:
            if n["id"] in (s.get("nfr_refs") or []):
                covered = True
                break
            blob = " ".join([s.get("i_want", ""), s.get("so_that", "")] + list(s.get("AC") or [])).lower()
            if n["text"].lower() in blob:
                covered = True
                break
        if not covered:
            out.append(f"NFR '{n['text']}' không được story nào phủ")
    return out


def discovery(board):
    found = conflicts(board)
    if not found or _already(board, "discovery", "conflicts"):
        return None
    return {"phase": "discovery", "trigger": "conflicts",
            "question": "Xử lý các xung đột story ↔ screen ↔ NFR thế nào trước G2?",
            "context": {"conflicts": found}}


def check(board, phase):
    """Điểm vào duy nhất cho orchestrator: phase 'PRESALES'/'DISCOVERY' (không phân biệt hoa thường)."""
    p = (phase or "").lower()
    if p == "presales":
        return presales(board)
    if p == "discovery":
        return discovery(board)
    return None
