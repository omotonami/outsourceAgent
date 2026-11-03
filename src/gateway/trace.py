"""Ghi traceability bắt buộc cho mọi output (append-only)."""
import json, time
from pathlib import Path

LOG = Path(__file__).parents[2] / "eval/runs/trace.jsonl"


def log(**fields) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), **fields}, ensure_ascii=False) + "\n")
