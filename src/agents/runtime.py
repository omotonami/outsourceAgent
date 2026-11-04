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
Review PR #2: nhiều object khớp schema mà khác nhau → AmbiguousJSON (retry "chỉ MỘT object"); span trong `{`
chưa đóng (bị cắt) không nhận; quét cân ngoặc O(n) có trần config (extract_max_chars/_candidates); lý do LƯU
(_err_msg) chỉ validator + path + kiểu thực tế, chi tiết (_err_detail) chỉ gửi model ở lượt retry.
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


class AmbiguousJSON(ValueError):
    """Output có ≥ 2 object JSON KHÁC nhau cùng khớp schema → không tự chọn; retry yêu cầu MỘT object."""

    def __init__(self, n: int):
        super().__init__(f"output có {n} object JSON khác nhau cùng khớp schema — không biết chọn cái nào")
        self.n = n


def extract_limits(root=None) -> tuple[int, int]:
    """(extract_max_chars, extract_max_candidates) — config/runtime.yaml agent_runtime (không hard-code)."""
    cfg = config.load_yaml(RUNTIME_CFG, root)
    out = []
    for k in ("extract_max_chars", "extract_max_candidates"):
        n = config.require(cfg, f"agent_runtime.{k}", RUNTIME_CFG)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise config.ConfigError(f"{RUNTIME_CFG}: agent_runtime.{k} phải là số nguyên ≥ 1")
        out.append(n)
    return out[0], out[1]


def _spans(text):
    """Một lượt quét O(n), cân ngoặc `{}` có xét chuỗi JSON (chỉ khi đang trong ngoặc — dấu " ở văn bản ngoài
    không tính) và escape. Trả (spans, unclosed): spans = [(start, end)] mọi cặp {…} cân, xếp theo start
    (ngoài trước trong); unclosed = vị trí `{` nhỏ nhất còn mở tới cuối text (output bị cắt) hoặc None."""
    spans, stack = [], []
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == "{":
            stack.append(i)
        elif ch == "}":
            if stack:
                spans.append((stack.pop(), i + 1))
        elif ch == '"' and stack:
            in_str = True
    spans.sort(key=lambda x: (x[0], -x[1]))
    return spans, (stack[0] if stack else None)


def _objects(text, max_candidates=None):
    """Object JSON cấp ngoài cùng trong text: [(start, end, obj)].
    - Ứng viên = cặp {…} cân (quét tay, không thử raw_decode ở MỌI '{' → không O(n²), không dựng
      JSONDecodeError có lineno trên cả text); decode từng span trên lát cắt riêng.
    - Span nằm trong một `{` chưa đóng tới cuối (output bị cắt) → KHÔNG nhận.
    - Span nằm trong object đã decode được → bỏ (object con không thành ứng viên riêng).
    - Decode lỗi / RecursionError (lồng quá sâu) → coi như span hỏng, thử span kế (có thể là object con).
    - Quá `max_candidates` lần decode → JSONDecodeError (output bất thường)."""
    if max_candidates is None:
        max_candidates = extract_limits()[1]
    spans, unclosed = _spans(text)
    out, tried, taken_end = [], 0, -1
    for s, e in spans:
        if unclosed is not None and s > unclosed:
            break                               # mọi span sau `{` chưa đóng đều nằm trong nó
        if s < taken_end:
            continue
        tried += 1
        if tried > max_candidates:
            raise json.JSONDecodeError(f"quá nhiều ứng viên JSON (> {max_candidates})", "", 0)
        chunk = text[s:e]
        try:
            obj, end = _DECODER.raw_decode(chunk)
        except (json.JSONDecodeError, RecursionError):
            continue
        if end != len(chunk) or not isinstance(obj, dict):
            continue
        out.append((s, e, obj))
        taken_end = e
    return out


def _extract_json(text, schema=None):
    """Object JSON trong output model (văn bản/code fence trước-sau đều được).
    Có `schema`: đúng MỘT object khớp schema (các bản trùng nhau coi là một) → trả nó; ≥ 2 object khớp mà
    KHÁC nhau → AmbiguousJSON (retry "chỉ MỘT object"). Không cái nào khớp (hoặc không truyền schema) →
    object LỚN NHẤT (hoà → cái sau) để validate báo lỗi. Không có object nào → JSONDecodeError.
    Output dài hơn agent_runtime.extract_max_chars → JSONDecodeError (không quét)."""
    t = str(text or "")
    max_chars, max_cand = extract_limits()
    if len(t) > max_chars:
        raise json.JSONDecodeError(f"output quá dài ({len(t)} > {max_chars} ký tự)", "", 0)
    found = _objects(t, max_cand)
    if not found:
        try:                                  # vd model trả mảng/chuỗi: để json báo lỗi đúng chỗ
            v = json.loads(t.strip())
        except (json.JSONDecodeError, RecursionError):
            raise json.JSONDecodeError("không tìm thấy object JSON trong output", "", 0) from None
        return v
    if schema is not None:
        val = Draft202012Validator(schema)
        ok = []
        for o in found:
            if not any(True for _ in val.iter_errors(o[2])) and o[2] not in ok:
                ok.append(o[2])
        if len(ok) > 1:
            raise AmbiguousJSON(len(ok))
        if ok:
            return ok[0]
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


def _path(e) -> str:
    return "/".join(str(x) for x in e.absolute_path) or "(gốc)"


def _err_msg(e) -> str:
    """Lý do lỗi AN TOÀN để lưu (risk_register / trace_log / report / gate_view / room): chỉ validator + path +
    kiểu thực tế — KHÔNG e.message (có repr giá trị output, có thể là dữ liệu khách)."""
    if isinstance(e, ValidationError):
        miss = ""
        if e.validator == "required" and isinstance(e.instance, dict) and isinstance(e.validator_value, list):
            miss = f" thiếu {[k for k in e.validator_value if k not in e.instance]}"   # tên khoá từ schema
        return f"sai schema: {e.validator}{miss} tại {_path(e)} (kiểu thực tế: {type(e.instance).__name__})"
    if isinstance(e, json.JSONDecodeError):
        return f"không parse được JSON: {e.msg}"
    if isinstance(e, AmbiguousJSON):
        return str(e)
    return type(e).__name__


def _err_detail(e) -> str:
    """Lý do CHI TIẾT chỉ cho lượt retry gửi model (đi qua gateway, không lưu Blackboard)."""
    if isinstance(e, ValidationError):
        return e.message + f" (tại {_path(e)})"
    return _err_msg(e)


def _retry_msg(err, data, schema) -> str:
    one = (" Output đang có NHIỀU object JSON khác nhau: chỉ trả MỘT object JSON (bỏ ví dụ, bỏ bản nháp)."
           if "khác nhau cùng khớp schema" in str(err) else "")
    return (f"Output không hợp lệ: {err}.{one} {_root_hint(data, schema)}. "
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
        return {"error": "INVALID_INPUT", "reason": _err_msg(e), "tier": tier}

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
        except (json.JSONDecodeError, ValidationError, AmbiguousJSON) as e:
            last = f"{_err_msg(e)} [{_shape(text)}]"     # lưu được: không giá trị output
            sig.add("schema_fail_twice" if attempt >= 1 else "reflection_fail")
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": _retry_msg(_err_detail(e), data, out_schema)}]
    else:
        return {"error": "SCHEMA_FAIL", "reason": last, "escalate": True, "signals": list(sig),
                "tier": tier, "attempts": max_retries + 1}

    # 4. post-process deterministic (tính số, coverage...) + ghi Blackboard
    if post:
        data = post(data, inp, ctx)
    return {"ok": True, "data": data, "skill_versions": sv, "tier": tier}
