"""Điểm DUY NHẤT gọi model. Enforce provider/region, PII mask, budget, trace.
Cần: pip install anthropic ; biến môi trường ANTHROPIC_API_KEY."""
import json
from pathlib import Path
import yaml
from . import pii_mask, budget, trace

_LOCK = yaml.safe_load((Path(__file__).parents[2] / "config/models.lock").read_text(encoding="utf-8"))
_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # import trễ để test offline không cần gói
        _client = anthropic.Anthropic()  # đọc ANTHROPIC_API_KEY
    return _client


def call(agent, tier, system, messages, project_id, phase,
         skill_versions=None, max_tokens=4000, sender=None):
    """Trả (text, usage). `sender` để test inject mock."""
    model = _LOCK[tier]
    messages = [pii_mask.mask_message(m) for m in messages]
    # Giữ chỗ cận trên TRƯỚC khi gọi (mỗi token ≥ 1 byte UTF-8 → số byte input là cận trên an toàn),
    # rồi trừ theo usage thực tế. Vượt trần → BudgetExceeded, không gọi model.
    res = budget.reserve_call(project_id, phase, max_tokens + input_bound(system, messages))
    try:
        text, usage = (sender or _send)(model, system, messages, max_tokens)
    except BaseException:
        budget.cancel(res)
        raise
    budget.commit(res, usage.get("total_tokens", 0))
    trace.log(agent=agent, model=model, tier=tier, skill_versions=skill_versions or {},
              usage=usage, project_id=project_id, phase=phase)
    return text, usage


def input_bound(system, messages) -> int:
    """Cận trên số token input: tổng byte UTF-8 của system + messages (đã serialize)."""
    return len(str(system or "").encode("utf-8")) + sum(
        len(json.dumps(m, ensure_ascii=False).encode("utf-8")) for m in messages)


def _send(model, system, messages, max_tokens):
    resp = _get_client().messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=messages)
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    u = resp.usage
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "total_tokens": u.input_tokens + u.output_tokens}
    return text, usage
