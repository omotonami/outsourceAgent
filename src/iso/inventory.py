"""AI system inventory (ISO/IEC 42001). Tự đăng ký mỗi lần chạy: agent+model, mục đích, dữ liệu, hosting.
Deterministic — không hỏi người."""
import json, datetime
from pathlib import Path
ROOT = Path(__file__).parents[2]
DIR = ROOT / "data/ai_inventory"

def register(project_id, purpose, models, data_touched, hosting, role="provider", base=None):
    d = Path(base) if base else DIR; d.mkdir(parents=True, exist_ok=True)
    rec = {"project_id": project_id, "purpose": purpose, "models": sorted(set(models)),
           "data_touched": data_touched, "hosting": hosting, "org_role": role,
           "registered_at": datetime.date.today().isoformat()}
    (d / f"{project_id}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec
