"""Báo cáo ngày (Markdown, deterministic) từ event store. Code thuần, chỉ ĐỌC store.

  python -m src.ops.report daily [--id P-...|--all] [--out docs/reports/x.md] [--redact] [--now <epoch>]

Cùng store + cùng `--now` + cùng múi giờ máy → cùng output (sắp xếp cố định).
`--redact` = bản MANG RA NGOÀI account công ty: chỉ mã project + trạng thái + số liệu. Bỏ tên client,
nội dung brief, nội dung zone sản phẩm (business_context, requirements, ...), text risk/câu hỏi/lý do
blocked, đường dẫn file. Lớp chặn cuối: mọi chuỗi trong lead_brief bị thay bằng [ẩn]; mã project chứa
chuỗi brief → bí danh băm.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

from src.agents import clarify
from src.blackboard.store import Blackboard

from . import common

DAY = 86400
HIGH = ("High", "Critical")
SUBSTATE_ORDER = ["chưa phát", "ready", "in_dev", "testing", "qc", "await_merge", "merged", "blocked"]
MASK = "[ẩn]"


def _ts(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M (UTC%z)", time.localtime(t))


def _strings(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in _strings(x)]
    return []


def sensitive(board) -> list[str]:
    """Chuỗi nhạy cảm từ lead_brief (cả cụm và từng từ ≥4 ký tự có chữ cái), dài trước."""
    out: set[str] = set()
    for s in _strings(board.get("lead_brief") or {}):
        s = s.strip()
        if len(s) >= 4 and re.search(r"[^\W\d_]", s):
            out.add(s)
            out.update(w for w in re.findall(r"[^\W_]{4,}", s) if re.search(r"[^\W\d_]", w))
    return sorted(out, key=lambda x: (-len(x), x))


def alias(pid: str, sens: list[str]) -> str:
    low = pid.lower()
    if any(s.lower() in low for s in sens):
        return "P-anon-" + hashlib.sha256(pid.encode()).hexdigest()[:8]
    return pid


def scrub_text(text: str, sens: list[str]) -> str:
    for s in sens:
        text = re.sub(re.escape(s), MASK, text, flags=re.IGNORECASE)
    return text


class ProjectReport:
    def __init__(self, store, pid: str, now: float, redact: bool):
        self.store, self.pid, self.now, self.redact = store, pid, now, redact
        self.rows = store.read(pid)
        self.board = Blackboard.load(pid, store)
        self.state = common.state_of(self.board)
        self.sens = sensitive(self.board)
        self.name = alias(pid, self.sens) if redact else pid

    # ---------- số liệu ----------
    def tasks(self) -> dict[str, int]:
        by: dict[str, int] = {}
        for t in self.board.get("task_backlog") or []:
            k = t.get("status") or "chưa phát"
            by[k] = by.get(k, 0) + 1
        return {k: by[k] for k in sorted(by, key=lambda k: (SUBSTATE_ORDER.index(k)
                                                            if k in SUBSTATE_ORDER else 99, k))}

    def blocked_tasks(self) -> list[dict]:
        return sorted((t for t in self.board.get("task_backlog") or [] if t.get("status") == "blocked"),
                      key=lambda t: t["id"])

    def open_high(self) -> list[dict]:
        return [r for r in self.board.get("risk_register") or []
                if r.get("severity") in HIGH and r.get("status") not in ("closed", "resolved")]

    def blocked_events(self, since: float | None = None) -> list[dict]:
        return [r.payload for r in self.rows if r.kind == "event" and r.zone == "trace_log"
                and (r.payload or {}).get("event") == "BLOCKED" and (since is None or r.ts >= since)]

    def gates(self) -> tuple[list[str], str | None]:
        signed = sorted(g for g, v in self.board._signatures.items() if v.get("valid"))
        return signed, common.pending_gate(self.state)

    def usage(self) -> dict:
        u = common.usage.Usage()
        for r in self.rows:
            if r.zone == common.usage.ZONE:
                u.add_record(r.payload)
        return u.to_dict()

    def activity(self) -> dict:
        since = self.now - DAY
        recent = [r for r in self.rows if since <= r.ts <= self.now]
        kinds: dict[str, int] = {}
        agents: dict[str, int] = {}
        zones: set[str] = set()
        for r in recent:
            kinds[r.kind] = kinds.get(r.kind, 0) + 1
            if r.agent:
                agents[r.agent] = agents.get(r.agent, 0) + 1
            if r.kind in ("zone_write", "zone_append") and r.zone:
                zones.add(r.zone)
        return {"rows": len(recent), "kinds": dict(sorted(kinds.items())), "agents": dict(sorted(agents.items())),
                "zones": sorted(zones), "last_ts": max((r.ts for r in self.rows), default=None)}

    def todo(self) -> list[str]:
        out = []
        _, gate = self.gates()
        if gate:
            out.append(f"Ký {gate} ({common.GATE_SIGNER[gate]}) qua dashboard (Gate dự án) hoặc người tự gõ `python -m src.ops.gate sign|reject --id ... --gate ...` — người, không phải bot.")
        if self.state == "ESCALATED":
            out.append("Dự án ESCALATED — người quyết hướng xử lý.")
        bt = self.blocked_tasks()
        if bt:
            out.append(f"{len(bt)} task BLOCKED — xem lý do, xử lý rồi chạy lại `run`.")
        last_block = self.blocked_events()
        if last_block and self.state not in common.TERMINAL and not gate:
            out.append("Lần chạy gần nhất dừng BLOCKED — xem lý do, xử lý rồi chạy lại `run`.")
        hr = self.open_high()
        if hr:
            out.append(f"{len(hr)} risk High/Critical đang mở — PM người xem.")
        pend = clarify.pending(self.board)
        if pend:
            out.append(f"{len(pend)} câu hỏi Clarify chưa trả lời — hỏi client / PM.")
        if self.usage()["budget"] is None:
            out.append("Chưa đặt budget token (config/budgets.yaml per_project_tokens = null) — chủ repo đặt số.")
        return out

    # ---------- render ----------
    def render(self) -> list[str]:
        L = [f"## {self.name}", ""]
        if not self.redact:
            lb = self.board.get("lead_brief") or {}
            L.append(f"- Client: {lb.get('client', '?')} · nguồn: {lb.get('source', '?')} · nhận: {lb.get('received_at', '?')}")
        signed, gate = self.gates()
        L += [f"- State: **{self.state}**", f"- Gate đã ký: {', '.join(signed) or '(chưa)'} · gate đang chờ: "
              f"{gate or '-'}", ""]
        L += ["### Task theo trạng thái", ""]
        tk = self.tasks()
        L += (["| trạng thái | số task |", "|---|---|"] + [f"| {k} | {v} |" for k, v in tk.items()]
              if tk else ["(chưa có task_backlog)"])
        L += ["", "### Risk mở High/Critical", ""]
        hr = self.open_high()
        if self.redact:
            n = {s: sum(1 for r in hr if r.get("severity") == s) for s in HIGH}
            L.append(f"- High: {n['High']} · Critical: {n['Critical']}")
        else:
            L += [f"- [{r.get('severity')}] {r.get('risk')} (nguồn: {r.get('source_agent')})" for r in hr] or ["- (không có)"]
        L += ["", "### BLOCKED / escalate", ""]
        bt = self.blocked_tasks()
        be = self.blocked_events()
        if self.redact:
            L.append(f"- Task blocked: {len(bt)}" + (f" ({', '.join(t['id'] for t in bt)})" if bt else ""))
            L.append(f"- Sự kiện BLOCKED (tổng): {len(be)} · ESCALATED: {'có' if self.state == 'ESCALATED' else 'không'}")
        else:
            L += [f"- Task {t['id']}: {t.get('blocked_reason')}" for t in bt]
            L += [f"- Sự kiện BLOCKED: {e.get('reason')}" for e in be[-5:]]
            if self.state == "ESCALATED":
                L.append("- **ESCALATED**")
            if not bt and not be and self.state != "ESCALATED":
                L.append("- (không có)")
        u = self.usage()
        L += ["", "### Token & chi phí ước tính", "",
              f"- Tổng: {u['total']} token ({u['tokens_in']} in / {u['tokens_out']} out) · {u['calls']} lần gọi · "
              f"~{u['est_cost_usd']:.4f} USD (đơn giá config/pricing.yaml, chỉ ước tính)"]
        for tier in sorted(u["by_tier"]):
            t = u["by_tier"][tier]
            L.append(f"- {tier}: {t['total']} token · {t['calls']} lần")
        if u["by_agent"]:
            L.append("- Theo agent: " + ", ".join(f"{a} {n}" for a, n in sorted(u["by_agent"].items())))
        b = u["budget"]
        L.append(f"- Budget: {b['used']}/{b['limit']} ({b['ratio']:.0%})" if b else "- Budget: chưa đặt (null)")
        act = self.activity()
        L += ["", "### Hoạt động 24h qua", "",
              f"- {act['rows']} bản ghi" + (f" · lần cuối {_ts(act['last_ts'])}" if act["last_ts"] else ""),
              "- Theo loại: " + (", ".join(f"{k} {v}" for k, v in act["kinds"].items()) or "-"),
              "- Theo agent: " + (", ".join(f"{k} {v}" for k, v in act["agents"].items()) or "-"),
              "- Zone được ghi: " + (", ".join(act["zones"]) or "-")]
        drs = self.board.get("decision_records") or []
        pend = clarify.pending(self.board)
        L += ["", "### Huddle / Clarify", "", f"- decision_records: {len(drs)} · câu hỏi Clarify chờ: {len(pend)}"]
        if not self.redact:
            L += [f"  - Huddle {d.get('phase')}: {d.get('question')} → {d.get('chosen')!r}" for d in drs]
            L += [f"  - Clarify: {q}" for q in pend]
        L += ["", "### Cần người quyết", ""] + ([f"- {t}" for t in self.todo()] or ["- (không có)"]) + [""]
        return L

    def overview(self) -> str:
        signed, gate = self.gates()
        tk = self.tasks()
        done = sum(v for k, v in tk.items() if k in ("await_merge", "merged"))
        u = self.usage()
        return (f"| {self.name} | {self.state} | {done}/{sum(tk.values())} | {gate or '-'} | {len(self.open_high())} | "
                f"{len(self.blocked_tasks())} | {u['total']} | {u['est_cost_usd']:.4f} |")


def build(store, pids: list[str], now: float | None = None, redact: bool = False) -> str:
    now = time.time() if now is None else float(now)
    reps = [ProjectReport(store, p, now, redact) for p in sorted(pids)]
    L = [f"# Báo cáo ngày — {time.strftime('%Y-%m-%d', time.localtime(now))}", "",
         f"Tạo lúc {_ts(now)} từ event store" + (" · **BẢN REDACT** (chỉ mã + trạng thái + số liệu)" if redact else
                                                 " · BẢN NỘI BỘ — KHÔNG mang ra ngoài account công ty"), ""]
    if not reps:
        return "\n".join(L + ["(chưa có project)", ""])
    L += ["| Project | State | Task xong | Gate chờ | Risk H/C mở | Task blocked | Token | USD ước tính |",
          "|---|---|---|---|---|---|---|---|"] + [r.overview() for r in reps] + [""]
    todo = [f"- **{r.name}**: {t}" for r in reps for t in r.todo()]
    L += ["## Việc cần người quyết (tất cả project)", ""] + (todo or ["- (không có)"]) + [""]
    for r in reps:
        L += r.render()
    text = "\n".join(L)
    if redact:
        sens = sorted({s for r in reps for s in r.sens}, key=lambda x: (-len(x), x))
        text = scrub_text(text, sens)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.ops.report", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("daily")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--id", default=None)
    g.add_argument("--all", action="store_true", help="mọi project (mặc định)")
    p.add_argument("--out", default=None, help="ghi file Markdown (docs/reports/ đã gitignore)")
    p.add_argument("--redact", action="store_true", help="bản mang ra ngoài: chỉ mã + trạng thái + số liệu")
    p.add_argument("--now", type=float, default=None, help="mốc thời gian (epoch) — để tái lập / test")
    p.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    store = common.open_store(a.db, readonly=True)
    if a.id:
        if not common.exists(store, a.id):
            print(f"LỖI: không có project {a.id}", file=sys.stderr)
            return 1
        pids = [a.id]
    else:
        pids = [p["id"] for p in store.projects()]
    text = build(store, pids, now=a.now, redact=a.redact)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Đã ghi {out}" + (" (redact)" if a.redact else " (NỘI BỘ — không mang ra ngoài)"))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
