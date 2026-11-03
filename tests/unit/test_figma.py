"""Figma adapter (H5): chỉ đọc, transport inject — KHÔNG gọi mạng."""
import json
import urllib.request
from pathlib import Path

import pytest
from jsonschema import validate

from src.tools.integrations import figma

SCHEMAS = Path(__file__).parents[2] / "specs/04_schemas"

FILE = {
    "name": "DS",
    "styles": {"S:1": {"name": "Brand/Primary", "styleType": "FILL"},
               "S:2": {"name": "Heading 1", "styleType": "TEXT"},
               "S:3": {"name": "Card shadow", "styleType": "EFFECT"},
               "S:4": {"name": "Ink 50%", "styleType": "FILL"}},
    "document": {"id": "0:0", "type": "DOCUMENT", "children": [
        {"id": "0:1", "name": "Page 1", "type": "CANVAS", "children": [
            {"id": "1:1", "name": "Home (mẫu)", "type": "FRAME", "children": [
                {"id": "1:2", "type": "RECTANGLE", "styles": {"fill": "S:1"},
                 "fills": [{"type": "SOLID", "color": {"r": 0.0588, "g": 0.4314, "b": 0.4314, "a": 1}}]},
                {"id": "1:3", "type": "TEXT", "styles": {"text": "S:2"},
                 "style": {"fontFamily": "Inter", "fontWeight": 700, "fontSize": 32, "lineHeightPx": 38.73,
                           "textAutoResize": "WIDTH"}},
                {"id": "1:4", "type": "RECTANGLE", "styles": {"effect": "S:3"},
                 "effects": [{"type": "DROP_SHADOW", "offset": {"x": 0, "y": 2}, "radius": 8,
                              "color": {"r": 0, "g": 0, "b": 0, "a": 0.25}}]},
                {"id": "1:5", "type": "RECTANGLE", "styles": {"fill": "S:4"},
                 "fills": [{"type": "SOLID", "color": {"r": 0, "g": 0, "b": 0, "a": 1}, "opacity": 0.5}]},
                {"id": "1:6", "type": "RECTANGLE",                 # màu rải rác, không style → bỏ
                 "fills": [{"type": "SOLID", "color": {"r": 1, "g": 0, "b": 0}}]}]},
            {"id": "2:1", "name": "Button", "type": "COMPONENT"},
            {"id": "2:2", "name": "note", "type": "TEXT"}]}]},
}
STYLES = {"meta": {"styles": [{"key": "k1", "node_id": "1:2", "name": "Brand/Primary", "style_type": "FILL"}]}}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test không được gọi mạng")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def _transport(calls, status=200):
    def t(method, url, headers, timeout):
        calls.append((method, url, headers))
        if status != 200:
            return status, b'{"err":"x"}'
        if "/styles" in url:
            return 200, json.dumps(STYLES).encode()
        if "/nodes" in url:
            ids = url.split("ids=")[1].replace("%3A", ":").replace("%2C", ",").split(",")
            return 200, json.dumps({"nodes": {i: {"document": {"id": i, "name": "Home (mẫu)",
                                   "children": [{"name": "Header"}, {"name": "List"}]}} for i in ids}}).encode()
        return 200, json.dumps(FILE).encode()
    return t


def test_token_from_env_and_get_only(monkeypatch):
    monkeypatch.delenv("FIGMA_TOKEN", raising=False)
    with pytest.raises(figma.FigmaError):
        figma.FigmaClient(transport=_transport([]))
    monkeypatch.setenv("FIGMA_TOKEN", "figd_test")
    calls = []
    c = figma.FigmaClient(transport=_transport(calls))
    assert [f["name"] for f in c.frames("AbC123")] == ["Home (mẫu)", "Button"]
    c.styles("AbC123")
    c.nodes("AbC123", ["1:1"])
    assert {m for m, _, _ in calls} == {"GET"}
    assert all(u.startswith("https://api.figma.com/v1/files/AbC123") for _, u, _ in calls)
    assert all(h["X-Figma-Token"] == "figd_test" for _, _, h in calls)
    assert not any(hasattr(c, n) for n in ("post", "put", "delete", "patch", "post_comment"))


def test_rejects_bad_key_and_http_error_hides_token():
    c = figma.FigmaClient(token="figd_SECRET", transport=_transport([], status=403))
    with pytest.raises(figma.FigmaError):
        c.file("../../me")
    with pytest.raises(figma.FigmaError) as e:
        c.file("AbC123")
    assert "403" in str(e.value) and "SECRET" not in str(e.value)


def test_export_tokens_to_ui_system_format():
    out = figma.export_tokens(FILE)
    t = out["tokens"]
    assert t["color"] == {"brand-primary": "#0F6E6E", "ink-50": "#00000080"}
    assert t["typography"]["heading-1"] == {"fontFamily": "Inter", "fontWeight": 700, "fontSize": 32,
                                            "lineHeightPx": 38.73}
    assert t["effect"]["card-shadow"] == "0px 2px 8px #00000040"
    css = out["tokens_css"]
    assert "--color-brand-primary: #0F6E6E;" in css and "--font-heading-1-size: 32px;" in css
    assert "#FF0000" not in css                                          # màu không style bị bỏ
    ui_schema = json.load((SCHEMAS / "ui_system.json").open(encoding="utf-8"))
    for k in ("tokens", "tokens_css"):
        validate(out[k], ui_schema["properties"][k])


def test_b3_input_design_system_plus_one_sample_screen():
    c = figma.FigmaClient(token="figd_x", transport=_transport([]))
    inp = figma.b3_input(c, "AbC123", "1:1", {"screens": ["home", "list", "detail"]})
    validate(inp, json.load((SCHEMAS / "b3_ui.in.json").open(encoding="utf-8")))
    assert inp["figma_tokens"]["tokens"]["color"]["brand-primary"] == "#0F6E6E"
    assert inp["sample_screen"] == {"id": "1:1", "name": "Home (mẫu)", "children": ["Header", "List"]}
