"""Kiểm môi trường vận hành — in checklist. Code thuần, không gọi model, không in giá trị key.

  python -m src.ops.doctor [--db path]
Exit 1 nếu có mục FAIL; WARN không làm fail (vd chưa có API key thì vẫn chạy được --mock).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

from src.common import config

OK, WARN, FAIL, INFO = "OK  ", "WARN", "FAIL", "INFO"
DEPS = ("yaml", "jsonschema", "fastapi", "uvicorn", "anthropic", "dotenv")


def checks(db: str | None = None) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    v = sys.version_info
    out.append((OK if v >= (3, 10) else FAIL, "Python ≥ 3.10", f"{v.major}.{v.minor}.{v.micro}"))
    for m in DEPS:
        has = importlib.util.find_spec(m) is not None
        out.append((OK if has else FAIL, f"gói {m}", "có" if has else "thiếu → pip install -r requirements.txt"))
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    out.append((OK if key else WARN, "ANTHROPIC_API_KEY",
                "có (không in giá trị)" if key else "chưa có → chỉ chạy được --mock"))
    try:
        bud = config.load_yaml(config.BUDGETS)
        ppt = bud.get("per_project_tokens") or {}
        nulls = sorted(k for k, x in ppt.items() if x is None)
        out.append((WARN if nulls else OK, "budgets per_project_tokens",
                    f"null: {nulls} → KHÔNG có trần token; chủ repo đặt số" if nulls else "đã đặt số"))
        wt = (bud.get("dispatcher") or {}).get("worker_tokens")
        out.append((WARN if wt is None else OK, "budgets dispatcher.worker_tokens",
                    "null → worker BUILD không có trần riêng" if wt is None else str(wt)))
    except config.ConfigError as e:
        out.append((FAIL, "config/budgets.yaml", str(e)))
    try:
        from src.orchestrator import usage
        pr = usage.prices()
        out.append((OK, "config/pricing.yaml", ", ".join(f"{t} in {a * 1e6:g}/out {b * 1e6:g}"
                                                         for t, (a, b) in sorted(pr.items()))))
    except (config.ConfigError, KeyError, TypeError, ValueError) as e:
        out.append((FAIL, "config/pricing.yaml", f"không đọc được số: {e}"))
    try:
        from src.blackboard import eventstore
        p = Path(db) if db else eventstore.default_path()
        eventstore.EventStore(p)            # tạo file + bảng nếu chưa có (không thêm dòng)
        with tempfile.NamedTemporaryFile(dir=p.parent, prefix=".doctor-", delete=True):
            pass
        out.append((OK, "event store ghi được", str(p)))
    except Exception as e:  # noqa: BLE001
        out.append((FAIL, "event store ghi được", f"{type(e).__name__}: {e}"))
    sk = config.ROOT / ".claude/settings.json"
    out.append((OK if sk.is_file() else WARN, ".claude/settings.json", "có" if sk.is_file() else "thiếu"))
    out.append((INFO, "Phòng 3D", "Three.js tải từ CDN (cdn.jsdelivr.net) → cần mạng; mạng cty chặn CDN thì "
                "Phòng 3D không vẽ (dashboard khác vẫn chạy)"))
    out.append((INFO, "Gate", "chỉ người ký qua gate_ui; CLI không có lệnh ký"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ops.doctor", description=__doc__)
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    res = checks(a.db)
    for st, name, detail in res:
        print(f"[{st}] {name}: {detail}")
    fails = sum(1 for st, *_ in res if st == FAIL)
    print(f"\n{'CÓ LỖI' if fails else 'Sẵn sàng'}: {fails} FAIL · {sum(1 for st, *_ in res if st == WARN)} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
