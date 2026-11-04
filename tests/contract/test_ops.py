"""src/ops — bộ vận hành hằng ngày (Chief). Offline, sender mock, store SQLite tạm."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.blackboard.eventstore import EventStore
from src.blackboard.store import Blackboard
from src.gateway import budget, trace
from src.orchestrator import autonomy
from src.ops import common, doctor, mock, pipeline, project, prune, report

ROOT = Path(__file__).parents[2]
SECRETS = ["Acme Secret Bank", "CoreBankX", "giảm thời gian onboarding", "Ví điện tử nội bộ"]
BRIEF = """client: Acme Secret Bank
source: email
received_at: 2026-11-01
domain: fintech
summary: Ví điện tử nội bộ cho Acme Secret Bank, tích hợp CoreBankX
goals: [giảm thời gian onboarding]
"""


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(autonomy, "STATE", tmp_path / "autonomy_state.json")
    monkeypatch.delenv("OA_EVENTSTORE_PATH", raising=False)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ev.sqlite3")


@pytest.fixture
def brief(tmp_path):
    p = tmp_path / "brief.yaml"
    p.write_text(BRIEF, encoding="utf-8")
    return str(p)


def _new(db, brief, pid="P-T1"):
    assert project.main(["new", "--brief", brief, "--id", pid, "--db", db]) == 0
    return pid


def _run(db, pid):
    return project.main(["run", "--id", pid, "--mock", "--db", db])


def _state(db, pid):
    return common.state_of(Blackboard.load(pid, EventStore(db)))


def _sign(db, pid, gate):
    """Mô phỏng NGƯỜI ký qua gate_ui (đường ký duy nhất của hệ — không nằm trong src/ops)."""
    Blackboard.resume(pid, EventStore(db)).sign(gate, by="human", via="gate_ui")


# ---------- new / run ----------
def test_new_validates_brief(tmp_path, db):
    bad = tmp_path / "bad.yaml"
    bad.write_text("client: X\n", encoding="utf-8")
    assert project.main(["new", "--brief", str(bad), "--db", db]) == 1
    assert EventStore(db).projects() == []


def test_new_md_brief_front_matter(tmp_path, db):
    p = tmp_path / "b.md"
    p.write_text("---\nclient: C\nsource: call\nreceived_at: 2026-11-02\n---\nMô tả dài\n", encoding="utf-8")
    assert project.load_brief(p)["description"] == "Mô tả dài"
    assert project.main(["new", "--brief", str(p), "--id", "P-MD", "--db", db]) == 0
    assert _state(db, "P-MD") == "INTAKE"


def test_new_refuses_duplicate_id(db, brief):
    _new(db, brief)
    assert project.main(["new", "--brief", brief, "--id", "P-T1", "--db", db]) == 1


def test_run_stops_at_await_g1(db, brief, capsys):
    pid = _new(db, brief)
    assert _run(db, pid) == 0
    out = capsys.readouterr().out
    assert _state(db, pid) == "AWAIT_G1"
    assert "G1" in out and "CẦN NGƯỜI" in out and "KHÔNG ký thay" in out
    b = Blackboard.load(pid, EventStore(db))
    assert not b.is_signed("G1")
    assert all(b.get(z) for z in ("business_context", "estimate", "proposal"))
    # chạy lại không gọi lại agent, vẫn chờ G1
    n = EventStore(db).last_seq(pid)
    assert _run(db, pid) == 0
    assert _state(db, pid) == "AWAIT_G1" and EventStore(db).last_seq(pid) == n


def test_resume_in_new_process_after_human_signs(db, brief):
    pid = _new(db, brief)
    _run(db, pid)
    _sign(db, pid, "G1")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-m", "src.ops.project", "run", "--id", pid, "--mock", "--db", db],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "AWAIT_G2" in r.stdout
    assert _state(db, pid) == "AWAIT_G2"
    assert Blackboard.load(pid, EventStore(db)).get("task_backlog")


def test_full_flow_to_released_only_with_human_signatures(db, brief):
    pid = _new(db, brief)
    for gate, nxt in (("G1", "AWAIT_G2"), ("G2", "AWAIT_G3"), ("G3", "AWAIT_G4"), ("G4", "RELEASED")):
        _run(db, pid)
        assert _state(db, pid) == f"AWAIT_{gate}"
        _sign(db, pid, gate)
    _run(db, pid)
    assert _state(db, pid) == "RELEASED"
    b = Blackboard.load(pid, EventStore(db))
    assert {t["status"] for t in b.get("task_backlog")} == {"await_merge"}   # Dispatcher không ghi merged
    # scanner stub → risk High, không giả vờ đã quét
    assert any("CHƯA quét thật" in r["risk"] for r in b.get("risk_register"))
    signs = [r for r in EventStore(db).read(pid) if r.kind == "sign"]
    assert len(signs) == 4 and all(r.payload["via"] == "gate_ui" for r in signs)


def test_no_gate_signing_path_in_ops():
    """Đường ký DUY NHẤT trong src/ops là src/ops/gate.py (người: dashboard / terminal TTY) — test_ops_gate.py."""
    for f in (ROOT / "src/ops").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "gate_signatures" not in src, f
        if f.name != "gate.py":
            assert ".sign(" not in src, f
    g = (ROOT / "src/ops/gate.py").read_text(encoding="utf-8")
    assert g.count(".sign(") - g.count("Blackboard.sign(") == 1 and "b.sign(gate, by=who, via=GATE_UI)" in g
    with pytest.raises(SystemExit):
        project.main(["sign", "--id", "x"])


def test_run_does_not_sign_even_if_gate_pending(db, brief):
    pid = _new(db, brief)
    for _ in range(3):
        _run(db, pid)
    assert [r for r in EventStore(db).read(pid) if r.kind == "sign"] == []


def test_escalated_on_schema_fail(db, brief, capsys):
    pid = _new(db, brief)
    bad = lambda m, s, msg, mt: ("không phải json", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    o = pipeline.run(EventStore(db), pid, sender=bad)
    assert o.stop == "ESCALATED" and _state(db, pid) == "ESCALATED"
    o2 = pipeline.run(EventStore(db), pid, sender=mock.sender)        # không tự gỡ ESCALATED
    assert o2.stop == "ESCALATED" and o2.ran == []


def test_blocked_on_guard_refusal(db, brief, monkeypatch):
    pid = _new(db, brief)
    _run(db, pid)
    _sign(db, pid, "G1")
    orig = mock._out

    def no_gwt(agent, inp):
        out = orig(agent, inp)
        if agent == "b1_ba":
            for s in out["stories"]:
                s["AC"] = ["người dùng thấy màn hình"]
        return out
    monkeypatch.setattr(mock, "_out", no_gwt)
    o = pipeline.run(EventStore(db), pid, sender=mock.sender)
    assert o.stop == "BLOCKED" and "discovery_ready" in o.reason
    assert _state(db, pid) == "DISCOVERY"
    ev = [r.payload for r in EventStore(db).read(pid) if r.zone == "trace_log"]
    assert ev[-1]["event"] == "BLOCKED"


def test_budget_from_eventstore_escalates(db, brief, monkeypatch):
    pid = _new(db, brief)
    _run(db, pid)                                   # đã dùng ~5400 token presales
    _sign(db, pid, "G1")
    monkeypatch.setitem(budget._CFG, "per_project_tokens", {"presales": 100, "discovery": 1000,
                                                            "build": None, "security": None})
    budget.reset(pid)
    o = pipeline.run(EventStore(db), pid, sender=mock.sender)
    assert o.stop == "ESCALATED" and "budget" in o.reason
    assert common.seed_ledger(EventStore(db), pid)["presales"] > 100


def test_huddle_manual_only_and_no_state_change(db, brief, capsys):
    pid = _new(db, brief)
    _run(db, pid)
    st = _state(db, pid)
    assert not Blackboard.load(pid, EventStore(db)).get("decision_records")   # run không tự huddle
    assert project.main(["huddle", "--id", pid, "--phase", "presales", "--mock", "--db", db]) == 0
    b = Blackboard.load(pid, EventStore(db))
    assert len(b.get("decision_records")) == 1 and _state(db, pid) == st
    assert project.main(["huddle", "--id", pid, "--phase", "presales", "--mock", "--db", db]) == 0
    assert "Không có điều kiện" in capsys.readouterr().out


def test_list_and_status(db, brief, capsys):
    pid = _new(db, brief)
    _run(db, pid)
    capsys.readouterr()
    assert project.main(["list", "--db", db]) == 0
    assert pid in capsys.readouterr().out
    assert project.main(["status", "--id", pid, "--db", db]) == 0
    assert json.loads(capsys.readouterr().out.split("\n→")[0])["gate_pending"] == "G1"


def test_blackboard_resume_writes_through(db, brief):
    pid = _new(db, brief)
    b = Blackboard.resume(pid, EventStore(db))
    assert b.get("lead_brief")["client"] == "Acme Secret Bank"
    b.append("risk_register", {"risk": "x", "severity": "Low", "status": "open"}, agent="a1_bu")
    assert Blackboard.load(pid, EventStore(db)).get("risk_register")[-1]["risk"] == "x"


# ---------- report ----------
SECTIONS = ["### Task theo trạng thái", "### Risk mở High/Critical", "### BLOCKED / escalate",
            "### Token & chi phí ước tính", "### Hoạt động 24h qua", "### Cần người quyết",
            "## Việc cần người quyết", "| Project | State |"]


def _built(db, brief, pid="P-T1"):
    _new(db, brief, pid)
    for g in ("G1", "G2"):
        _run(db, pid)
        _sign(db, pid, g)
    _run(db, pid)
    return pid


def test_report_daily_sections_and_deterministic(db, brief):
    pid = _built(db, brief)
    st = EventStore(db, readonly=True)
    now = time.time()
    a = report.build(st, [pid], now=now)
    assert a == report.build(st, [pid], now=now)
    for s in SECTIONS:
        assert s in a, s
    assert "AWAIT_G3" in a and "Acme Secret Bank" in a and "await_merge" in a
    assert "USD" in a and "Ký G3" in a


def test_report_redact_leaks_nothing(db, brief, tmp_path):
    pid = _built(db, brief, pid="P-acme-secret-bank-01")
    out = tmp_path / "r.md"
    assert report.main(["daily", "--all", "--redact", "--db", db, "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    b = Blackboard.load(pid, EventStore(db))
    leaks = SECRETS + ["src/", "Onboarding", "Nhà cung cấp cổng thanh toán", "SLA uptime", "pid", pid,
                       "story 1", "CHƯA quét thật"]
    leaks += [r["risk"] for r in b.get("risk_register")]
    low = text.lower()
    for s in leaks:
        assert s.lower() not in low, s
    for s in SECTIONS:
        assert s in text
    assert "P-anon-" in text and "AWAIT_G3" in text and "BẢN REDACT" in text


def test_report_redact_keeps_safe_id(db, brief):
    pid = _new(db, brief, "P-20261104-0900")
    _run(db, pid)
    assert pid in report.build(EventStore(db, readonly=True), [pid], redact=True)


# ---------- prune ----------
def test_prune_drafts_keeps_latest_and_resume_state(db, brief):
    pid = _built(db, brief)
    st = EventStore(db)
    before = Blackboard.load(pid, st)
    rows = st.read(pid)
    n_dry = prune.prune_drafts(st, days=0, now=time.time() + 10, dry_run=True)
    assert n_dry > 0 and len(st.read(pid)) == len(rows)
    n = prune.prune_drafts(st, days=0, now=time.time() + 10)
    assert n == n_dry and len(st.read(pid)) == len(rows) - n
    after = Blackboard.load(pid, st)
    for z in ("lead_brief", "task_backlog", "project_status", "proposal", "build_results"):
        assert after.get(z) == before.get(z)
    assert common.state_of(after) == "AWAIT_G3"
    assert prune.prune_drafts(st, days=30) == 0          # mới tạo → không gì quá hạn


def test_prune_closed_project_only_when_closed(db, brief):
    pid = _new(db, brief)
    _run(db, pid)
    with pytest.raises(PermissionError):
        prune.prune_closed(EventStore(db), pid)
    assert prune.main(["--closed-project", pid, "--db", db]) == 1
    pipeline.run(EventStore(db), _new(db, brief, "P-X"),
                 sender=lambda m, s, msg, mt: ("x", {"total_tokens": 0}))     # → ESCALATED
    assert _state(db, "P-X") == "ESCALATED"
    assert prune.prune_closed(EventStore(db), "P-X") > 0
    assert EventStore(db).last_seq("P-X") == 0 and EventStore(db).last_seq(pid) > 0


# ---------- doctor ----------
def test_doctor_runs(db, capsys):
    res = doctor.checks(db)
    names = {n for _, n, _ in res}
    assert {"Python ≥ 3.10", "ANTHROPIC_API_KEY", "budgets per_project_tokens", "config/pricing.yaml",
            "event store ghi được", "Phòng 3D"} <= names
    assert doctor.main(["--db", db]) == 0
    out = capsys.readouterr().out
    assert "sk-" not in out


def test_doctor_warns_null_budget(db, monkeypatch):
    from src.common import config
    real = config.load_yaml

    def fake(rel, root=None):
        d = real(rel, root)
        if str(rel) == config.BUDGETS:
            d["per_project_tokens"] = {"presales": None, "build": 1000}
        return d
    monkeypatch.setattr(config, "load_yaml", fake)
    res = {n: (s, d) for s, n, d in doctor.checks(db)}
    assert res["budgets per_project_tokens"][0] == doctor.WARN and "presales" in res["budgets per_project_tokens"][1]
