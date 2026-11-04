"""H3 Huddle — chế độ khám phá cho PRESALES & DISCOVERY (đề án 0002 mục H3).

Vai: Proposer (agent chủ phase) → Critic (agent phase kề) → Decider (agent chủ phase).
- Số vòng, budget token, số lần retry Decider, vai theo phase: `config/budgets.yaml` → `huddle`.
- Mỗi lượt nói append vào zone `huddle_log` {round, agent, role, summary}.
- Kết thúc BẮT BUỘC bằng `decision_record` (specs/04_schemas/decision_record.json), append zone
  `decision_records` (writer "huddle"). Decider fail schema → retry → vẫn fail → escalate (không ghi record).
- Vượt budget huddle → dừng, record chosen=null, open_questions đầy đủ.
- Huddle CHỈ append `huddle_log` / `decision_records`; không bao giờ `write` zone sản phẩm.
  Blackboard chặn: zone sản phẩm là owner_write (append bị từ chối) và "huddle" không phải writer.
- Mọi lời gọi model qua `src/gateway/llm_client.py`; `sender` để test offline.
Việc quyết CÓ huddle hay không là Code: `src/orchestrator/huddle_trigger.py`.
"""
import json

from jsonschema import ValidationError, validate

from src.agents.runtime import _extract_json
from src.common import config as cfgload
from src.gateway import budget, llm_client, router
from src.orchestrator import events

ROOT = cfgload.ROOT
AGENT = "huddle"
ZONES = ("huddle_log", "decision_records")   # zone DUY NHẤT huddle được chạm tới
RECORD_SCHEMA = cfgload.load_json("specs/04_schemas/decision_record.json")
_SRC = f"{cfgload.BUDGETS} → huddle"
INVALID_TURN = "(lượt nói không hợp lệ)"   # lượt nói không phải JSON: KHÔNG giữ text thô của LLM
SUMMARY_MAX_CHARS = 1000      # summary một lượt nói ghi huddle_log


def config():
    """Section `huddle` của config/budgets.yaml ({} nếu không có)."""
    return cfgload.load_yaml(cfgload.BUDGETS).get("huddle") or {}


def _append(board, zone, entry, agent):
    """Mọi ghi của huddle đi qua events.record — chỉ ZONES; quyền thật do Blackboard enforce."""
    events.record(board, zone, entry, agent=agent, allowed=ZONES)


def _list(v):
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _dedupe(items):
    seen, out = set(), []
    for x in items:
        if isinstance(x, str) and x.strip() and x not in seen:
            seen.add(x)
            out.append(x)
    return out


_SYS = {
    "proposer": ("Bạn là {agent}, vai PROPOSER trong huddle phase {phase}. Đề xuất các phương án trả lời "
                 "câu hỏi, có cân nhắc phản biện vòng trước. Mọi đề xuất là RECOMMENDATION. "
                 'Trả CHỈ JSON: {{"summary": string, "options": [string]}}'),
    "critic": ("Bạn là {agent}, vai CRITIC trong huddle phase {phase}. Phản biện phương án của Proposer: "
               "rủi ro, mâu thuẫn, câu hỏi cần hỏi client. converged=true nếu đủ để chốt. "
               'Trả CHỈ JSON: {{"summary": string, "risks": [string], "open_questions": [string], "converged": bool}}'),
    "decider": ("Bạn là {agent}, vai DECIDER trong huddle phase {phase}. Chốt quyết định từ transcript. "
                "chosen phải là MỘT phần tử của options, hoặc null nếu chưa đủ thông tin. Chỉ đề xuất, "
                "không ghi sản phẩm. "
                'Trả CHỈ JSON: {{"question": string, "options": [string], "chosen": string|null, '
                '"rationale": string, "risks": [string], "open_questions": [string]}}'),
}


class _Run:
    def __init__(self, board, phase, question, context, ctx, sender, trigger):
        cfg = config()
        roles = (cfg.get("roles") or {}).get(phase)
        if not roles:
            raise ValueError(f"huddle không cấu hình cho phase {phase}")
        self.board, self.phase, self.question = board, phase, question
        self.context, self.ctx, self.sender, self.trigger = context or {}, ctx, sender, trigger
        self.roles = roles
        self.max_rounds = int(cfgload.require(cfg, "max_rounds", _SRC))
        self.cap = cfgload.require(cfg, "tokens", _SRC, nullable=True)
        self.retries = int(cfgload.require(cfg, "decider_retries", _SRC))
        self.used = 0
        self.transcript = []                   # chỉ trong bộ nhớ + huddle_log; không lên gate
        self.options, self.risks, self.open_qs = [], [], []
        self.participants = _dedupe([roles["proposer"], roles["critic"], roles["decider"]])

    # --- budget ---
    def over_budget(self):
        return bool(self.cap) and self.used >= self.cap

    def call(self, role, messages, signals=()):
        agent = self.roles[role]
        system = _SYS[role].format(agent=agent, phase=self.phase)
        tier = router.pick_tier(AGENT, signals=set(signals),
                                risk_level=self.ctx.get("risk_level", "normal"))
        text, usage = llm_client.call(f"{AGENT}:{agent}", tier, system, messages,
                                      self.ctx.get("project_id", self.board.project_id), self.phase,
                                      sender=self.sender)
        self.used += int((usage or {}).get("total_tokens", 0))
        return text

    def brief(self):
        return {"question": self.question, "context": self.context, "transcript": self.transcript}

    def speak(self, rnd, role, summary):
        agent = self.roles[role]
        turn = {"round": rnd, "agent": agent, "role": role, "summary": summary}
        self.transcript.append(turn)
        _append(self.board, "huddle_log", turn, agent=agent)

    def turn(self, rnd, role):
        text = self.call(role, [{"role": "user", "content": json.dumps(self.brief(), ensure_ascii=False)}])
        try:
            data = _extract_json(text)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:                     # JSONDecodeError là ValueError; lượt nói lỗi không chặn huddle
            data = {"summary": INVALID_TURN}   # text thô có thể chứa suy nghĩ nội bộ → không ghi/không chuyển tiếp
        self.speak(rnd, role, str(data.get("summary") or "")[:SUMMARY_MAX_CHARS])
        return data

    # --- kết thúc ---
    def finish(self, rec):
        validate(rec, RECORD_SCHEMA)
        _append(self.board, "decision_records", rec, agent=AGENT)
        return {"ok": True, "decision_record": rec, "tokens_used": self.used}

    def stop_budget(self, rnd):
        _append(self.board, "huddle_log", {"round": rnd, "agent": AGENT, "role": "system",
                "summary": f"Dừng: vượt budget huddle ({self.used}/{self.cap} token)"}, agent=AGENT)
        rec = self._base(rnd, "budget_exceeded")
        rec.update(chosen=None, options=_dedupe(self.options), risks=_dedupe(self.risks),
                   rationale=f"Huddle dừng do vượt budget ({self.used}/{self.cap} token); chưa chốt — người quyết.",
                   open_questions=_dedupe([self.question] + self.open_qs))
        return self.finish(rec)

    def _base(self, rnd, reason):
        rec = {"question": self.question, "participants": self.participants, "rounds": rnd,
               "phase": self.phase, "stopped_reason": reason}
        if self.trigger:
            rec["trigger"] = self.trigger
        return rec

    def decide(self, rnd):
        msgs = [{"role": "user", "content": json.dumps(self.brief(), ensure_ascii=False)}]
        sig, last = [], None
        for attempt in range(self.retries + 1):
            if self.over_budget():
                return self.stop_budget(rnd)
            try:
                text = self.call("decider", msgs, signals=sig)
            except budget.BudgetExceeded:
                return self.stop_budget(rnd)
            try:
                data = _extract_json(text)
                if not isinstance(data, dict):
                    raise ValidationError("decision_record phải là object")
                rec = {**data, **self._base(rnd, "decided")}   # Code là nguồn cho participants/rounds
                rec["question"] = data.get("question") or self.question
                validate(rec, RECORD_SCHEMA)
                if rec["chosen"] is not None and rec["chosen"] not in rec["options"]:
                    raise ValidationError(f"chosen '{rec['chosen']}' không nằm trong options")
                self.speak(rnd, "decider", rec.get("rationale", "")[:SUMMARY_MAX_CHARS])
                return self.finish(rec)
            except (ValueError, ValidationError) as e:
                last = getattr(e, "message", None) or str(e)
                sig = ["reflection_fail"] if attempt == 0 else ["schema_fail_twice"]
                msgs = msgs + [{"role": "assistant", "content": text},
                               {"role": "user", "content": f"decision_record không hợp lệ: {last}. "
                                                           "Trả lại CHỈ JSON đúng schema."}]
        _append(self.board, "huddle_log", {"round": rnd, "agent": AGENT, "role": "system",
                "summary": f"Decider fail schema {self.retries + 1} lần → escalate"}, agent=AGENT)
        return {"error": "SCHEMA_FAIL", "reason": last, "escalate": True, "rounds": rnd,
                "tokens_used": self.used}

    def run(self):
        rnd = 0
        for rnd in range(1, self.max_rounds + 1):
            try:
                for role in ("proposer", "critic"):
                    if self.over_budget():   # rounds = số vòng đã có lượt nói
                        return self.stop_budget(rnd - 1 if role == "proposer" else rnd)
                    data = self.turn(rnd, role)
                    if role == "proposer":
                        self.options += _list(data.get("options"))
                    else:
                        self.risks += _list(data.get("risks"))
                        self.open_qs += _list(data.get("open_questions"))
            except budget.BudgetExceeded:      # budget phase (gateway) cũng dừng huddle
                return self.stop_budget(rnd)
            if data.get("converged") is True:
                break
        return self.decide(rnd)


def run(board, phase, question, context=None, ctx=None, sender=None, trigger=None):
    """Chạy một huddle. Trả {"ok", "decision_record", "tokens_used"} hoặc {"error", "escalate": True}."""
    return _Run(board, phase, question, context, ctx or {}, sender, trigger).run()
