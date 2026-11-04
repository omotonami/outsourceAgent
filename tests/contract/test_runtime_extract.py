"""Lỗi thực tế A1 (2026-11-04): model trả văn bản dẫn trước JSON / JSON thiếu khoá gốc business_context
→ SCHEMA_FAIL → ESCALATED. Runtime phải parse được JSON lẫn văn bản/code fence, không đoán bọc khoá gốc,
retry với thông báo khoá gốc rõ ràng, số lượt retry lấy từ config."""
import json

import pytest

from src.agents import a1_bu, runtime
from src.blackboard.store import Blackboard
from src.common import config

IN = {"project_id": "p1", "lead_brief": {"client": "X", "summary": "ví điện tử"}}
CTX = {"project_id": "p-rt-extract"}
GOOD = {"business_context": {"summary": "ví điện tử", "goals": ["ra mắt MVP"]}, "domain": "fintech",
        "modules": [{"name": "KYC", "desc": "eKYC", "label": "ASSUMPTION"}], "tech_hints": ["react"],
        "compliance_flags": ["PDPA"], "ambiguities": ["Nhà cung cấp FX?"]}
J = json.dumps(GOOD, ensure_ascii=False, indent=2)
NO_ROOT = {k: v for k, v in GOOD.items() if k != "business_context"}
WRAPPED = {"business_context": {**GOOD["business_context"],
                                **{k: v for k, v in GOOD.items() if k != "business_context"}}}


def _seq(*texts, seen=None):
    it = iter(texts)

    def send(model, system, messages, max_tokens):
        if seen is not None:
            seen.append({"system": system, "messages": [dict(m) for m in messages]})
        return next(it), {"total_tokens": 10}
    return send


def _run(*texts, seen=None, **kw):
    return a1_bu.run(Blackboard("p"), None, CTX, IN, sender=_seq(*texts, seen=seen), **kw)


# ---------- _extract_json ----------
@pytest.mark.parametrize("text", [
    "Dưới đây là phân tích business context của dự án:\n\n" + J,                  # văn bản + JSON
    J + "\n\nGhi chú: các module trên là ASSUMPTION, cần client xác nhận {sớm}.",    # JSON + văn bản
    "Phân tích:\n```json\n" + J + "\n```\nHết.",                                    # fence giữa đoạn
    "```\n" + J + "\n```",                                                          # fence không ngôn ngữ
    "Ví dụ {không phải json} và {\"a\": 1}\n" + J + "\ncuối {",                     # ngoặc lẻ trước/sau
])
def test_extract_json_tolerates_text_and_fences(text):
    assert runtime._extract_json(text) == GOOD
    assert runtime._extract_json(text, runtime._schema("a1_bu.out.json")) == GOOD


def test_extract_prefers_last_schema_valid_object_else_largest():
    schema = runtime._schema("a1_bu.out.json")
    big_invalid = {**NO_ROOT, "padding": "x" * 500}
    text = f"Nháp: {json.dumps(big_invalid)}\nBản đúng: {json.dumps(GOOD)}"
    assert runtime._extract_json(text, schema) == GOOD
    assert runtime._extract_json(text) == big_invalid          # không schema → lớn nhất
    assert runtime._extract_json('{"a": 1} và {"a": 2}') == {"a": 2}   # hoà kích thước → cái sau


def test_extract_no_object_raises_and_never_evals():
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json("__import__('os').system('echo x')")
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json("{'python': 'dict'}")             # dict Python không phải JSON


def test_extract_does_not_unwrap_or_wrap_root():
    assert runtime._extract_json("x " + json.dumps(WRAPPED)) == WRAPPED
    assert runtime._extract_json(json.dumps(NO_ROOT)) == NO_ROOT


# ---------- runtime.run ----------
def test_text_before_json_passes_first_try():
    seen = []
    res = _run("Đây là kết quả phân tích (4735 ký tự)...\n" + J, seen=seen)
    assert res.get("ok"), res
    assert res["data"] == GOOD and len(seen) == 1


def test_missing_root_key_retries_with_clear_root_message():
    seen = []
    res = _run("Kết quả:\n" + json.dumps(NO_ROOT), J, seen=seen)
    assert res.get("ok"), res
    retry = seen[1]["messages"][-1]["content"]
    assert "'business_context' is a required property" in retry
    assert "Khoá gốc BẮT BUỘC" in retry and "business_context" in retry
    assert "thiếu ở cấp gốc: ['business_context']" in retry
    assert "CHỈ MỘT object JSON" in retry and "không văn bản trước/sau" in retry


def test_wrapped_output_not_unwrapped_and_retry_says_root_level():
    seen = []
    res = _run(json.dumps(WRAPPED), json.dumps(WRAPPED), seen=seen)
    assert res["error"] == "SCHEMA_FAIL"                       # không tự bỏ bọc
    retry = seen[1]["messages"][-1]["content"]
    assert "CẤP GỐC" in retry and "['business_context']" in retry


def test_extra_root_key_reported():
    seen = []
    _run(json.dumps({**GOOD, "reflection": {"issues": []}}), J, seen=seen)
    assert "khoá không có trong schema (bỏ đi): ['reflection']" in seen[1]["messages"][-1]["content"]


def test_schema_fail_reason_has_shape_not_raw_output():
    secret = "NOI-DUNG-THO-CUA-MODEL"
    res = _run(f"{secret} " + json.dumps(NO_ROOT), f"{secret} " + json.dumps(NO_ROOT))
    assert res["error"] == "SCHEMA_FAIL" and res["escalate"] is True
    assert "business_context" in res["reason"] and "mở đầu bằng chữ: True" in res["reason"]
    assert secret not in res["reason"] and "Nhà cung cấp FX" not in res["reason"]
    assert res["attempts"] == 1 + runtime.schema_retries()


def test_retry_count_from_config(monkeypatch):
    real = config.load_yaml

    def fake(rel, root=None):
        d = real(rel, root)
        if str(rel) == runtime.RUNTIME_CFG:
            d = {**d, "agent_runtime": {"schema_retries": 3}}
        return d
    monkeypatch.setattr(config, "load_yaml", fake)
    seen = []
    res = _run(*(["không có json"] * 4), seen=seen)
    assert res["error"] == "SCHEMA_FAIL" and len(seen) == 4 and res["attempts"] == 4
    assert runtime.schema_retries() == 3


def test_schema_retries_config_default_and_validation(tmp_path):
    assert runtime.schema_retries() == 1                        # giữ mặc định cũ max_retries=1
    (tmp_path / "config").mkdir()
    (tmp_path / "config/runtime.yaml").write_text("agent_runtime: {schema_retries: -1}\n")
    with pytest.raises(config.ConfigError):
        runtime.schema_retries(tmp_path)


def test_system_prompt_carries_output_schema_and_required_roots():
    seen = []
    _run(J, seen=seen)
    sysmsg = seen[0]["system"]
    assert "OUTPUT JSON SCHEMA (specs/04_schemas/a1_bu.out.json" in sysmsg
    assert "'business_context', 'domain', 'modules', 'compliance_flags', 'ambiguities'" in sysmsg


def test_specs_module4_mentions_every_required_root_key():
    """Mô tả output (MODULE 4) của mọi agent phải nêu đủ khoá gốc bắt buộc của schema out."""
    bad = {}
    for aid in sorted(config.agent_ids()):
        sp = config.ROOT / f"specs/04_schemas/{aid}.out.json"
        if not sp.exists():
            continue
        m4 = runtime._prompt(aid).split("MODULE 4", 1)[1]
        if "``" not in m4:                 # agent chỉ trỏ file schema (runtime nối schema nguyên văn)
            continue
        req = json.loads(sp.read_text(encoding="utf-8")).get("required") or []
        miss = [k for k in req if k not in m4]
        if miss:
            bad[aid] = miss
    assert bad == {}


def test_specs_do_not_ask_for_reflection_field_schema_forbids():
    bad = []
    for aid in sorted(config.agent_ids()):
        sp = config.ROOT / f"specs/04_schemas/{aid}.out.json"
        if not sp.exists():
            continue
        s = json.loads(sp.read_text(encoding="utf-8"))
        if s.get("additionalProperties") is False and "reflection" not in (s.get("properties") or {}):
            if "`reflection.issues`" in runtime._prompt(aid):
                bad.append(aid)
    assert bad == []
