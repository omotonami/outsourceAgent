"""Ghi traceability bắt buộc cho mọi output (append-only)."""
import time

from src.common import config, jsonl

LOG = config.ROOT / "eval/runs/trace.jsonl"


def log(**fields) -> None:
    jsonl.append(LOG, {"ts": time.time(), **fields})
