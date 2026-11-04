"""Event store dùng chung giữa các process — SQLite (stdlib), WAL, append-only.

Một bảng `events(seq, project_id, ts, kind, zone, agent, payload_json)`; `seq` AUTOINCREMENT là thứ tự
toàn cục (nhiều process ghi đồng thời: BEGIN IMMEDIATE + busy_timeout → không mất, không trùng seq).
- Ghi: CHỈ qua Blackboard (write-through, SAU khi đã kiểm quyền + schema) — module này không biết quyền.
- Đọc: server dashboard mở `readonly=True` (URI mode=ro): không tạo file, không ghi.
- KHÔNG UPDATE. DELETE chỉ ở `prune()` / `prune_superseded()` — retention có chủ đích, gọi tay (specs/05_blackboard.yaml →
  retention.drafts_days); không endpoint nào gọi.
- Payload qua `scrub()`: bỏ khoá raw thinking / prompt / transcript / contract (không bao giờ vào store).
Cấu hình: config/runtime.yaml → eventstore (path, write_through, busy_timeout_ms, poll_ms, heartbeat_s,
live_window_s); env OA_EVENTSTORE_PATH thay path.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from src.common import config

RUNTIME = "config/runtime.yaml"
ENV_PATH = "OA_EVENTSTORE_PATH"
KINDS = ("zone_write", "zone_append", "event", "sign", "revoke")
# Khoá KHÔNG bao giờ vào store (ở mọi độ sâu payload): suy nghĩ thô, prompt, transcript, contract.
FORBIDDEN_KEYS = frozenset({"thinking", "raw_thinking", "reasoning", "transcript", "prompt", "prompts",
                            "system_prompt", "messages", "contract", "raw", "raw_output", "completion"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('zone_write','zone_append','event','sign','revoke')),
  zone TEXT,
  agent TEXT,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_project_seq ON events(project_id, seq);
"""


def settings(root=None) -> dict:
    """Section eventstore của config/runtime.yaml (ConfigError nếu thiếu)."""
    return config.section(config.load_yaml(RUNTIME, root), "eventstore", RUNTIME)


def default_path(root=None) -> Path:
    env = os.environ.get(ENV_PATH)
    if env:
        return Path(env)
    p = Path(config.require(settings(root), "path", RUNTIME))
    return p if p.is_absolute() else config.path(p, root)


def scrub(v: Any) -> Any:
    """Bản sao payload đã bỏ FORBIDDEN_KEYS (đệ quy dict/list)."""
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in v.items() if str(k).lower() not in FORBIDDEN_KEYS}
    if isinstance(v, (list, tuple)):
        return [scrub(x) for x in v]
    return v


@dataclass(frozen=True)
class Row:
    seq: int
    project_id: str
    ts: float
    kind: str
    zone: str | None
    agent: str | None
    payload: Any


class EventStore:
    def __init__(self, path: str | Path | None = None, *, readonly: bool = False,
                 busy_timeout_ms: int | None = None):
        self.path = Path(path) if path else default_path()
        self.readonly = readonly
        self.busy_timeout_ms = int(busy_timeout_ms if busy_timeout_ms is not None
                                   else config.require(settings(), "busy_timeout_ms", RUNTIME))
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn() as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(_SCHEMA)

    # Mỗi thao tác một kết nối: an toàn giữa thread/process (fork), chi phí nhỏ với WAL.
    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection | None]:
        if self.readonly:
            if not self.path.exists():
                yield None
                return
            c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=self.busy_timeout_ms / 1000,
                                isolation_level=None)
        else:
            c = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        try:
            c.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            yield c
        finally:
            c.close()

    def append(self, project_id: str, kind: str, payload: Any, *, zone: str | None = None,
               agent: str | None = None, ts: float | None = None) -> int:
        """Thêm một dòng; trả seq. Chỉ Blackboard gọi (sau khi đã kiểm quyền + schema)."""
        if self.readonly:
            raise PermissionError("EventStore readonly: không ghi")
        if kind not in KINDS:
            raise ValueError(f"kind lạ: {kind}")
        body = json.dumps(scrub(payload), ensure_ascii=False, default=str)
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                cur = c.execute("INSERT INTO events(project_id, ts, kind, zone, agent, payload_json) "
                                "VALUES (?,?,?,?,?,?)",
                                (project_id, time.time() if ts is None else float(ts), kind, zone, agent, body))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return int(cur.lastrowid)

    def read(self, project_id: str, after: int = 0, upto: int | None = None,
             limit: int | None = None) -> list[Row]:
        """Dòng của project có seq > after (≤ upto nếu có), theo seq tăng."""
        q = "SELECT seq, project_id, ts, kind, zone, agent, payload_json FROM events WHERE project_id=? AND seq>?"
        args: list = [project_id, int(after or 0)]
        if upto is not None:
            q += " AND seq<=?"
            args.append(int(upto))
        q += " ORDER BY seq"
        if limit:
            q += " LIMIT ?"
            args.append(int(limit))
        with self._conn() as c:
            if c is None:
                return []
            try:
                rows = c.execute(q, args).fetchall()
            except sqlite3.OperationalError as e:       # file có nhưng chưa có bảng (writer vừa tạo)
                if "no such table" in str(e):
                    return []
                raise
        return [Row(s, p, t, k, z, a, json.loads(b)) for s, p, t, k, z, a, b in rows]

    def last_seq(self, project_id: str) -> int:
        with self._conn() as c:
            if c is None:
                return 0
            try:
                r = c.execute("SELECT MAX(seq) FROM events WHERE project_id=?", (project_id,)).fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(r[0] or 0)

    def projects(self) -> list[dict]:
        """[{id, first_ts, last_ts, last_seq, count, status}] — status = payload project_status mới nhất."""
        with self._conn() as c:
            if c is None:
                return []
            try:
                agg = c.execute("SELECT project_id, MIN(ts), MAX(ts), MAX(seq), COUNT(*) FROM events "
                                "GROUP BY project_id").fetchall()
                st = dict(c.execute(
                    "SELECT e.project_id, e.payload_json FROM events e JOIN (SELECT project_id, MAX(seq) m "
                    "FROM events WHERE kind='zone_write' AND zone='project_status' GROUP BY project_id) x "
                    "ON e.seq = x.m").fetchall())
                last_agent = dict(c.execute(
                    "SELECT e.project_id, e.agent FROM events e JOIN (SELECT project_id, MAX(seq) m FROM events "
                    "WHERE agent GLOB '[a-e][0-9]_*' GROUP BY project_id) x ON e.seq = x.m").fetchall())
            except sqlite3.OperationalError:
                return []
        return [{"id": p, "first_ts": f, "last_ts": l, "last_seq": s, "count": n,
                 "status": json.loads(st[p]) if p in st else None, "last_agent": last_agent.get(p)}
                for p, f, l, s, n in agg]

    def prune(self, before_ts: float, project_id: str | None = None) -> int:
        """Retention CÓ CHỦ ĐÍCH (gọi tay, không endpoint nào gọi): xoá dòng ts < before_ts. Trả số dòng xoá."""
        if self.readonly:
            raise PermissionError("EventStore readonly: không xoá")
        q, args = "DELETE FROM events WHERE ts<?", [float(before_ts)]
        if project_id is not None:
            q += " AND project_id=?"
            args.append(project_id)
        with self._conn() as c:
            return c.execute(q, args).rowcount


    def prune_superseded(self, before_ts: float) -> int:
        """Retention bản nháp (src/ops/prune.py): xoá dòng zone_write có ts < before_ts mà ĐÃ có
        zone_write mới hơn cùng (project, zone). Bản mới nhất của mỗi zone giữ lại → Blackboard.load /
        resume vẫn dựng đúng trạng thái hiện tại. Trả số dòng xoá."""
        if self.readonly:
            raise PermissionError("EventStore readonly: không xoá")
        q = ("DELETE FROM events WHERE kind='zone_write' AND ts<? AND EXISTS (SELECT 1 FROM events n "
             "WHERE n.project_id=events.project_id AND n.zone=events.zone AND n.kind='zone_write' "
             "AND n.seq>events.seq)")
        with self._conn() as c:
            return c.execute(q, (float(before_ts),)).rowcount

    def vacuum(self) -> None:
        """Sau khi xoá: checkpoint WAL + VACUUM để dữ liệu đã xoá không còn trong file (PDPA)."""
        if self.readonly:
            raise PermissionError("EventStore readonly: không vacuum")
        with self._conn() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("VACUUM")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def retention_cutoff(now: float | None = None, root=None) -> float:
    """Mốc prune theo specs/05_blackboard.yaml → retention.drafts_days."""
    days = config.require(config.load_yaml("specs/05_blackboard.yaml", root), "retention.drafts_days",
                          "specs/05_blackboard.yaml")
    return (time.time() if now is None else now) - float(days) * 86400


_DEFAULT: dict[Path, EventStore] = {}
_BY_PROJECT: dict[str, EventStore] = {}     # project → store của Blackboard ghi xuyên (cho trace gateway)


def register(project_id: str, store: EventStore | None) -> None:
    """Blackboard có store gọi lúc khởi tạo: trace gateway của project này cũng vào store đó.
    store=None: project ảo (vd chat CoS không gắn dự án) — trace KHÔNG vào store nào."""
    _BY_PROJECT[project_id] = store


def store_for(project_id: str | None) -> EventStore | None:
    if project_id in _BY_PROJECT:
        return _BY_PROJECT[project_id]
    return configured_store() if project_id else None


_WT: list = []      # cache cờ write_through (đọc config một lần / process; reset_cache() để đọc lại)


def reset_cache() -> None:
    _WT.clear()
    _DEFAULT.clear()


def configured_store() -> EventStore | None:
    """Store ghi xuyên mặc định khi config bật `write_through` (không thì None = hành vi cũ)."""
    if not _WT:
        _WT.append(bool(config.require(settings(), "write_through", RUNTIME)))
    if not _WT[0]:
        return None
    p = default_path()
    if p not in _DEFAULT:
        _DEFAULT[p] = EventStore(p)
    return _DEFAULT[p]
