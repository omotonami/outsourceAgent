"""Log JSONL append-only (eval/runs/*.jsonl): ghi một dòng / đọc bỏ qua dòng hỏng."""
from __future__ import annotations

import json
from pathlib import Path


def append(path: str | Path, entry: dict) -> dict:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read(path: str | Path | None) -> list[dict]:
    """Mọi dòng JSON hợp lệ; file không có → []. Dòng hỏng bị bỏ qua (log append-only, không sửa)."""
    if not path or not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
