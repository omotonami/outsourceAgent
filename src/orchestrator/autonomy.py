"""H4 Autonomy Ladder (đề án 0002) — CHỈ mức L0–L2. Code thuần, không LLM.

- Mức theo cặp (agent_id, task_type), lưu ở file state JSON (đường dẫn inject được).
  Agent mới / chưa có dữ liệu = L0. Agent ghim (config `pinned`) không bao giờ nâng.
- `evaluate()` đọc review log (gate duyệt có/không sửa) + `gate_feedback.jsonl` + `revision_log`
  (Blackboard) + kết quả eval regression → ĐỀ XUẤT nâng (chưa hiệu lực) và HẠ ngay khi có
  revision / gate edit / regression. `report_incident()` hạ về `demote.on_incident`.
  Mọi lần hạ ghi `risk_register`.
- Nâng chỉ có hiệu lực qua `promote(..., approved_by=<người>)` khi đang có đề xuất —
  giống `gate_feedback.accept_suggestion`.
- Mức tối đa cứng HARD_MAX = L2: config `max_level` cao hơn vẫn bị khoá ở L2; state file bị sửa tay
  lên > L2 cũng bị kẹp về L2; promote tới > L2 → AutonomyRefused.
- L3 Auto-gate BẢO LƯU: module này KHÔNG ký, không thu hồi, không đọc/ghi chữ ký gate.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from src.common import config as cfgload, jsonl
from src.governance import confirm as human

ROOT = cfgload.ROOT
CONFIG = ROOT / cfgload.AUTONOMY
STATE = ROOT / "eval/runs/autonomy_state.json"
REVIEWS = ROOT / "eval/runs/autonomy_reviews.jsonl"
FEEDBACK = ROOT / "eval/runs/gate_feedback.jsonl"
REGRESSION = ROOT / "eval/runs/eval_regression.jsonl"

HARD_MAX = 2                      # L2. L3 bảo lưu — không đổi số này nếu chủ repo chưa duyệt.
MANUAL = "manual"
RISK_AGENT = "orchestrator"


class AutonomyRefused(Exception):
    pass


def _num(level) -> int:
    """'L2' / 2 → 2. Không kẹp ở đây."""
    if isinstance(level, int):
        return level
    s = str(level).strip().upper()
    if not (s.startswith("L") and s[1:].isdigit()):
        raise ValueError(f"mức autonomy lạ: {level!r}")
    return int(s[1:])


def _name(n: int) -> str:
    return f"L{n}"


def _match(entry: dict, agent: str, task_type: str) -> bool:
    """Entry khớp cặp: cùng agent; nếu entry có task_type thì phải trùng (không có = áp cả agent —
    thận trọng: gate_feedback cũ / revision_log không ghi task_type thì hạ mọi task_type của agent)."""
    if entry.get("agent") != agent:
        return False
    tt = entry.get("task_type")
    return tt is None or tt == task_type


class Autonomy:
    def __init__(self, config: dict | str | Path | None = None, *, state_path: str | Path | None = None,
                 reviews_log: str | Path | None = None, feedback_log: str | Path | None = None,
                 regression_log: str | Path | None = None, clock: Callable[[], float] = time.time):
        if isinstance(config, dict):
            cfg = config
        else:
            cfg = cfgload.load_yaml(Path(config or CONFIG))
        self.cfg = cfg
        self.max_level = min(_num(cfg.get("max_level", "L0")), HARD_MAX)   # config sai vẫn khoá L2
        lv = cfg.get("levels") or {}
        self.levels = {n: lv.get(_name(n)) or {"allowed_triggers": [MANUAL], "review": "every_output"}
                       for n in range(HARD_MAX + 1)}                            # bỏ qua mọi mức > L2
        self.promote_cfg = cfg.get("promote") or {}
        self.demote_cfg = cfg.get("demote") or {}
        self.pinned = {a: min(_num(v), HARD_MAX) for a, v in (cfg.get("pinned") or {}).items()}
        self.state_path = Path(state_path or STATE)
        self.reviews_log = Path(reviews_log or REVIEWS)
        self.feedback_log = Path(feedback_log or FEEDBACK)
        self.regression_log = Path(regression_log or REGRESSION)
        self.clock = clock
        self._last = 0.0

    def _now(self) -> float:
        """Thời điểm tăng ngặt trong instance — tránh trùng ts giữa đổi mức và sự kiện kế tiếp."""
        self._last = max(float(self.clock()), self._last + 1e-6)
        return self._last

    # ---------- state ----------
    @staticmethod
    def key(agent: str, task_type: str) -> str:
        return f"{agent}::{task_type}"

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                st = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                st = {}
        else:
            st = {}
        st.setdefault("pairs", {})
        return st

    def _save(self, st: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _pair(self, st: dict, agent: str, task_type: str) -> dict:
        return st["pairs"].setdefault(self.key(agent, task_type),
                                      {"agent": agent, "task_type": task_type, "level": "L0",
                                       "since": 0.0, "pending": None, "history": []})

    def _cap(self, agent: str) -> int:
        return self.pinned.get(agent, self.max_level)

    def level_num(self, agent: str, task_type: str) -> int:
        p = self._load()["pairs"].get(self.key(agent, task_type))
        n = _num(p["level"]) if p else 0
        return max(0, min(n, self._cap(agent), HARD_MAX))     # state sửa tay > L2 vẫn kẹp

    def level(self, agent: str, task_type: str) -> str:
        return _name(self.level_num(agent, task_type))

    def pending(self, agent: str, task_type: str) -> dict | None:
        p = self._load()["pairs"].get(self.key(agent, task_type))
        return p.get("pending") if p else None

    # ---------- chạy hay không ----------
    def can_run(self, agent: str, task_type: str, trigger: str) -> dict:
        n = self.level_num(agent, task_type)
        spec = self.levels[n]
        allowed_triggers = list(spec.get("allowed_triggers") or [MANUAL])
        ok = trigger in allowed_triggers
        reason = None if ok else (f"autonomy: {agent}/{task_type} ở {_name(n)} chỉ chạy khi trigger "
                                  f"{allowed_triggers}, nhận '{trigger}'")
        return {"allowed": ok, "level": _name(n), "trigger": trigger,
                "review": spec.get("review", "every_output"), "reason": reason}

    # ---------- dữ liệu vào ----------
    def record_review(self, agent: str, task_type: str, gate: str, reviewer: str, edited: bool = False,
                      approved: bool = True) -> dict:
        """Ghi kết quả người duyệt ở gate cho output của cặp. CHỈ ghi log — không ký gate.
        `reviewer` phải là người (governance/confirm) — máy/auto-* → AutonomyRefused, không ghi."""
        reviewer = human.require_human(reviewer, AutonomyRefused, "reviewer '{who}' không phải người",
                                       extra=set(self.promote_cfg.get("reject_approvers") or []) | {agent})
        e = {"ts": self._now(), "agent": agent, "task_type": task_type, "gate": gate,
             "reviewer": reviewer, "approved": bool(approved), "edited": bool(edited)}
        return jsonl.append(self.reviews_log, e)

    # ---------- đánh giá ----------
    def evaluate(self, board=None, eval_results: list[dict] | None = None) -> dict:
        """Hạ ngay (có hiệu lực) khi có bằng chứng xấu; nâng chỉ ĐỀ XUẤT (pending)."""
        st = self._load()
        reviews = jsonl.read(self.reviews_log)
        feedback = jsonl.read(self.feedback_log)
        revisions = [e for e in ((board.get("revision_log") if board is not None else None) or [])
                     if e.get("kind", "edit") == "edit"]
        fresh = [{"ts": self._now(), **e} for e in (eval_results or [])]   # truyền trực tiếp = kết quả mới
        regressions = [e for e in jsonl.read(self.regression_log) + fresh if e.get("regression")]
        for e in reviews:
            self._pair(st, e["agent"], e["task_type"])
        proposals, demotions = [], []
        for pair in list(st["pairs"].values()):
            a, tt, since = pair["agent"], pair["task_type"], float(pair.get("since") or 0)
            # bằng chứng xấu: >= since (thà hạ nhầm còn hơn bỏ sót); duyệt: > since
            bad = lambda xs: [x for x in xs if _match(x, a, tt) and float(x.get("ts") or 0) >= since]
            r_rev, r_fb, r_reg = bad(revisions), bad(feedback), bad(regressions)
            r_rv = [x for x in bad(reviews) if float(x.get("ts") or 0) > since]
            gate_edits = r_fb + [x for x in r_rv if x.get("edited") or not x.get("approved", True)]
            steps, why = 0, []
            for xs, k, label in ((r_rev, "on_revision", "revision"), (gate_edits, "on_gate_edit", "gate edit"),
                                 (r_reg, "on_regression", "eval regression")):
                if xs:
                    s = int(self.demote_cfg.get(k, 1))
                    steps = max(steps, s)
                    why.append(f"{label} x{len(xs)}")
            if why:
                seen = [float(x.get("ts") or 0) for x in r_rev + gate_edits + r_reg]
                d = self._apply_demote(st, pair, steps, "; ".join(why), board, max(seen) + 1e-6)
                if d:
                    demotions.append(d)
                continue
            # chuỗi duyệt liên tiếp không sửa, kể từ lần đổi mức gần nhất
            streak = 0
            for x in sorted(r_rv, key=lambda x: float(x.get("ts") or 0)):
                streak = streak + 1 if (x.get("approved", True) and not x.get("edited")) else 0
            cur = min(_num(pair["level"]), HARD_MAX)
            nxt = cur + 1
            need = (self.promote_cfg.get("consecutive_approvals") or {}).get(_name(nxt))
            if nxt > self._cap(a) or need is None or streak < int(need):
                continue
            prop = {"from": _name(cur), "to": _name(nxt), "streak": streak, "threshold": int(need),
                    "proposed_at": self._now()}
            if (pair.get("pending") or {}).get("to") != prop["to"]:
                pair["pending"] = prop
            proposals.append({"agent": a, "task_type": tt, **pair["pending"]})
        self._save(st)
        return {"proposals": proposals, "demotions": demotions}

    # ---------- nâng (chỉ người) ----------
    def promote(self, agent: str, task_type: str, approved_by: str, to: str | None = None) -> dict:
        if not approved_by or not str(approved_by).strip():
            raise AutonomyRefused("promote cần approved_by là người")
        bots = set(self.promote_cfg.get("reject_approvers") or []) | {agent}
        human.require_human(approved_by, AutonomyRefused, "'{who}' không phải người duyệt hợp lệ", extra=bots)
        if to is not None and _num(to) > HARD_MAX:
            raise AutonomyRefused(f"{to} vượt mức tối đa cứng L{HARD_MAX} (L3 bảo lưu)")
        st = self._load()
        pair = self._pair(st, agent, task_type)
        pend = pair.get("pending")
        if not pend:
            raise AutonomyRefused(f"{agent}/{task_type} chưa có đề xuất nâng (chạy evaluate trước)")
        target = _num(pend["to"])
        if to is not None and _num(to) != target:
            raise AutonomyRefused(f"đề xuất là {pend['to']}, không phải {to}")
        if target > min(self._cap(agent), HARD_MAX):
            raise AutonomyRefused(f"{pend['to']} vượt mức tối đa {_name(self._cap(agent))}")
        now = self._now()
        pair["history"].append({"at": now, "from": pair["level"], "to": _name(target), "kind": "promote",
                                "by": approved_by, "streak": pend.get("streak")})
        pair.update(level=_name(target), since=now, pending=None)
        self._save(st)
        return {"agent": agent, "task_type": task_type, "level": _name(target), "approved_by": approved_by}

    def reject(self, agent: str, task_type: str, by: str) -> None:
        """Người bác đề xuất nâng: xoá pending, chuỗi duyệt đếm lại từ đầu."""
        st = self._load()
        pair = self._pair(st, agent, task_type)
        pair["history"].append({"at": self._now(), "kind": "reject_promote", "by": by, "pending": pair.get("pending")})
        pair.update(pending=None, since=self._now())
        self._save(st)

    # ---------- hạ (tự động) ----------
    def report_incident(self, agent: str, task_type: str, reason: str, board=None) -> dict | None:
        st = self._load()
        pair = self._pair(st, agent, task_type)
        to = min(_num(self.demote_cfg.get("on_incident", "L0")), HARD_MAX)
        steps = max(0, _num(pair["level"]) - to)
        d = self._apply_demote(st, pair, steps, f"incident: {reason}", board, self._now())
        self._save(st)
        return d

    def _apply_demote(self, st, pair, steps, reason, board, since) -> dict | None:
        cur = min(_num(pair["level"]), HARD_MAX)
        new = max(0, cur - int(steps))
        had_pending = pair.get("pending") is not None
        pair.update(since=max(float(since), self._now()), pending=None)
        self._last = max(self._last, pair["since"])
        if new == cur and not had_pending:
            return None
        pair["history"].append({"at": self._now(), "from": _name(cur), "to": _name(new), "kind": "demote",
                                "by": "autonomy", "reason": reason})
        pair["level"] = _name(new)
        if board is not None:
            board.append("risk_register", {
                "risk": f"Autonomy hạ {pair['agent']}/{pair['task_type']} {_name(cur)}→{_name(new)}: {reason}",
                "severity": self.demote_cfg.get("risk_severity", "Medium"), "status": "open",
                "phase": "autonomy"}, agent=RISK_AGENT)
        return {"agent": pair["agent"], "task_type": pair["task_type"], "from": _name(cur), "to": _name(new),
                "reason": reason}
