"""src/ops/escalation.py — NGƯỜI gỡ ESCALATED (retry | backtrack:<PHASE> | abandon) cho dự án thật: lõi, CLI
TTY, API dashboard. Lỗi thực tế 2026-11-04: dự án kẹt ESCALATED sau A1 SCHEMA_FAIL, người không có cách gỡ."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.blackboard.eventstore import EventStore
from src.blackboard.store import Blackboard
from src.gateway import llm_client, trace
from src.ops import common, escalation, gate, mock, pipeline, project
from src.orchestrator import autonomy, team_feed
from src.orchestrator.autonomy import Autonomy
from src.orchestrator.state_machine import Orchestrator

ROOT = Path(__file__).parents[2]
BRIEF = "client: Acme\nsource: email\nreceived_at: 2026-11-01\ndomain: fintech\nsummary: Ví nội bộ\n"
MACHINES = ["orchestrator", "Orchestrator", "c1-dev", "A1_BU", "gate_ui", "Gate-UI", "auto-L3", "AUTO",
            "huddle:a1_bu", " ", ""]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(autonomy, "STATE", tmp_path / "autonomy_state.json")
    monkeypatch.delenv("OA_EVENTSTORE_PATH", raising=False)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ev.sqlite3")


def _failing(agent, calls=None):
    """Sender mock, riêng `agent` trả văn bản + JSON thiếu khoá gốc (như lỗi thực tế A1)."""
    def send(model, system, messages, max_tokens):
        who = mock.identify(system)
        if calls is not None:
            calls.append((who, model))
        if who == agent:
            return "Phân tích:\n" + json.dumps({"domain": "fintech"}), {"total_tokens": 5}
        return mock.sender(model, system, messages, max_tokens)
    return send


def _recording(calls):
    def send(model, system, messages, max_tokens):
        calls.append((mock.identify(system), model))
        return mock.sender(model, system, messages, max_tokens)
    return send


@pytest.fixture
def pid(db, tmp_path):
    """Dự án kẹt ESCALATED ở A1 (PRESALES)."""
    p = tmp_path / "brief.yaml"
    p.write_text(BRIEF, encoding="utf-8")
    assert project.main(["new", "--brief", str(p), "--id", "P-E", "--db", db]) == 0
    o = pipeline.run(EventStore(db), "P-E", sender=_failing("a1_bu"))
    assert o.stop == "ESCALATED" and "a1_bu: SCHEMA_FAIL" in o.reason
    return "P-E"


def _state(db, pid):
    return common.state_of(Blackboard.load(pid, EventStore(db)))


def _board(db, pid):
    return Blackboard.load(pid, EventStore(db))


def _decisions(db, pid):
    return [e for e in _board(db, pid).get("trace_log") or []
            if e.get("event") == "DECISION" and e.get("by") == escalation.BY]


def _resolve(db, pid, action, who="Lan Nguyen", **kw):
    return escalation.resolve(pid, action, who, "dashboard", store=EventStore(db), **kw)


# ---------- lõi ----------
def test_view_reports_reason_phase_agent_and_options(db, pid):
    v = escalation.view(EventStore(db), pid)
    assert v["escalated"] and v["from_state"] == "PRESALES" and v["agent"] == "a1_bu"
    assert "business_context" in v["reason"]
    assert v["options"] == ["retry", "backtrack:PRESALES", "abandon"]
    assert v["packet_hash"].startswith("sha256:")


def test_human_todo_points_to_human_resolve():
    todo = " ".join(pipeline.Outcome("P-X", "ESCALATED", "ESCALATED", reason="r").human_todo())
    assert "python -m src.ops.escalation resolve --id P-X" in todo and "KHÔNG" in todo
    assert "/api/projects/P-X/escalation/" in todo


def test_retry_reruns_failed_agent_with_higher_tier_then_continues(db, pid):
    res = _resolve(db, pid, "retry", note="đã sửa runtime")
    assert res["state"] == "PRESALES" and res["signals"] == ["retry_2"] and res["agent"] == "a1_bu"
    assert _state(db, pid) == "PRESALES"
    d = _decisions(db, pid)[-1]
    assert d["decision"] == "retry" and d["signer"] == "Lan Nguyen" and d["channel"] == "dashboard"
    b = _board(db, pid)
    rr = b.get("risk_register")
    last = rr[-1]
    assert last["status"] == "resolved" and last["resolved_by"] == "Lan Nguyen" and last["via"] == "gate_ui"
    assert rr[last["resolves"]]["source_agent"] == "orchestrator"
    assert not [r for r in team_feed.open_risks(rr) if r.get("source_agent") == "orchestrator"]
    assert b.get("project_status")["state"] == "PRESALES"
    calls = []
    o = pipeline.run(EventStore(db), pid, sender=_recording(calls))
    assert o.stop == "AWAIT_GATE" and o.gate == "G1" and o.ran[0] == "a1_bu"
    assert calls[0] == ("a1_bu", llm_client._LOCK["opus"])          # routing a1_bu escalate_to: opus
    assert _decisions(db, pid)[-1]["decision"] == "retry_done"
    assert pipeline.Runner(EventStore(db), pid).retry is None          # retry đã tiêu → lần sau tier thường


def test_first_call_without_retry_uses_default_tier(db, tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text(BRIEF, encoding="utf-8")
    project.main(["new", "--brief", str(p), "--id", "P-T", "--db", db])
    calls = []
    pipeline.run(EventStore(db), "P-T", sender=_recording(calls))
    assert calls[0] == ("a1_bu", llm_client._LOCK["sonnet"])


def test_retry_that_fails_again_escalates_again(db, pid):
    _resolve(db, pid, "retry")
    o = pipeline.run(EventStore(db), pid, sender=_failing("a1_bu"))
    assert o.stop == "ESCALATED"
    v = escalation.view(EventStore(db), pid)
    assert v["escalated"] and v["risk_index"] is not None and v["from_state"] == "PRESALES"


def test_backtrack_from_discovery_revokes_g1_and_reruns(db, tmp_path, pid):
    _resolve(db, pid, "retry")
    assert pipeline.run(EventStore(db), pid, sender=mock.sender).gate == "G1"
    ax = Autonomy(state_path=tmp_path / "ax.json", reviews_log=tmp_path / "rv.jsonl")
    gate.sign(pid, "G1", "Lan", "dashboard", store=EventStore(db), autonomy=ax, feedback_log=tmp_path / "fb.jsonl")
    o = pipeline.run(EventStore(db), pid, sender=_failing("b1_ba"))
    assert o.stop == "ESCALATED"
    v = escalation.view(EventStore(db), pid)
    assert v["from_state"] == "DISCOVERY" and v["agent"] == "b1_ba"
    with pytest.raises(escalation.EscalationError):
        _resolve(db, pid, "backtrack:BUILD")                          # chỉ về phase ≤ phase lỗi
    res = _resolve(db, pid, "backtrack:PRESALES")
    assert res["state"] == "PRESALES" and res["rerun"] == ["PRESALES", "DISCOVERY"]
    assert not _board(db, pid).is_signed("G1")
    o = pipeline.run(EventStore(db), pid, sender=mock.sender)
    assert o.gate == "G1" and o.ran == ["a1_bu", "a2_estimation", "a3_proposal"]   # chạy lại dù zone đã có


def test_abandon_closes_project(db, pid):
    res = _resolve(db, pid, "abandon", note="client huỷ")
    assert res["state"] == "ABANDONED" and res["next"] is None
    assert _state(db, pid) == "ABANDONED"
    o = pipeline.run(EventStore(db), pid, sender=mock.sender)
    assert o.stop == "ABANDONED" and o.ran == []
    with pytest.raises(escalation.EscalationError) as e:
        _resolve(db, pid, "retry")
    assert e.value.kind == "state"
    assert project.main(["run", "--id", pid, "--mock", "--db", db]) == 0


@pytest.mark.parametrize("who", MACHINES)
def test_machine_cannot_resolve(db, pid, who):
    n = EventStore(db).last_seq(pid)
    for act in ("retry", "backtrack:PRESALES", "abandon"):
        with pytest.raises(escalation.EscalationError):
            _resolve(db, pid, act, who=who)
    assert EventStore(db).last_seq(pid) == n and _state(db, pid) == "ESCALATED"


def test_bad_action_channel_state_hash_refused(db, pid):
    n = EventStore(db).last_seq(pid)
    for act in ("", "retry2", "backtrack:AWAIT_G1", "backtrack:RELEASED", "sign"):
        with pytest.raises(escalation.EscalationError):
            _resolve(db, pid, act)
    with pytest.raises(escalation.EscalationError):
        escalation.resolve(pid, "retry", "Lan", "gate_ui", store=EventStore(db))      # kênh lạ
    with pytest.raises(escalation.EscalationError) as e:
        _resolve(db, pid, "retry", expected_hash="sha256:khac")
    assert e.value.kind == "hash"
    with pytest.raises(escalation.EscalationError) as e:
        _resolve(db, "P-NONE", "retry")
    assert e.value.kind == "missing"
    assert EventStore(db).last_seq(pid) == n


def test_legacy_escalation_without_decision_event_infers_phase(db, tmp_path):
    """Dự án ESCALATED từ bản cũ (không có DECISION escalate) — như dự án thật ở máy công ty."""
    p = tmp_path / "b.yaml"
    p.write_text(BRIEF, encoding="utf-8")
    project.main(["new", "--brief", str(p), "--id", "P-OLD", "--db", db])
    b = Blackboard.resume("P-OLD", EventStore(db))
    from src.orchestrator import status as pstatus
    pstatus.update(b, "PRESALES")
    o = Orchestrator(b, "PRESALES")
    o.escalate("a1_bu: SCHEMA_FAIL — 'business_context' is a required property")
    pstatus.update(b, o.state)
    v = escalation.view(EventStore(db), "P-OLD")
    assert v["from_state"] == "PRESALES" and v["agent"] == "a1_bu" and "retry" in v["options"]
    assert _resolve(db, "P-OLD", "retry")["state"] == "PRESALES"


# ---------- CLI terminal ----------
class _Stdin(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


def _cli(monkeypatch, db, args, text, tty=True):
    monkeypatch.setattr(escalation.sys, "stdin", _Stdin(text, tty=tty))
    return escalation.main([*args, "--db", db])


def test_cli_retry_with_human_typing(monkeypatch, db, pid, capsys):
    rc = _cli(monkeypatch, db, ["resolve", "--id", pid, "--action", "retry"], "Lan Nguyen\nXAC NHAN\n")
    out = capsys.readouterr().out
    assert rc == 0 and "packet_hash" in out and "ĐÃ GỠ ESCALATED" in out and "project run" in out
    d = _decisions(db, pid)[-1]
    assert d["signer"] == "Lan Nguyen" and d["channel"] == "terminal" and _state(db, pid) == "PRESALES"


@pytest.mark.parametrize("text,tty", [
    ("Lan\nXAC NHAN\n", False),
    ("orchestrator\nXAC NHAN\n", True),
    ("Gate_UI\nXAC NHAN\n", True),
    ("Lan\nxac nhan\n", True),
    ("Lan\n", True),
    ("", True),
])
def test_cli_refusals_write_nothing(monkeypatch, db, pid, capsys, text, tty):
    n = EventStore(db).last_seq(pid)
    for act in ("retry", "abandon", "backtrack:PRESALES"):
        rc = _cli(monkeypatch, db, ["resolve", "--id", pid, "--action", act], text, tty=tty)
        assert rc != 0 and "TỪ CHỐI" in capsys.readouterr().err
    assert EventStore(db).last_seq(pid) == n


def test_cli_no_signer_arg(db, pid):
    for flag in ("--signer", "--by", "--confirmed-by", "--reviewer"):
        with pytest.raises(SystemExit):
            escalation.main(["resolve", "--id", pid, "--action", "retry", flag, "Lan", "--db", db])


def test_cli_subprocess_without_tty_refused(db, pid):
    r = subprocess.run([sys.executable, "-m", "src.ops.escalation", "resolve", "--id", pid, "--action", "retry",
                        "--db", db], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
                       input="Lan\nXAC NHAN\n", capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "TTY" in r.stderr and _state(db, pid) == "ESCALATED"


# ---------- API dashboard ----------
@pytest.fixture
def client(monkeypatch, db):
    tc = pytest.importorskip("fastapi.testclient")
    from src.server import app as srv
    monkeypatch.setattr(srv, "EVENTSTORE", EventStore(db, readonly=True))
    monkeypatch.setattr(srv, "GATE_STORE", EventStore(db))
    return tc.TestClient(srv.app, base_url="http://localhost")   # ghi chỉ từ Host cục bộ (app.write_refusal)


def _post(client, pid, action, **body):
    return client.post(f"/api/projects/{pid}/escalation/{action}", json=body)


def test_api_get_and_retry(client, db, pid):
    v = client.get(f"/api/projects/{pid}/escalation").json()
    assert v["escalated"] and v["agent"] == "a1_bu"
    r = _post(client, pid, "retry", signer="Lan Nguyen", typed_confirm="XAC NHAN", packet_hash=v["packet_hash"])
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "PRESALES" and r.json()["view"]["escalated"] is False
    assert _post(client, pid, "retry", signer="Lan", typed_confirm="XAC NHAN").status_code == 409  # hết ESCALATED
    assert client.get("/api/projects/P-NONE/escalation").status_code == 404


def test_api_backtrack_and_abandon_paths(client, db, pid):
    r = _post(client, pid, "backtrack:PRESALES", signer="Lan", typed_confirm="XAC NHAN")
    assert r.status_code == 200 and r.json()["state"] == "PRESALES"


@pytest.mark.parametrize("who", ["orchestrator", "C1_DEV", "gate_ui", "AUTO-L3", ""])
def test_api_machine_or_missing_signer_400(client, db, pid, who):
    n = EventStore(db).last_seq(pid)
    for act in ("retry", "abandon"):
        assert _post(client, pid, act, signer=who, typed_confirm="XAC NHAN").status_code == 400
    assert EventStore(db).last_seq(pid) == n


def test_api_typed_confirm_and_bad_action(client, db, pid):
    n = EventStore(db).last_seq(pid)
    for body in ({"signer": "Lan"}, {"signer": "Lan", "typed_confirm": "xac nhan"}):
        assert _post(client, pid, "abandon", **body).status_code == 400
    assert _post(client, pid, "nuke", signer="Lan", typed_confirm="XAC NHAN").status_code == 400
    assert _post(client, pid, "retry", signer="Lan", typed_confirm="XAC NHAN",
                 packet_hash="sha256:khac").status_code == 409
    assert client.post(f"/api/projects/{pid}/escalation/retry", content=b"x",
                       headers={"Content-Type": "application/json"}).status_code == 400
    assert EventStore(db).last_seq(pid) == n


# ---------- chặn Claude ----------
def test_claude_settings_deny_escalation_resolve():
    perms = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]
    assert "Bash(python -m src.ops.escalation:*)" in perms["deny"]
    assert "Bash(python3 -m src.ops.escalation:*)" in perms["deny"]
    assert any("api/projects" in d and "escalation" in d for d in perms["deny"])
    assert not any("escalation" in a for a in perms["allow"] + perms.get("ask", []))


def test_claude_settings_pytest_tests_and_git_diff_review_pr2():
    perms = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]
    py = [a for a in perms["allow"] if "pytest" in a]
    assert sorted(py) == ["Bash(pytest -q tests/)", "Bash(python -m pytest -q tests/)"]
    assert not any(a.endswith(":*)") and "pytest" in a for a in perms["allow"])
    assert {"Edit(tests/**)", "Write(tests/**)"} <= set(perms["ask"])
    assert any(d.startswith("Bash(git diff --no-index") for d in perms["deny"])
