"""Retention event store (PDPA) — chạy hằng ngày. Code thuần.

  python -m src.ops.prune [--days N] [--dry-run] [--db path]
      Xoá BẢN NHÁP cũ: dòng zone_write có ts < now − N ngày mà zone đó ĐÃ có bản mới hơn.
      N mặc định = specs/05_blackboard.yaml → retention.drafts_days. Bản mới nhất của mỗi zone giữ lại,
      nên `project run` vẫn chạy tiếp được (xoá theo dòng thô sẽ làm mất lead_brief của dự án dài ngày).
  python -m src.ops.prune --closed-project P-... [--db path]
      Xoá TOÀN BỘ dữ liệu một project đã đóng (RELEASED / ESCALATED). retention.client_data_on_project_close
      = delete_or_return → NGƯỜI quyết xoá hay trả; chỉ chạy khi người đã quyết. Project đang chạy → từ chối.
Sau khi xoá: checkpoint WAL + VACUUM để dữ liệu không còn trong file. In số dòng xoá.
"""
from __future__ import annotations

import argparse
import sys
import time

from src.blackboard import eventstore
from src.blackboard.store import Blackboard
from src.common import config

from . import common


def cutoff(days: float | None, now: float | None = None) -> float:
    now = time.time() if now is None else now
    if days is None:
        return eventstore.retention_cutoff(now)
    return now - float(days) * 86400


def count_superseded(store, before_ts: float) -> int:
    n = 0
    for p in store.projects():
        last: dict[str, int] = {}
        rows = store.read(p["id"])
        for r in rows:
            if r.kind == "zone_write":
                last[r.zone] = r.seq
        n += sum(1 for r in rows if r.kind == "zone_write" and r.ts < before_ts and r.seq != last[r.zone])
    return n


def prune_drafts(store, days: float | None = None, now: float | None = None, dry_run: bool = False) -> int:
    cut = cutoff(days, now)
    if dry_run:
        return count_superseded(store, cut)
    n = store.prune_superseded(cut)
    if n:
        store.vacuum()
    return n


def prune_closed(store, pid: str) -> int:
    if not common.exists(store, pid):
        raise LookupError(f"không có project {pid}")
    st = common.state_of(Blackboard.load(pid, store))
    if st not in common.TERMINAL:
        raise PermissionError(f"project {pid} đang ở {st}, chưa đóng (RELEASED/ESCALATED) — không xoá")
    n = store.prune(time.time() + 86400 * 365 * 100, project_id=pid)
    store.vacuum()
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ops.prune", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=None, help="mặc định specs retention.drafts_days")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--closed-project", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    store = common.open_store(a.db)
    if a.closed_project:
        try:
            n = prune_closed(store, a.closed_project)
        except (LookupError, PermissionError) as e:
            print(f"TỪ CHỐI: {e}", file=sys.stderr)
            return 1
        print(f"Đã xoá {n} dòng của project {a.closed_project} (đã đóng) · {store.path}")
        return 0
    days = a.days if a.days is not None else config.require(
        config.load_yaml("specs/05_blackboard.yaml"), "retention.drafts_days", "specs/05_blackboard.yaml")
    n = prune_drafts(store, a.days, dry_run=a.dry_run)
    print(f"{'Sẽ xoá' if a.dry_run else 'Đã xoá'} {n} dòng bản nháp cũ hơn {days:g} ngày · {store.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
