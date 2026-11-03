"""Vòng training 2 — đối chiếu khi đóng dự án.
So estimate với timesheet thật, ghi dự án vào historical DB (để lần sau item thành FACT),
và log metric. Đây là 'door 2' của học có kiểm soát: số đến từ dữ liệu thật, không từ LLM."""
import json, time, datetime
from pathlib import Path
from src.store import historical
from eval.metrics import estimation as em

ROOT = Path(__file__).parents[2]
METRIC_LOG = ROOT / "eval/runs/estimation_accuracy.jsonl"


def close(project_id, domain, tech_stack, estimate, actuals,
          board=None, hist_base=None, metric_log=None):
    """estimate: dict vùng 'estimate'. actuals: {'items': {item: actual_md}, 'total_md': n}."""
    wbs = []
    for it in estimate.get("wbs", []):
        wbs.append({"item": it["item"], "role": it.get("role"),
                    "estimate_md": it.get("effort_md"),
                    "actual_md": actuals.get("items", {}).get(it["item"])})
    est_total = estimate.get("effort_md_total")
    act_total = actuals.get("total_md")
    record = {"id": project_id, "domain": domain, "tech_stack": tech_stack, "wbs": wbs,
              "estimate_md_total": est_total, "actual_md_total": act_total,
              "closed_at": datetime.date.today().isoformat()}
    historical.append(record, base=hist_base)      # DB có thêm dữ liệu

    err = em.error_pct(est_total, act_total) if (est_total and act_total) else None
    band = em.within_band(est_total, act_total) if (est_total and act_total) else None
    log = Path(metric_log) if metric_log else METRIC_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "project_id": project_id,
                            "error_pct": err, "within_band": band}, ensure_ascii=False) + "\n")

    if board is not None and band is False:
        board.append("risk_register",
                     {"risk": f"Estimate lệch {err:.0f}% ở {project_id}", "severity": "Medium",
                      "status": "open", "phase": "closeout"}, agent="reconcile")
    return {"project_id": project_id, "error_pct": err, "within_band": band,
            "added_to_db": True}
