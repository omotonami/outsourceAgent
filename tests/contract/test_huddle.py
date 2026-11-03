"""H3 Huddle — contract test, offline (sender giả)."""
import json
import uuid
from pathlib import Path

import pytest
import yaml
from jsonschema import validate

from src.agents import clarify, huddle
from src.blackboard.store import Blackboard, PermissionDenied
from src.orchestrator import gate_view, huddle_trigger

ROOT = Path(__file__).parents[2]
SCHEMA = json.loads((ROOT / "specs/04_schemas/decision_record.json").read_text(encoding="utf-8"))
PRODUCT_ZONES = ["lead_brief", "business_context", "estimate", "proposal", "requirements",
                 "ui_system", "design_spec", "architecture", "task_backlog", "build_results",
                 "security_report", "project_status", "gate_signatures", "clarify_answers"]
GOOD = {"question": "Chọn cổng FX?", "options": ["Wise", "Nium"], "chosen": "Wise",
        "rationale": "phí thấp, có API SG", "risks": ["phụ thuộc vendor"],
        "open_questions": ["Client đã có hợp đồng FX chưa?"]}


def _board():
    return Blackboard("hud-" + uuid.uuid4().hex[:8])   # project riêng → budget gateway không dính nhau


def _sender(converge_at=None, decider=None, tokens=10):
    """Giả LLM theo vai (đọc từ system prompt). decider: list output lần lượt."""
    calls = {"proposer": 0, "critic": 0, "decider": 0}
    dec = list(decider or [json.dumps(GOOD)])

    def send(model, system, messages, max_tokens):
        role = "proposer" if "PROPOSER" in system else "critic" if "CRITIC" in system else "decider"
        calls[role] += 1
        n = calls[role]
        if role == "proposer":
            out = {"summary": f"đề xuất v{n}", "options": ["Wise", "Nium"]}
        elif role == "critic":
            out = {"summary": f"phản biện v{n}", "risks": [f"rủi ro {n}"],
                   "open_questions": [f"Hỏi client {n}?"], "converged": converge_at == n}
        else:
            return dec[min(n - 1, len(dec) - 1)], {"total_tokens": tokens}
        return json.dumps(out, ensure_ascii=False), {"total_tokens": tokens}
    send.calls = calls
    return send


def _run(board, sender, phase="presales"):
    return huddle.run(board, phase, "Chọn cổng FX?", {"x": 1}, {"project_id": board.project_id},
                      sender=sender, trigger="ambiguities")


def test_three_round_flow_valid_record_and_log():
    b, s = _board(), _sender(converge_at=None)
    out = _run(b, s)
    rec = out["decision_record"]
    validate(rec, SCHEMA)
    assert rec["chosen"] == "Wise" and rec["rounds"] == 3 and rec["stopped_reason"] == "decided"
    assert rec["participants"] == ["a1_bu", "a2_estimation"]
    log = b.get("huddle_log")
    assert [t["role"] for t in log] == ["proposer", "critic"] * 3 + ["decider"]
    assert all({"round", "agent", "role", "summary"} <= set(t) for t in log)
    assert b.get("decision_records") == [{**rec, "source_agent": "huddle"}]


def test_stops_exactly_at_config_max_rounds():
    max_rounds = huddle.config()["max_rounds"]
    assert max_rounds == 3                                # số vòng nằm trong config, không hard-code
    b, s = _board(), _sender(converge_at=None)
    out = _run(b, s)
    assert s.calls == {"proposer": max_rounds, "critic": max_rounds, "decider": 1}
    assert max(t["round"] for t in b.get("huddle_log")) == max_rounds
    assert out["decision_record"]["rounds"] == max_rounds


def test_converged_early_ends_before_max():
    b, s = _board(), _sender(converge_at=1)
    out = _run(b, s)
    assert out["decision_record"]["rounds"] == 1 and s.calls["proposer"] == 1


def test_max_rounds_read_from_config(monkeypatch):
    cfg = {**huddle.config(), "max_rounds": 2}
    monkeypatch.setattr(huddle, "config", lambda: cfg)
    b, s = _board(), _sender()
    assert _run(b, s)["decision_record"]["rounds"] == 2 and s.calls["critic"] == 2


def test_decider_schema_fail_retry_then_escalate():
    b = _board()
    s = _sender(decider=["không phải json", json.dumps({**GOOD, "chosen": "Ngoài options"})])
    out = _run(b, s)
    assert out["escalate"] is True and out["error"] == "SCHEMA_FAIL"
    assert s.calls["decider"] == 2                        # 1 lần + 1 retry
    assert not b.get("decision_records")
    assert b.get("huddle_log")[-1]["role"] == "system"


def test_decider_fail_once_then_ok():
    b = _board()
    s = _sender(decider=[json.dumps({"question": "x"}), json.dumps(GOOD)])
    out = _run(b, s)
    assert out["ok"] and s.calls["decider"] == 2


def test_over_budget_stops_with_chosen_null():
    cap = huddle.config()["tokens"]
    b, s = _board(), _sender(tokens=cap // 2 + 1)        # proposer + critic vượt cap ở vòng 1
    out = _run(b, s)
    rec = out["decision_record"]
    validate(rec, SCHEMA)
    assert rec["chosen"] is None and rec["stopped_reason"] == "budget_exceeded"
    assert s.calls["decider"] == 0 and s.calls["proposer"] == 1 and rec["rounds"] == 1
    assert rec["open_questions"][0] == "Chọn cổng FX?" and "Hỏi client 1?" in rec["open_questions"]
    assert rec["options"] == ["Wise", "Nium"] and rec["risks"] == ["rủi ro 1"]


def test_huddle_cannot_write_product_zones():
    b = _board()
    for z in PRODUCT_ZONES:
        with pytest.raises(PermissionDenied):
            b.write(z, {}, agent="huddle")
        with pytest.raises(PermissionDenied):            # owner_write → append bị chặn kể cả agent tham gia
            b.append(z, {}, agent="a1_bu")
    with pytest.raises(PermissionDenied):
        b.append("decision_records", GOOD, agent="a1_bu")  # chỉ "huddle" ghi decision_records
    with pytest.raises(PermissionError):
        huddle._append(b, "requirements", {}, agent="b1_ba")


def test_huddle_run_leaves_product_zones_untouched():
    b = _board()
    b.write("business_context", {"domain": "fintech", "modules": [], "ambiguities": ["a", "b", "c"]},
            agent="a1_bu")
    before = {z: b.get(z) for z in PRODUCT_ZONES}
    _run(b, _sender())
    assert {z: b.get(z) for z in PRODUCT_ZONES} == before
    assert set(b._data) == {"business_context", "huddle_log", "decision_records"}


def test_zone_files_in_sync():
    for rel in ("specs/05_blackboard.yaml", "src/blackboard/permissions.yaml"):
        z = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))["zones"]
        assert z["huddle_log"]["mode"] == "append_only" and "huddle" in z["huddle_log"]["writers"]
        assert z["decision_records"] == {"writers": ["huddle"], "mode": "append_only"}
        roles = huddle.config()["roles"].values()
        assert {a for r in roles for a in r.values()} <= set(z["huddle_log"]["writers"])
    for rel in ("specs/03_routing.yaml", "src/gateway/routing.yaml"):
        assert "huddle" in yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))["agents"]


# ---------- trigger (Code, không LLM) ----------
def _bc(b, n):
    b.write("business_context", {"domain": "fintech", "modules": [],
                                 "ambiguities": [f"Q{i}?" for i in range(n)]}, agent="a1_bu")


def test_presales_trigger_threshold():
    b = _board(); _bc(b, 2)
    assert huddle_trigger.check(b, "PRESALES") is None
    b = _board(); _bc(b, 3)
    t = huddle_trigger.check(b, "PRESALES")
    assert t["phase"] == "presales" and t["trigger"] == "ambiguities"
    assert huddle_trigger.presales(b, threshold=4) is None
    huddle.run(b, t["phase"], t["question"], t["context"], {"project_id": b.project_id},
               sender=_sender(), trigger=t["trigger"])
    assert huddle_trigger.check(b, "PRESALES") is None     # đã có record → không trigger lại


def _disc(b, stories, screens, nfr=None):
    req = {"stories": stories, "open_questions": []}
    if nfr is not None:
        req["nfr"] = nfr
    b.write("requirements", req, agent="b1_ba")
    b.write("design_spec", {"flows": [], "screens": screens}, agent="b2_ux")


ST = {"as_a": "user", "i_want": "KYC", "so_that": "dùng ví", "AC": ["Given..When..Then..; p95 < 2s"]}


def test_discovery_no_conflict_no_trigger():
    b = _board()
    _disc(b, [ST], [{"id": "SC1", "purpose": "kyc", "states": [], "story_refs": ["S1"]}], nfr=["p95 < 2s"])
    assert huddle_trigger.check(b, "DISCOVERY") is None


def test_discovery_conflicts_trigger():
    b = _board()
    st2 = {**ST, "i_want": "Chuyển tiền", "AC": ["Given..When..Then.."]}
    _disc(b, [ST, st2], [{"id": "SC1", "purpose": "kyc", "states": [], "story_refs": ["KYC", "S9"]}],
          nfr=["PDPA mã hoá dữ liệu"])
    t = huddle_trigger.check(b, "discovery")
    c = t["context"]["conflicts"]
    assert t["phase"] == "discovery" and t["trigger"] == "conflicts"
    assert any("Chuyển tiền" in x for x in c)              # story không có screen
    assert any("S9" in x for x in c)                       # screen trỏ story không tồn tại
    assert any("PDPA" in x for x in c)                     # NFR không được phủ
    assert huddle_trigger.check(b, "BUILD") is None


# ---------- Clarify + gate ----------
def test_open_questions_flow_into_clarify():
    b = _board(); _bc(b, 1)
    _run(b, _sender(converge_at=1))
    p = clarify.pending(b)
    assert p == ["Q0?", "Client đã có hợp đồng FX chưa?"]
    clarify.resolve(b, "Q0?", "x")
    clarify.resolve(b, "Client đã có hợp đồng FX chưa?", "chưa")
    assert clarify.done(b)


def test_gate_view_shows_records_not_transcript():
    b = _board()
    _run(b, _sender(), phase="presales")
    g1 = gate_view.decision_records(b, "G1")
    assert len(g1) == 1 and g1[0]["chosen"] == "Wise"
    assert "source_agent" not in g1[0] and "summary" not in json.dumps(g1)
    assert gate_view.decision_records(b, "G2") == [] and gate_view.decision_records(b, "G3") == []


def test_server_gate_payload_includes_decision_records():
    app = pytest.importorskip("src.server.app")
    app.S.reset(True)
    app.S.board.append("decision_records", {**GOOD, "participants": ["a1_bu"], "rounds": 1,
                                             "phase": "presales"}, agent="huddle")
    app.S.board.append("huddle_log", {"round": 1, "agent": "a1_bu", "role": "proposer",
                                      "summary": "TRANSCRIPT-SECRET"}, agent="a1_bu")
    app.S.i = next(i for i, s in enumerate(app.S.steps) if s.get("gate") == "G1")
    out = app._do_step()
    assert out["status"] == "gate" and out["decision_records"][0]["chosen"] == "Wise"
    st = app.state()
    assert st["gate_decisions"][0]["question"] == GOOD["question"]
    assert "TRANSCRIPT-SECRET" not in json.dumps({**out, **st}, ensure_ascii=False)
    assert app.gate_decisions("G1")["decision_records"] == st["gate_decisions"]
    app.S.reset(True)
