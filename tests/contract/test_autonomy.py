"""H4 Autonomy Ladder (L0–L2) — offline, state/log ở tmp_path. L3 bảo lưu: không có gì tự ký gate."""
import copy, inspect, json
from pathlib import Path

import pytest
import yaml

from src.blackboard.store import Blackboard
from src.feedback import gate_feedback
from src.learning import revision
from src.orchestrator import autonomy as au, huddle_trigger
from src.orchestrator.autonomy import Autonomy, AutonomyRefused, HARD_MAX
from src.orchestrator.dispatcher import Dispatcher

ROOT = Path(__file__).parents[2]
CFG = yaml.safe_load((ROOT / "config/autonomy.yaml").read_text(encoding="utf-8"))
A, TT = "c1_dev", "build_task"
N1 = CFG["promote"]["consecutive_approvals"]["L1"]
N2 = CFG["promote"]["consecutive_approvals"]["L2"]


def _ax(tmp_path, cfg=None):
    return Autonomy(cfg or CFG, state_path=tmp_path / "state.json", reviews_log=tmp_path / "reviews.jsonl",
                    feedback_log=tmp_path / "gate_feedback.jsonl", regression_log=tmp_path / "regr.jsonl")


def _approve(ax, n, agent=A, tt=TT):
    for _ in range(n):
        ax.record_review(agent, tt, "G3", reviewer="lan")


def _to(ax, level_n, board=None):
    """Đưa cặp lên mức level_n qua đúng luồng: đủ ngưỡng → evaluate → người duyệt."""
    for n in range(1, level_n + 1):
        _approve(ax, CFG["promote"]["consecutive_approvals"][f"L{n}"])
        ax.evaluate(board)
        ax.promote(A, TT, approved_by="lan")
    assert ax.level(A, TT) == f"L{level_n}"


def test_default_L0_and_manual_only(tmp_path):
    ax = _ax(tmp_path)
    assert ax.level("agent_moi", "x") == "L0"
    assert ax.can_run("agent_moi", "x", "manual")["allowed"]
    for trig in ("routine", "schedule", "event"):
        assert not ax.can_run("agent_moi", "x", trig)["allowed"]
    assert ax.evaluate() == {"proposals": [], "demotions": []}


def test_threshold_proposes_but_no_effect_until_approved(tmp_path):
    ax = _ax(tmp_path)
    _approve(ax, N1 - 1)
    assert ax.evaluate()["proposals"] == []
    _approve(ax, 1)
    props = ax.evaluate()["proposals"]
    assert props and props[0]["to"] == "L1" and props[0]["streak"] == N1
    assert ax.level(A, TT) == "L0"                       # chỉ là đề xuất
    with pytest.raises(AutonomyRefused):
        ax.promote(A, TT, approved_by="")                # không có người
    with pytest.raises(AutonomyRefused):
        ax.promote(A, TT, approved_by="auto-L3")         # bot không được duyệt
    with pytest.raises(AutonomyRefused):
        ax.promote(A, TT, approved_by=A)                 # agent tự duyệt mình
    assert ax.level(A, TT) == "L0"
    ax.promote(A, TT, approved_by="lan")
    assert ax.level(A, TT) == "L1" and ax.pending(A, TT) is None
    # L1: người kích hoạt vẫn chạy, routine thì chưa; duyệt mọi output
    assert ax.can_run(A, TT, "manual")["review"] == "every_output"
    assert not ax.can_run(A, TT, "routine")["allowed"]


def test_promote_without_proposal_refused_and_streak_resets_on_edit(tmp_path):
    ax = _ax(tmp_path, {**copy.deepcopy(CFG), "demote": {**CFG["demote"], "on_gate_edit": 0}})
    with pytest.raises(AutonomyRefused):
        ax.promote(A, TT, approved_by="lan")
    _approve(ax, N1 - 1)
    ax.record_review(A, TT, "G3", reviewer="lan", edited=True)  # sửa → chuỗi về 0
    _approve(ax, N1 - 1)
    assert ax.evaluate()["proposals"] == []


def test_L2_routine_runs_auto(tmp_path):
    ax = _ax(tmp_path)
    _to(ax, 2)
    d = ax.can_run(A, TT, "routine")
    assert d["allowed"] and d["review"] == "gate"
    assert ax.evaluate()["proposals"] == []              # không có mức trên L2


def test_one_revision_demotes_immediately_with_risk(tmp_path):
    ax = _ax(tmp_path)
    b = Blackboard("p-h4")
    _to(ax, 2, b)
    revision.record(b, agent=A, role="dev", before="x", after="y")
    res = ax.evaluate(b)
    assert res["demotions"] and res["demotions"][0]["to"] == "L1"
    assert ax.level(A, TT) == "L1"
    assert any(r.get("phase") == "autonomy" for r in b.get("risk_register"))
    assert ax.evaluate(b)["demotions"] == []             # cùng revision không hạ 2 lần
    # 'preference' là nhiễu → không hạ
    revision.record(b, agent=A, role="dev", before="a", after="b", kind="preference")
    assert ax.evaluate(b)["demotions"] == [] and ax.level(A, TT) == "L1"


def test_gate_feedback_regression_incident_demote(tmp_path):
    ax = _ax(tmp_path)
    b = Blackboard("p-h4")
    _to(ax, 2)
    gate_feedback.record_edit("G3", A, "dev", "f", "a", "b", "lan", "u1", base_log=tmp_path / "gate_feedback.jsonl")
    assert ax.evaluate(b)["demotions"][0]["to"] == "L1"
    ax.evaluate(b, eval_results=[{"agent": A, "task_type": TT, "regression": True}])
    assert ax.level(A, TT) == "L0"
    _to(ax, 2)
    d = ax.report_incident(A, TT, "xoá nhầm file", board=b)
    assert d["to"] == CFG["demote"]["on_incident"] and ax.level(A, TT) == "L0"
    assert len([r for r in b.get("risk_register") if r.get("phase") == "autonomy"]) == 3


def test_max_level_hard_capped_at_L2(tmp_path):
    bad = copy.deepcopy(CFG)
    bad["max_level"] = "L3"
    bad["levels"]["L3"] = {"allowed_triggers": ["manual", "routine"], "review": "none"}
    bad["promote"]["consecutive_approvals"]["L3"] = 1
    ax = _ax(tmp_path, bad)
    assert ax.max_level == HARD_MAX == 2
    _to(ax, 2)
    _approve(ax, 50)
    assert ax.evaluate()["proposals"] == []
    with pytest.raises(AutonomyRefused):
        ax.promote(A, TT, approved_by="lan", to="L3")
    assert ax.level(A, TT) == "L2"
    # state bị sửa tay lên L3 vẫn bị kẹp
    st = json.loads((tmp_path / "state.json").read_text())
    st["pairs"][Autonomy.key(A, TT)]["level"] = "L3"
    (tmp_path / "state.json").write_text(json.dumps(st))
    assert ax.level(A, TT) == "L2" and ax.can_run(A, TT, "routine")["review"] == "gate"


def test_config_max_level_respected_and_pinned(tmp_path):
    ax = _ax(tmp_path, {**copy.deepcopy(CFG), "max_level": "L1"})
    _to(ax, 1)
    _approve(ax, N2)
    assert ax.evaluate()["proposals"] == []
    _approve(ax, N2, agent="e1_pm")                       # e1_pm ghim L0
    assert all(p["agent"] != "e1_pm" for p in ax.evaluate()["proposals"])
    assert ax.level("e1_pm", TT) == "L0"


def test_no_gate_signing_anywhere(tmp_path):
    b = Blackboard("p-h4")
    before = copy.deepcopy(b._signatures)
    ax = _ax(tmp_path)
    _to(ax, 2, b)
    revision.record(b, agent=A, role="dev", before="x", after="y")
    ax.evaluate(b)
    ax.report_incident(A, TT, "x", board=b)
    assert b._signatures == before == {}
    src = inspect.getsource(au)
    for bad in (".sign(", "revoke_signature", "gate_signatures", "is_signed"):
        assert bad not in src
    assert not any("sign" in n.lower() for n, _ in inspect.getmembers(Autonomy, inspect.isfunction))


def _backlog_board():
    b = Blackboard("p-h4")
    b.write("task_backlog", [{"id": "T1", "story_ref": "S1", "spec": "x", "AC": ["a"], "deps": [],
                              "size": "S", "risk_level": "normal"}], agent="b4_tech_lead")
    return b


def test_dispatcher_blocks_auto_spawn_at_L0(tmp_path):
    ax = _ax(tmp_path)
    calls = []
    worker = lambda c, h: calls.append(c["task_id"]) or {"ok": True}
    b = _backlog_board()
    res = Dispatcher(b, worker, trigger="routine", autonomy=ax).run()
    assert calls == [] and res["spawned"] == 0 and "T1" in res["autonomy_denied"]
    assert b.get("task_backlog")[0]["status"] == "ready"
    ev = [e for e in b.get("trace_log") if e["event"] == "BLOCKED"]
    assert len(ev) == 1 and ev[0]["reason"] == "autonomy" and ev[0]["task_id"] == "T1"
    # người kích hoạt → chạy được ở L0
    res = Dispatcher(b, worker, trigger="manual", autonomy=ax).run()
    assert calls == ["T1"] and res["done"] == ["T1"]


def test_dispatcher_auto_spawn_allowed_at_L2(tmp_path):
    ax = _ax(tmp_path)
    _to(ax, 2)
    b = _backlog_board()
    res = Dispatcher(b, lambda c, h: {"ok": True}, trigger="routine", autonomy=ax).run()
    assert res["done"] == ["T1"] and res["autonomy_denied"] == {}
    assert b.is_signed("G3") is False                     # await_merge, không ai ký G3


def test_huddle_trigger_respects_autonomy(tmp_path):
    ax = _ax(tmp_path)
    b = Blackboard("p-h4")
    b.write("business_context", {"domain": "x", "modules": [], "ambiguities": ["a", "b", "c"]}, agent="a1_bu")
    assert huddle_trigger.check(b, "PRESALES") is not None              # không truyền autonomy: như cũ
    assert huddle_trigger.check(b, "PRESALES", autonomy=ax) is None     # L0 + routine → chặn
    assert huddle_trigger.check(b, "PRESALES", autonomy=ax, trigger="manual") is not None


# ---------- Quyết định 2026-11-03 (e): gate_ui/server ký gate → record_review (chỉ log) ----------
def test_server_gate_approve_records_review_without_signing_for_agent(tmp_path, monkeypatch):
    app = pytest.importorskip("src.server.app")
    ax = _ax(tmp_path)
    monkeypatch.setattr(app, "AUTONOMY", ax)
    app.S.reset(True)
    g3 = next(i for i, s in enumerate(app.S.steps) if s.get("gate") == "G3")
    app.S.i = g3
    app.gate("approve")
    reviews = [json.loads(l) for l in (tmp_path / "reviews.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["agent"] for r in reviews} == {"c1_dev", "c2_tester", "c3_qc"}
    assert all(r["gate"] == "G3" and r["task_type"] == "build_task" and r["edited"] is False
               and r["approved"] is True for r in reviews)
    sigs = app.S.board._signatures
    assert list(sigs) == ["G3"] and sigs["G3"]["by"] == "human"   # chữ ký là của người, qua gate_ui
    assert not (tmp_path / "state.json").exists() or ax.level("c1_dev", "build_task") == "L0"

    # G1: nhiều agent; edited=True → không ghi review
    app.S.reset(True)
    app.S.i = next(i for i, s in enumerate(app.S.steps) if s.get("gate") == "G1")
    n = len(reviews)
    app.gate("approve", edited=True)
    assert len((tmp_path / "reviews.jsonl").read_text(encoding="utf-8").splitlines()) == n
    app.S.reset(True)
    app.S.i = next(i for i, s in enumerate(app.S.steps) if s.get("gate") == "G1")
    app.gate("approve", reviewer="lan")
    new = [json.loads(l) for l in (tmp_path / "reviews.jsonl").read_text(encoding="utf-8").splitlines()][n:]
    assert {r["agent"] for r in new} == {"a1_bu", "a2_estimation", "a3_proposal"}
    assert {r["task_type"] for r in new} == {"presales"} and {r["reviewer"] for r in new} == {"lan"}
    app.S.reset(True)


def test_server_gate_reject_records_nothing(tmp_path, monkeypatch):
    app = pytest.importorskip("src.server.app")
    ax = _ax(tmp_path)
    monkeypatch.setattr(app, "AUTONOMY", ax)
    app.S.reset(True)
    app.S.i = next(i for i, s in enumerate(app.S.steps) if s.get("gate") == "G3")
    app.gate("reject")
    assert not (tmp_path / "reviews.jsonl").exists() and app.S.board._signatures == {}
    app.S.reset(True)


# ---------- Quyết định 2026-11-03 (f): gate_feedback có task_type → hạ đúng cặp ----------
def test_gate_feedback_task_type_demotes_only_that_pair(tmp_path):
    ax = _ax(tmp_path)
    other = "hotfix"
    for tt in (TT, other):                                       # đưa cả 2 cặp lên L1
        _approve(ax, N1, tt=tt)
        ax.evaluate()
        ax.promote(A, tt, approved_by="lan")
    gate_feedback.record_edit("G3", A, "dev", "f", "a", "b", "lan", "u1",
                              base_log=tmp_path / "gate_feedback.jsonl", task_type=other)
    entry = json.loads((tmp_path / "gate_feedback.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert entry["task_type"] == other
    res = ax.evaluate()
    assert [(d["task_type"], d["to"]) for d in res["demotions"]] == [(other, "L0")]
    assert ax.level(A, TT) == "L1" and ax.level(A, other) == "L0"


def test_gate_feedback_without_task_type_stays_conservative(tmp_path):
    ax = _ax(tmp_path)
    for tt in (TT, "hotfix"):
        _approve(ax, N1, tt=tt)
        ax.evaluate()
        ax.promote(A, tt, approved_by="lan")
    gate_feedback.record_edit("G3", A, "dev", "f", "a", "b", "lan", "u1",
                              base_log=tmp_path / "gate_feedback.jsonl")
    assert "task_type" not in json.loads((tmp_path / "gate_feedback.jsonl").read_text(encoding="utf-8"))
    ax.evaluate()
    assert ax.level(A, TT) == "L0" and ax.level(A, "hotfix") == "L0"
