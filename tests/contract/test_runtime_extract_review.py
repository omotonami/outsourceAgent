"""Review PR #2 (2026-11-04) — tái hiện từ /tmp/soi3/t_extract.py + t_msg.py:
- nhiều object khớp schema mà KHÁC nhau → mơ hồ → retry "chỉ trả MỘT object JSON";
- ứng viên nằm trong `{` chưa đóng (output bị cắt) → không nhận;
- quét O(n), trần số ứng viên / độ dài (config), RecursionError = lỗi parse;
- lý do lưu (risk_register/report/room) không chứa giá trị output; retry gửi model giữ chi tiết."""
import json
import time

import pytest
from jsonschema import ValidationError, validate

from src.agents import a1_bu, runtime
from src.blackboard.store import Blackboard
from src.common import config

SCHEMA = {"type": "object", "required": ["stories"], "properties": {"stories": {"type": "array"}},
          "additionalProperties": False}
N = 100_000


# ---------- mơ hồ ----------
def test_two_different_schema_valid_objects_is_ambiguous():
    real, ex = '{"stories":[{"id":"S1"}]}', '{"stories":[]}'
    with pytest.raises(runtime.AmbiguousJSON):
        runtime._extract_json(f"Đây là kết quả:\n```json\n{real}\n```\nVí dụ định dạng: {ex}", SCHEMA)
    with pytest.raises(runtime.AmbiguousJSON):
        runtime._extract_json('{"stories":[1]} rồi sửa lại: {"stories":[2]}', SCHEMA)


def test_identical_duplicates_are_not_ambiguous():
    assert runtime._extract_json('{"stories":[1]}\n```json\n{"stories":[1]}\n```', SCHEMA) == {"stories": [1]}


def test_ambiguous_output_retries_with_one_object_message():
    from tests.contract.test_runtime_extract import GOOD, IN, CTX, J, _seq
    other = json.dumps({**GOOD, "domain": "retail"}, ensure_ascii=False)
    seen = []
    res = a1_bu.run(Blackboard("p"), None, CTX, IN, sender=_seq(f"{J}\nhoặc\n{other}", J, seen=seen))
    assert res.get("ok"), res
    retry = seen[1]["messages"][-1]["content"]
    assert "chỉ trả MỘT object JSON" in retry and "khác nhau" in retry


# ---------- bị cắt ----------
def test_candidate_inside_unclosed_brace_rejected():
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json('{"result": {"stories":[1]}, "note": "trunc', SCHEMA)
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json('{"a":[' + '{"x":"y"},' * 50, SCHEMA)


def test_unclosed_brace_after_object_does_not_hide_it():
    assert runtime._extract_json('{"stories":[1]} rồi bị cắt {"stories":[', SCHEMA) == {"stories": [1]}


def test_quotes_and_braces_in_strings_and_prose():
    t = 'Model nói "xem {này}": ' + json.dumps({"stories": ["a } b { \" c"]})
    assert runtime._extract_json(t, SCHEMA) == {"stories": ["a } b { \" c"]}


@pytest.mark.parametrize("prose", ['ký tự { mở rồi ', 'dùng {name} để điền, ', 'mở { rồi đóng } ',
                                   'ngoặc lẻ { và "trích dẫn lẻ ', '{{ template '])
def test_stray_prose_brace_before_complete_json_accepted(prose):
    assert runtime._extract_json(prose + '{"stories":[1]}', SCHEMA) == {"stories": [1]}


@pytest.mark.parametrize("text", ['Kết quả: {"result": {"stories":[1]}, "note": "bị cắ',
                                  'dùng {name}: {"wrap": {"stories":[1]}, "x": [1, 2',
                                  '{ "outer": {"stories":[1]}'])
def test_truly_truncated_output_still_rejected(text):
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json(text, SCHEMA)


# ---------- hiệu năng / trần ----------
@pytest.mark.parametrize("text", [
    "{" * N, '{"a":' * (N // 5), '{"a":' * 3000 + "1", '{"' * (N // 2),
    '{"a":[' + ('{"x":"' + "y" * 50 + '"},') * 1500, ('{"k":[') * 2000 + "1," * 40000,
    '{"a": "' + "{" * N, ('{"a":1,') * (N // 7),
])
def test_pathological_inputs_fast_and_parse_error(text):
    t0 = time.perf_counter()
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json(text, SCHEMA)
    assert time.perf_counter() - t0 < 2.0


def test_deep_nesting_recursion_is_parse_error():
    deep = '{"a":' * 60000 + "1" + "}" * 60000          # json C scanner → RecursionError
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json(deep, SCHEMA)
    with pytest.raises(json.JSONDecodeError):
        runtime._extract_json("[" * 100_000, SCHEMA)


def test_candidate_and_length_limits_from_config(monkeypatch):
    real = config.load_yaml

    def fake(rel, root=None):
        d = real(rel, root)
        if str(rel) == runtime.RUNTIME_CFG:
            d = {**d, "agent_runtime": {**d["agent_runtime"], "extract_max_chars": 50,
                                        "extract_max_candidates": 3}}
        return d
    monkeypatch.setattr(config, "load_yaml", fake)
    assert runtime.extract_limits() == (50, 3)
    with pytest.raises(json.JSONDecodeError, match="quá dài"):
        runtime._extract_json('{"stories":[]}' + " " * 60, SCHEMA)
    with pytest.raises(json.JSONDecodeError, match="quá nhiều ứng viên"):
        runtime._extract_json('{"x"}{"y"}{"z"}{"w"}', SCHEMA)


def test_extract_limits_validation(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/runtime.yaml").write_text(
        "agent_runtime: {schema_retries: 1, extract_max_chars: 0, extract_max_candidates: 5}\n")
    with pytest.raises(config.ConfigError):
        runtime.extract_limits(tmp_path)


# ---------- không rò giá trị ----------
SENSITIVE = [{"stories": "KHÁCH HÀNG ABC: hợp đồng 5 tỷ, email ceo@abc.vn"},
             {"stories": [{"title": "Bí mật nội bộ dài quá giới hạn"}]}]
S2 = {"type": "object", "required": ["stories"],
      "properties": {"stories": {"type": "array", "items": {"type": "object",
                                                            "properties": {"title": {"type": "string", "maxLength": 10}}}}}}


@pytest.mark.parametrize("data", SENSITIVE)
def test_stored_error_has_no_output_values(data):
    with pytest.raises(ValidationError) as ei:
        validate(data, S2)
    msg = runtime._err_msg(ei.value)
    for leak in ("ABC", "5 tỷ", "ceo@abc.vn", "Bí mật"):
        assert leak not in msg
    assert ei.value.validator in msg and "kiểu thực tế: str" in msg
    assert "Bí mật" in runtime._err_detail(ei.value) or "ABC" in runtime._err_detail(ei.value)


def test_schema_fail_reason_and_risk_have_no_values_but_retry_has_detail():
    from tests.contract.test_runtime_extract import CTX, IN, _seq
    from tests.contract.test_runtime_extract import GOOD
    bad = json.dumps({**GOOD, "business_context": "KHÁCH HÀNG ABC: hợp đồng 5 tỷ, email ceo@abc.vn"},
                     ensure_ascii=False)
    seen = []
    res = a1_bu.run(Blackboard("p"), None, CTX, IN, sender=_seq(bad, bad, seen=seen))
    assert res["error"] == "SCHEMA_FAIL"
    assert "ceo@abc.vn" not in res["reason"] and "5 tỷ" not in res["reason"]
    assert "5 tỷ" in seen[1]["messages"][-1]["content"]     # chi tiết chỉ đi qua gateway (gateway tự che email)


def test_invalid_input_reason_has_no_values():
    res = a1_bu.run(Blackboard("p"), None, {"project_id": "p"}, {"project_id": 123456789,
                                                                  "lead_brief": "BI-MAT-KHACH"},
                    sender=lambda *a: ("{}", {}))
    assert res["error"] == "INVALID_INPUT"
    assert "BI-MAT-KHACH" not in res["reason"] and "123456789" not in res["reason"]
