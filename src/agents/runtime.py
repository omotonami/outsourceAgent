"""Runtime chung cho mọi agent theo framework 4 module.
Code lo: validate input, nạp skill, gọi LLM, parse+validate JSON, retry, trace.
Mỗi agent cấp: id, role, phase, post-process hook (deterministic).

Output model (đợt field-fixes 2026-11-04):
- System prompt = prompt spec + schema output (specs/04_schemas/<id>.out.json) nguyên văn → model thấy
  đúng khoá gốc bắt buộc (trước đây chỉ thấy câu tóm tắt, dễ lệch schema).
- `_extract_json` chịu văn bản trước/sau JSON và code fence ở bất cứ đâu: quét bằng
  json.JSONDecoder.raw_decode, lấy object hợp lệ (ưu tiên object khớp schema cuối cùng, không thì
  object lớn nhất). Không eval, không tự đoán bọc/bỏ bọc khoá gốc.
- Sai schema → lượt retry gửi thông báo lỗi rõ: khoá gốc bắt buộc / thiếu / thừa / bị bọc, + "chỉ JSON".
  Số lượt retry: config/runtime.yaml → agent_runtime.schema_retries.
- Lý do lỗi (vào risk_register) chỉ là message + hình dạng output, KHÔNG có raw output.
"""
import json

from jsonschema import Draft202012Validator, ValidationError, validate

from src.common import config
from src.gateway import llm_client, router
from src.orchestrator import skill_loader

ROOT = config.ROOT
SPECS = ROOT / "specs/02_agents"
SCHEMAS = ROOT / "specs/04_schemas"


def _schema(name):
    return config.load_json(SCHEMAS / name)


def _prompt(agent_id):
    text = (SPECS / f"{agent_id}.md").read_text(encoding="utf-8")
    return text.split("## Prompt", 1)[1]


RUNTIME_CFG = "config/runtime.yaml"
_DECODER = json.JSONDecoder()


def schema_retries(root=None) -> int:
    """Số lượt gọi lại model khi output sai JSON/schema (config, không hard-code)."""
    n = config.require(config.load_yaml(RUNTIME_CFG, root), "agent_runtime.schema_retries", RUNTIME_CFG)
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise config.ConfigError(f"{RUNTIME_CFG}: agent_runtime.schema_retries phải là số nguyên ≥ 0")
    return n


def _objects(text):
    """Mọi object JSON cấp ngoài cùng trong text: [(start, end, obj)]. Quét từng '{' bằng raw_decode;
    decode được thì nhảy qua cả object (object con không thành ứng viên riêng)."""
    out, i, n = [], 0, len(text)
    while True:
        i = text.find("{", i)
        if i < 0 or i >= n:
            return out
        try:
            obj, end = _DECODER.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            out.append((i, end, obj))
        i = end


def _extract_json(text, schema=None):
    """Object JSON trong output model (văn bản/code fence trước-sau đều được).
    Có `schema`: object khớp schema xuất hiện CUỐI CÙNG; không có cái nào khớp (hoặc không truyền
    schema) → object LỚN NHẤT (hoà → cái sau). Không có object nào → JSONDecodeError."""
    t = str(text or "")
    found = _objects(t)
    if not found:
        try:                                  # vd model trả mảng/chuỗi: để json báo lỗi đúng chỗ
            v = json.loads(t.strip())
        except json.JSONDecodeError:
            raise json.JSONDecodeError("không tìm thấy object JSON trong output", t, 0) from None
        return v
    if schema is not None:
        ok = [o for o in found if not list(Draft202012Validator(schema).iter_errors(o[2]))]
        if ok:
            return ok[-1][2]
    return max(enumerate(found), key=lambda x: (x[1][1] - x[1][0], x[0]))[1][2]


def _shape(text) -> str:
    """Hình dạng output (không lộ nội dung) — để người chẩn đoán lỗi parse."""
    t = str(text or "").strip()
    return (f"output {len(t)} ký tự, mở đầu bằng chữ: {bool(t[:1].isalpha())}, có '{{': {'{' in t}, "
            f"có ```: {'```' in t}, kết thúc bằng '}}': {t.endswith('}')}")


def _root_hint(data, schema) -> str:
    """Câu nhắc khoá gốc theo schema: bắt buộc / thiếu / thừa / bị bọc trong 1 khoá."""
    req = list(schema.get("required") or [])
    props = list((schema.get("properties") or {}).keys())
    parts = [f"Khoá gốc BẮT BUỘC theo schema: {req}"]
    if isinstance(data, dict):
        missing = [k for k in req if k not in data]
        extra = [k for k in data if props and k not in props] if schema.get("additionalProperties") is False else []
        if missing:
            parts.append(f"đang thiếu ở cấp gốc: {missing}")
        if extra:
            parts.append(f"khoá không có trong schema (bỏ đi): {extra}")
        inner = [k for k, v in data.items() if isinstance(v, dict) and missing and any(m in v for m in missing)]
        if inner:
            parts.append(f"các khoá {missing} phải nằm ở CẤP GỐC, không lồng trong {inner}")
    parts.append(f"khoá gốc được phép: {props}" if props else "")
    return "; ".join(p for p in parts if p)


def _err_msg(e) -> str:
    if isinstance(e, ValidationError):
        where = "/".join(str(x) for x in e.absolute_path)
        return e.message + (f" (tại {where})" if where else "")
    return f"không parse được JSON: {e.msg}" if isinstance(e, json.JSONDecodeError) else str(e)


def _retry_msg(err, data, schema) -> str:
    return (f"Output không hợp lệ: {err}. {_root_hint(data, schema)}. "
            "Trả lại CHỈ MỘT object JSON đúng schema — không văn bản trước/sau, không code fence, "
            "không bọc trong khoá khác.")


def _schema_block(agent_id, schema) -> str:
    """Schema output nguyên văn (nguồn sự thật specs/04_schemas) nối sau prompt — Code chèn, không phải
    knowledge (luật 5 không áp: không đụng Module 1/2/4/Forbidden)."""
    return (f"\n\n### OUTPUT JSON SCHEMA (specs/04_schemas/{agent_id}.out.json — bắt buộc)\n"
            "Trả CHỈ MỘT object JSON khớp schema này; khoá gốc bắt buộc: "
            f"{list(schema.get('required') or [])}.\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":")))


def run(agent_id, role, phase, board, user_id, ctx, inp,
        post=None, signals=None, sender=None, max_retries=None):
    if max_retries is None:
        max_retries = schema_retries()
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
    out_schema = _schema(f"{agent_id}.out.json")
    system = _prompt(agent_id).replace("{SKILL_GUIDANCE}", guidance) + _schema_block(agent_id, out_schema)
    sv = skill_loader.versions(skills)
    messages = [{"role": "user", "content": json.dumps(inp, ensure_ascii=False)}]

    # 3. gọi LLM + validate output, retry rồi escalate
    last = None
    for attempt in range(max_retries + 1):
        tier = router.pick_tier(agent_id, signals=sig, risk_level=risk)
        text, _ = llm_client.call(agent_id, tier, system, messages, ctx["project_id"], phase,
                                  skill_versions=sv, sender=sender)
        data = None
        try:
            data = _extract_json(text, out_schema)
            validate(data, out_schema)
            break
        except (json.JSONDecodeError, ValidationError) as e:
            err = _err_msg(e)
            last = f"{err} [{_shape(text)}]"
            sig.add("schema_fail_twice" if attempt >= 1 else "reflection_fail")
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": _retry_msg(err, data, out_schema)}]
    else:
        return {"error": "SCHEMA_FAIL", "reason": last, "escalate": True, "signals": list(sig),
                "tier": tier, "attempts": max_retries + 1}

    # 4. post-process deterministic (tính số, coverage...) + ghi Blackboard
    if post:
        data = post(data, inp, ctx)
    return {"ok": True, "data": data, "skill_versions": sv, "tier": tier}
