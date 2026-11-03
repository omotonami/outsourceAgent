"""Đọc rate card từ data/rate_cards/. Không có rate → raise, không đoán."""
from pathlib import Path
import yaml

DIR = Path(__file__).parents[2] / "data/rate_cards"


def rate(role: str, card: str = "default") -> float:
    data = yaml.safe_load((DIR / f"{card}.yaml").read_text(encoding="utf-8"))
    if role not in data["rates"]:
        raise KeyError(f"Không có rate cho {role} — ghi ASSUMPTION, không đoán")
    return data["rates"][role]
