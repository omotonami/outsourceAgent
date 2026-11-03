"""Điểm DUY NHẤT gọi model. Enforce provider/region, PII mask, budget, trace.
Cần: pip install anthropic ; biến môi trường ANTHROPIC_API_KEY."""
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
    budget.check(project_id, phase)
    text, usage = (sender or _send)(model, system, messages, max_tokens)
    budget.add(project_id, phase, usage.get("total_tokens", 0))
    trace.log(agent=agent, model=model, tier=tier, skill_versions=skill_versions or {},
              usage=usage, project_id=project_id, phase=phase)
    return text, usage


def _send(model, system, messages, max_tokens):
    resp = _get_client().messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=messages)
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    u = resp.usage
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "total_tokens": u.input_tokens + u.output_tokens}
    return text, usage
