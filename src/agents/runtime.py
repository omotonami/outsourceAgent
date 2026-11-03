"""Runtime chung cho mọi agent theo framework 4 module.
Code lo: validate input, nạp skill, gọi LLM, parse+validate JSON, retry, trace.
Mỗi agent cấp: id, role, phase, post-process hook (deterministic)."""
import json, re
from pathlib import Path
from jsonschema import validate, ValidationError
from src.gateway import llm_client, router
from src.orchestrator import skill_loader

ROOT = Path(__file__).parents[2]
SPECS = ROOT / "specs/02_agents"
SCHEMAS = ROOT / "specs/04_schemas"


def _schema(name):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def _prompt(agent_id):
    text = (SPECS / f"{agent_id}.md").read_text(encoding="utf-8")
    return text.split("## Prompt", 1)[1]


def _extract_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(json)?", "", t).rsplit("```", 1)[0]
    return json.loads(t.strip())


def run(agent_id, role, phase, board, user_id, ctx, inp,
        post=None, signals=None, sender=None, max_retries=1):
    risk = ctx.get("risk_level", "normal")
    sig = set(signals or [])
    tier = router.pick_tier(agent_id, signals=sig, risk_level=risk)
    # 1. validate input (kết quả lỗi vẫn mang `tier` để escalation đọc được)
    try:
        validate(inp, _schema(f"{agent_id}.in.json"))
    except ValidationError as e:
        return {"error": "INVALID_INPUT", "reason": e.message, "tier": tier}

    # 2. nạp skill (personal→team→_core), ghép guidance cho MODULE 3
    skills = skill_loader.load(role, user_id, ctx)
    guidance = "\n\n".join(s["body"] for s in skills if s.get("layer") != "core") or "(none)"
    system = _prompt(agent_id).replace("{SKILL_GUIDANCE}", guidance)
    sv = skill_loader.versions(skills)

    out_schema = _schema(f"{agent_id}.out.json")
    messages = [{"role": "user", "content": json.dumps(inp, ensure_ascii=False)}]

    # 3. gọi LLM + validate output, retry rồi escalate
    last = None
    for attempt in range(max_retries + 1):
        tier = router.pick_tier(agent_id, signals=sig, risk_level=risk)
        text, _ = llm_client.call(agent_id, tier, system, messages, ctx["project_id"], phase,
                                  skill_versions=sv, sender=sender)
        try:
            data = _extract_json(text)
            validate(data, out_schema)
            break
        except (json.JSONDecodeError, ValidationError) as e:
            last = str(e)
            sig.add("schema_fail_twice" if attempt >= 1 else "reflection_fail")
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"Output không hợp lệ: {last}. Trả lại CHỈ JSON đúng schema."}]
    else:
        return {"error": "SCHEMA_FAIL", "reason": last, "escalate": True, "signals": list(sig),
                "tier": tier}

    # 4. post-process deterministic (tính số, coverage...) + ghi Blackboard
    if post:
        data = post(data, inp, ctx)
    return {"ok": True, "data": data, "skill_versions": sv, "tier": tier}
