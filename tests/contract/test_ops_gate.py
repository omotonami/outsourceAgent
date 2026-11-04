"""src/ops/gate.py — NGƯỜI ký / trả lại gate G1–G4 cho dự án thật (event store): lõi, CLI TTY, API dashboard.
Offline (sender mock), store SQLite tạm."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.blackboard.eventstore import EventStore
from src.blackboard.store import Blackboard
from src.gateway import trace
from src.ops import common, gate, pipeline, project
from src.orchestrator import autonomy, gate_view
from src.orchestrator.autonomy import Autonomy

ROOT = Path(__file__).parents[2]
BRIEF = """client: Acme Secret Bank
source: email
received_at: 2026-11-01
domain: fintech
summary: Ví điện tử nội bộ
"""
MACHINES = ["orchestrator", "Orchestrator", "ORCHESTRATOR.dispatcher", "c1-dev", "A1_BU", "gate_ui", "Gate-UI",
            "auto-L3", "AUTO", "huddle:a1_bu", " ", ""]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(autonomy, "STATE", tmp_path / "autonomy_state.json")
    monkeypatch.delenv("OA_EVENTSTORE_PATH", raising=False)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ev.sqlite3")


@pytest.fixture
def ax(tmp_path):
    return Autonomy(state_path=tmp_path / "ax_state.json", reviews_log=tmp_path / "reviews.jsonl")


@pytest.fixture
def fb(tmp_path):
    return tmp_path / "gate_feedback.jsonl"


@pytest.fixture
def pid(db, tmp_path):
    p = tmp_path / "brief.yaml"
    p.write_text(BRIEF, encoding="utf-8")
    assert project.main(["new", "--brief", str(p), "--id", "P-G", "--db", db]) == 0
    assert project.main(["run", "--id", "P-G", "--mock", "--db", db]) == 0
    return "P-G"


def _state(db, pid):
    return common.state_of(Blackboard.load(pid, EventStore(db)))


def _signs(db, pid):
    return [r.payload for r in EventStore(db).read(pid) if r.kind == "sign"]


def _run(db, pid):
    return pipeline.run(EventStore(db), pid, sender=__import__("src.ops.mock", fromlist=["sender"]).sender)


def _human_sign(db, pid, g, ax, fb, **kw):
    """Giả lập NGƯỜI ký qua dashboard (lõi, via gate_ui)."""
    return gate.sign(pid, g, "Lan Nguyen", "dashboard", store=EventStore(db), autonomy=ax, feedback_log=fb, **kw)


class _Stdin(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


# ---------- lõi ----------
def test_sign_then_run_continues_to_g2(db, pid, ax, fb):
    assert _state(db, pid) == "AWAIT_G1"
    res = _human_sign(db, pid, "G1", ax, fb)
    assert res["signer"] == "Lan Nguyen" and res["reviews"] == 3
    s = _signs(db, pid)
    assert s == [{"gate": "G1", "by": "Lan Nguyen", "via": "gate_ui"}]
    # autonomy.record_review: duyệt không sửa cho từng agent G1
    rev = [json.loads(x) for x in Path(ax.reviews_log).read_text(encoding="utf-8").splitlines()]
    assert {r["agent"] for r in rev} == set(gate_view.GATE_AGENTS["G1"]) and not any(r["edited"] for r in rev)
    o = _run(db, pid)
    assert o.stop == "AWAIT_GATE" and o.gate == "G2" and _state(db, pid) == "AWAIT_G2"


def test_signature_visible_to_new_process(db, pid, ax, fb):
    _human_sign(db, pid, "G1", ax, fb)
    code = ("import sys;from src.blackboard.eventstore import EventStore;from src.blackboard.store import Blackboard;"
            f"b=Blackboard.load('{pid}',EventStore(r'{db}',readonly=True));sys.exit(0 if b.is_signed('G1') else 3)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, "-m", "src.ops.project", "run", "--id", pid, "--mock", "--db", db],
                       cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and "AWAIT_G2" in r.stdout, r.stderr


def test_reject_backtracks_and_records_feedback_with_task_type(db, pid, ax, fb):
    n_before = {r.payload.get("source_agent") for r in EventStore(db).read(pid)}
    res = gate.reject(pid, "G1", "Lan Nguyen", "dashboard", "giá quá cao, tách module Payments",
                      store=EventStore(db), autonomy=ax, feedback_log=fb)
    assert res["backtrack_to"] == "PRESALES" and res["rerun"] == ["PRESALES"]
    assert _state(db, pid) == "PRESALES" and _signs(db, pid) == []
    rows = [json.loads(x) for x in fb.read_text(encoding="utf-8").splitlines()]
    assert {r["agent"] for r in rows} == set(gate_view.GATE_AGENTS["G1"])
    assert all(r["task_type"] == "presales" and r["gate"] == "G1" and r["reviewer"] == "Lan Nguyen"
               and r["after"] == "giá quá cao, tách module Payments" for r in rows)
    assert n_before is not None
    # run chạy LẠI agent presales (không bỏ qua vì zone đã có) rồi dừng lại ở G1
    o = _run(db, pid)
    assert o.stop == "AWAIT_GATE" and o.gate == "G1"
    assert o.ran == ["a1_bu", "a2_estimation", "a3_proposal"]
    o2 = _run(db, pid)                       # rerun_done đã ghi → lần sau không chạy lại
    assert o2.ran == [] and o2.gate == "G1"


def test_reject_g2_back_to_presales_revokes_g1(db, pid, ax, fb):
    _human_sign(db, pid, "G1", ax, fb)
    _run(db, pid)
    assert _state(db, pid) == "AWAIT_G2"
    res = gate.reject(pid, "G2", "Lan Nguyen", "terminal", "đổi scope", "PRESALES", store=EventStore(db),
                      autonomy=ax, feedback_log=fb)
    assert res["rerun"] == ["PRESALES", "DISCOVERY"]
    b = Blackboard.load(pid, EventStore(db))
    assert not b.is_signed("G1") and _state(db, pid) == "PRESALES"
    with pytest.raises(gate.GateError):
        gate.reject(pid, "G1", "Lan Nguyen", "terminal", "x", "DISCOVERY", store=EventStore(db))


@pytest.mark.parametrize("who", MACHINES)
def test_machine_signer_refused_core(db, pid, ax, fb, who):
    n = EventStore(db).last_seq(pid)
    with pytest.raises(gate.GateError) as e:
        gate.sign(pid, "G1", who, "dashboard", store=EventStore(db), autonomy=ax, feedback_log=fb)
    assert e.value.kind == "machine"
    with pytest.raises(gate.GateError):
        gate.reject(pid, "G1", who, "dashboard", "x", store=EventStore(db), autonomy=ax, feedback_log=fb)
    assert EventStore(db).last_seq(pid) == n and _signs(db, pid) == []


def test_wrong_gate_or_channel_refused(db, pid, ax, fb):
    n = EventStore(db).last_seq(pid)
    for g in ("G2", "G3", "G4"):
        with pytest.raises(gate.GateError) as e:
            _human_sign(db, pid, g, ax, fb)
        assert e.value.kind == "state"
    with pytest.raises(gate.GateError):
        gate.sign(pid, "G9", "Lan", "dashboard", store=EventStore(db))
    with pytest.raises(gate.GateError):
        gate.sign(pid, "G1", "Lan", "orchestrator", store=EventStore(db))      # kênh không phải người
    with pytest.raises(gate.GateError) as e:
        gate.sign("P-NONE", "G1", "Lan", "dashboard", store=EventStore(db))
    assert e.value.kind == "missing"
    with pytest.raises(gate.GateError):
        gate.reject(pid, "G1", "Lan", "dashboard", "  ", store=EventStore(db))  # thiếu lý do
    assert EventStore(db).last_seq(pid) == n


def test_hash_mismatch_refused(db, pid, ax, fb):
    v = gate.view(EventStore(db), pid)
    Blackboard.resume(pid, EventStore(db)).append(
        "risk_register", {"risk": "mới", "severity": "High", "status": "open"}, agent="a1_bu")
    with pytest.raises(gate.GateError) as e:
        _human_sign(db, pid, "G1", ax, fb, expected_hash=v["packet_hash"])
    assert e.value.kind == "hash" and _signs(db, pid) == []


def test_view_has_packet_without_transcript(db, pid):
    assert project.main(["huddle", "--id", pid, "--phase", "presales", "--mock", "--db", db]) == 0
    v = gate.view(EventStore(db), pid)
    assert v["gate"] == "G1" and v["packet_hash"].startswith("sha256:")
    assert set(v["packet"]["zones"]) == {"business_context", "estimate", "proposal"}
    assert v["packet"]["highlights"]["assumptions"] and len(v["packet"]["decision_records"]) == 1
    blob = json.dumps(v, ensure_ascii=False)
    assert "huddle_log" not in blob and "thinking" not in blob and "transcript" not in blob


def _to_g4(db, pid, ax, fb):
    for g in ("G1", "G2", "G3"):
        _human_sign(db, pid, g, ax, fb)
        _run(db, pid)
    assert _state(db, pid) == "AWAIT_G4"


def test_g4_with_open_high_finding_still_blocked(db, pid, ax, fb):
    _to_g4(db, pid, ax, fb)
    b = Blackboard.resume(pid, EventStore(db))
    b.write("security_report", {"findings": [{"type": "sqli", "severity": "High"}],
                                "gate": "block"}, agent="d1_soc")
    with pytest.raises(gate.GateError) as e:
        _human_sign(db, pid, "G4", ax, fb)
    assert e.value.kind == "guard" and "High/Critical" in str(e.value)
    assert not Blackboard.load(pid, EventStore(db)).is_signed("G4")
    assert _run(db, pid).stop == "AWAIT_GATE" and _state(db, pid) == "AWAIT_G4"


def test_g4_clean_releases(db, pid, ax, fb):
    _to_g4(db, pid, ax, fb)
    _human_sign(db, pid, "G4", ax, fb)
    assert _run(db, pid).stop == "RELEASED"
    assert all(s["via"] == "gate_ui" and s["by"] == "Lan Nguyen" for s in _signs(db, pid))


def test_edited_approval_goes_to_gate_feedback(db, pid, ax, fb):
    res = _human_sign(db, pid, "G1", ax, fb, edited=True, note="sửa rate")
    assert res["reviews"] == 0 and not Path(ax.reviews_log).exists()
    rows = [json.loads(x) for x in fb.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 and all(r["task_type"] == "presales" and r["field"] == "gate_approve_edited" for r in rows)


def test_review_log_failure_keeps_signature(db, pid, fb):
    class Broken:
        cfg = {"gate_task_types": {"G1": "presales"}}

        def record_review(self, *a, **k):
            raise OSError("disk full")
    gate.sign(pid, "G1", "Lan", "dashboard", store=EventStore(db), autonomy=Broken(), feedback_log=fb)
    b = Blackboard.load(pid, EventStore(db))
    assert b.is_signed("G1") and "disk full" in b.get("risk_register")[-1]["risk"]


# ---------- CLI terminal ----------
def _cli(monkeypatch, db, args, text, tty=True, ax=None, fb=None):
    monkeypatch.setattr(gate.sys, "stdin", _Stdin(text, tty=tty))
    if ax is not None:
        monkeypatch.setattr(gate, "Autonomy", lambda: ax)
    if fb is not None:
        monkeypatch.setattr(gate.gate_feedback, "LOG", fb)
    return gate.main([*args, "--db", db])


def test_cli_sign_with_human_typing(monkeypatch, db, pid, ax, fb, capsys):
    rc = _cli(monkeypatch, db, ["sign", "--id", pid, "--gate", "G1"], "Lan Nguyen\nXAC NHAN\n", ax=ax, fb=fb)
    out = capsys.readouterr().out
    assert rc == 0 and "packet_hash" in out and "ĐÃ DUYỆT G1" in out
    assert _signs(db, pid) == [{"gate": "G1", "by": "Lan Nguyen", "via": "gate_ui"}]


def test_cli_reject(monkeypatch, db, pid, ax, fb, capsys):
    rc = _cli(monkeypatch, db, ["reject", "--id", pid, "--gate", "G1", "--reason", "giá cao"],
              "Lan\nXAC NHAN\n", ax=ax, fb=fb)
    assert rc == 0 and _state(db, pid) == "PRESALES"


@pytest.mark.parametrize("text,tty", [
    ("Lan\nXAC NHAN\n", False),               # không TTY
    ("orchestrator\nXAC NHAN\n", True),       # tên máy
    ("Gate_UI\nXAC NHAN\n", True),
    ("AUTO-l3\nXAC NHAN\n", True),
    ("Lan\nxac nhan\n", True),                # gõ sai
    ("Lan\n", True),                          # EOF
    ("", True),
])
def test_cli_refusals_write_nothing(monkeypatch, db, pid, ax, fb, capsys, text, tty):
    n = EventStore(db).last_seq(pid)
    rc = _cli(monkeypatch, db, ["sign", "--id", pid, "--gate", "G1"], text, tty=tty, ax=ax, fb=fb)
    assert rc != 0 and "TỪ CHỐI" in capsys.readouterr().err
    assert EventStore(db).last_seq(pid) == n
    rc = _cli(monkeypatch, db, ["reject", "--id", pid, "--gate", "G1", "--reason", "x"], text, tty=tty, ax=ax, fb=fb)
    assert rc != 0 and EventStore(db).last_seq(pid) == n and not fb.exists()


def test_cli_hash_changes_midway_refused(monkeypatch, db, pid, ax, fb, capsys):
    class Sneaky(_Stdin):
        def readline(self):
            line = super().readline()
            if line.strip() == "XAC NHAN":    # dữ liệu đổi trong lúc người đang gõ
                Blackboard.resume(pid, EventStore(db)).append(
                    "risk_register", {"risk": "chen", "severity": "Critical", "status": "open"}, agent="a2_estimation")
            return line
    monkeypatch.setattr(gate.sys, "stdin", Sneaky("Lan\nXAC NHAN\n"))
    assert gate.main(["sign", "--id", pid, "--gate", "G1", "--db", db]) != 0
    assert "đã đổi" in capsys.readouterr().err and _signs(db, pid) == []


def test_cli_wrong_gate_and_no_signer_arg(monkeypatch, db, pid, ax, fb, capsys):
    assert _cli(monkeypatch, db, ["sign", "--id", pid, "--gate", "G2"], "Lan\nXAC NHAN\n", ax=ax, fb=fb) != 0
    for flag in ("--signer", "--by", "--confirmed-by", "--reviewer"):
        with pytest.raises(SystemExit):
            gate.main(["sign", "--id", pid, "--gate", "G1", flag, "Lan", "--db", db])
    assert _signs(db, pid) == []


def test_cli_subprocess_without_tty_refused(db, pid):
    r = subprocess.run([sys.executable, "-m", "src.ops.gate", "sign", "--id", pid, "--gate", "G1", "--db", db],
                       cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, input="Lan\nXAC NHAN\n",
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "TTY" in r.stderr and _signs(db, pid) == []


# ---------- API dashboard ----------
@pytest.fixture
def client(monkeypatch, db, ax, fb):
    tc = pytest.importorskip("fastapi.testclient")
    from src.server import app as srv
    monkeypatch.setattr(srv, "EVENTSTORE", EventStore(db, readonly=True))
    monkeypatch.setattr(srv, "GATE_STORE", EventStore(db))
    monkeypatch.setattr(srv, "GATE_FEEDBACK_LOG", fb)
    monkeypatch.setattr(srv, "AUTONOMY", ax)
    return tc.TestClient(srv.app)


def _post(client, pid, g, d, **body):
    return client.post(f"/api/projects/{pid}/gate/{g}/{d}", json=body)


def test_api_get_and_approve(client, db, pid):
    v = client.get(f"/api/projects/{pid}/gate").json()
    assert v["gate"] == "G1" and v["signer_role"].startswith("Sales Lead")
    r = _post(client, pid, "G1", "approve", signer="Lan Nguyen", typed_confirm="XAC NHAN",
              packet_hash=v["packet_hash"])
    assert r.status_code == 200, r.text
    assert r.json()["view"]["gate"] is None and _signs(db, pid)[0]["by"] == "Lan Nguyen"
    assert _run(db, pid).gate == "G2"
    assert client.get("/api/projects/P-NONE/gate").status_code == 404


@pytest.mark.parametrize("who", ["orchestrator", "Orchestrator", "C1_DEV", "gate_ui", "GATE-UI", "auto", "AUTO-L3"])
def test_api_machine_signer_400(client, db, pid, who):
    for d, extra in (("approve", {}), ("reject", {"reason": "x"})):
        r = _post(client, pid, "G1", d, signer=who, typed_confirm="XAC NHAN", **extra)
        assert r.status_code == 400, (who, r.text)
    assert _signs(db, pid) == [] and _state(db, pid) == "AWAIT_G1"


def test_api_typed_confirm_required(client, db, pid):
    n = EventStore(db).last_seq(pid)
    for body in ({"signer": "Lan"}, {"signer": "Lan", "typed_confirm": "xac nhan"},
                 {"signer": "Lan", "typed_confirm": "XAC NHAN "}, {"typed_confirm": "XAC NHAN"}):
        assert _post(client, pid, "G1", "approve", **body).status_code == 400
    assert client.post(f"/api/projects/{pid}/gate/G1/approve", content=b"not json").status_code == 400
    assert EventStore(db).last_seq(pid) == n


def test_api_wrong_gate_hash_and_reject(client, db, pid, fb):
    assert _post(client, pid, "G2", "approve", signer="Lan", typed_confirm="XAC NHAN").status_code == 409
    assert _post(client, pid, "G1", "approve", signer="Lan", typed_confirm="XAC NHAN",
                 packet_hash="sha256:khac").status_code == 409
    assert _post(client, pid, "G1", "maybe", signer="Lan", typed_confirm="XAC NHAN").status_code == 404
    assert _post(client, pid, "G1", "reject", signer="Lan", typed_confirm="XAC NHAN").status_code == 400  # thiếu lý do
    r = _post(client, pid, "G1", "reject", signer="Lan", typed_confirm="XAC NHAN", reason="giá cao")
    assert r.status_code == 200 and r.json()["backtrack_to"] == "PRESALES" and _state(db, pid) == "PRESALES"
    assert all(json.loads(x)["task_type"] == "presales" for x in fb.read_text(encoding="utf-8").splitlines())


def test_room_project_mode_shows_pending_gate(client, db, pid):
    snap = client.get(f"/api/room?project={pid}").json()
    assert snap["gate"]["id"] == "G1" and "Sales Lead" in snap["gate"]["who"]
    by = {a["id"]: a["status"] for a in snap["agents"]}
    assert by["a2_estimation"] == "waiting_gate"


# ---------- chặn Claude ----------
def test_claude_settings_deny_gate_signing():
    deny = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
    allow = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["allow"]
    assert "Bash(python -m src.ops.gate:*)" in deny and "Bash(python3 -m src.ops.gate:*)" in deny
    assert any("api/projects" in d and "gate" in d for d in deny)
    assert not any("src.ops.gate" in a or "/gate" in a for a in allow)
    for sk in (ROOT / ".claude/skills").glob("*/SKILL.md"):
        txt = sk.read_text(encoding="utf-8")
        if "gate" in txt.lower():
            assert "src.ops.gate" in txt and "Claude" in txt, sk
