"""Conformance check (ISO/IEC 42001) — chạy bằng Code, không hỏi người.
Soát bằng chứng cho từng control; trả gaps. Đây là 'conformance-ready', KHÔNG phải chứng chỉ
(chứng chỉ do tổ chức chứng nhận được công nhận cấp)."""
from pathlib import Path
ROOT = Path(__file__).parents[2]

def check(board, project_id, trace_path=None, inventory_base=None, impact_present=False):
    tp = Path(trace_path) if trace_path else ROOT / "eval/runs/trace.jsonl"
    inv = Path(inventory_base) if inventory_base else ROOT / "data/ai_inventory"
    est = board.get("estimate") or {}
    labeled = bool(est.get("wbs")) and all("label" in i for i in est.get("wbs", []))
    controls = [
        {"clause": "A.x Logging",        "control": "Traceability model+token", "ok": tp.exists()},
        {"clause": "6.1.2 Risk",         "control": "AI risk register",         "ok": bool(board.get("risk_register"))},
        {"clause": "6.1.4/8.4 Impact",   "control": "Impact assessment",        "ok": impact_present},
        {"clause": "A.x Oversight",      "control": "Human gates đã ký",        "ok": any(v.get("valid") for v in board._signatures.values()) if getattr(board,"_signatures",None) else False},
        {"clause": "A.x Inventory",      "control": "AI system inventory",      "ok": (inv / f"{project_id}.json").exists()},
        {"clause": "A.x Transparency",   "control": "Nhãn FACT/ASSUMPTION có nguồn", "ok": labeled},
    ]
    gaps = [c for c in controls if not c["ok"]]
    return {"controls": controls, "gaps": gaps, "ready": not gaps,
            "note": "conformance-ready evidence; không phải chứng chỉ"}
