"""src/common: config loader một chỗ + JSONL."""
import pytest

from src.common import config, jsonl


def test_load_yaml_missing_file_raises_config_error(tmp_path):
    with pytest.raises(config.ConfigError, match="thiếu file"):
        config.load_yaml("config/khong_co.yaml", root=tmp_path)


def test_load_returns_fresh_object_each_call():
    a, b = config.load_yaml(config.BUDGETS), config.load_yaml(config.BUDGETS)
    assert a == b and a is not b                 # module giữ bản riêng, monkeypatch không lan


def test_require_missing_and_null():
    cfg = {"a": {"b": None, "c": 1}}
    assert config.require(cfg, "a.c", "x.yaml") == 1
    assert config.require(cfg, "a.b", "x.yaml", nullable=True) is None
    with pytest.raises(config.ConfigError, match="'a.b' không được null"):
        config.require(cfg, "a.b", "x.yaml")
    with pytest.raises(config.ConfigError, match="thiếu 'a.z'"):
        config.require(cfg, "a.z", "x.yaml")
    with pytest.raises(config.ConfigError, match="thiếu 'k'"):
        config.section({}, "k", "x.yaml")


def test_config_error_is_key_error():
    assert issubclass(config.ConfigError, KeyError)   # tương thích dispatcher.ConfigError cũ


def test_split_front_matter():
    meta, body = config.split_front_matter("---\nid: x\n---\nthân\n")
    assert meta == {"id": "x"} and body == "thân\n"
    text = "không frontmatter"
    assert config.split_front_matter(text) == ({}, text)


def test_agent_spec_meta_reads_subordinates():
    assert "c1_dev" in config.agent_spec_meta("b4_tech_lead")["subordinates"]


def test_jsonl_roundtrip_skips_broken_lines(tmp_path):
    p = tmp_path / "x" / "log.jsonl"
    jsonl.append(p, {"a": 1})
    with p.open("a", encoding="utf-8") as f:
        f.write("{hỏng\n\n")
    jsonl.append(p, {"b": "ễ"})
    assert jsonl.read(p) == [{"a": 1}, {"b": "ễ"}]
    assert jsonl.read(tmp_path / "none.jsonl") == [] and jsonl.read(None) == []
