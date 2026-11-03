"""Hai vòng training chạy đúng với Blackboard + historical DB."""
import json, tempfile
from pathlib import Path

from src.store import historical
from src.agents.a2_estimation import enrich
from src.reconcile.close_project import close
from src.feedback.gate_feedback import record_edit, accept_suggestion
from src.blackboard.store import Blackboard


def _seed(tmp, pid="P-KYC", actual=14):
    historical.append({
        "id": pid, "domain": "fintech", "tech_stack": [],
        "estimate_md_total": 12, "actual_md_total": actual,
        "wbs": [{"item": "KYC onboarding flow", "role": "dev",
                 "estimate_md": 12, "actual_md": actual}]}, base=tmp)


def test_store_append_lookup_holdout():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(tmp, "P1", 14); _seed(tmp, "P2", 18)
        hit = historical.lookup("KYC onboarding flow", "fintech", base=tmp)
        assert hit["n"] == 2 and hit["effort_md"] == 16.0          # median(14,18)
        # holdout: loại P1, P2 -> không còn match
        out = historical.lookup("KYC onboarding flow", "fintech",
                                exclude_ids={"P1", "P2"}, base=tmp)
        assert out["effort_md"] is None


def test_label_transition_assumption_to_fact():
    """Trước khi đóng dự án: ASSUMPTION. Sau khi đóng 1 dự án tương tự: FACT."""
    with tempfile.TemporaryDirectory() as tmp:
        draft = {"wbs": [{"id": "W1", "item": "KYC onboarding flow",
                          "module": "KYC", "role": "dev", "size": "L"}],
                 "assumptions": [], "exclusions": [], "risks": [],
                 "reflection": {"passed": True, "issues": []}}
        ctx = {"domain": "fintech", "blended_rate": 650, "tech_stack": [], "hist_base": tmp}

        before = enrich(draft, ctx)
        assert before["wbs"][0]["label"] == "ASSUMPTION"        # chưa có DB -> parametric
        assert before["wbs"][0]["effort_md"] is not None        # có số từ size, KHÔNG hỏi người
        assert "parametric" in before["wbs"][0]["basis"]
        assert before["needs_human"] is True

        # VÒNG 2: đóng một dự án tương tự
        rep = close("P-KYC", "fintech", [],
                    estimate={"wbs": [{"item": "KYC onboarding flow", "role": "dev", "effort_md": 12}],
                              "effort_md_total": 12},
                    actuals={"items": {"KYC onboarding flow": 14}, "total_md": 14},
                    hist_base=tmp, metric_log=Path(tmp)/"m.jsonl")
        assert rep["added_to_db"] and rep["within_band"] is True   # lệch ~14% < 20%

        after = enrich(draft, ctx)
        assert after["wbs"][0]["label"] == "FACT"
        assert after["wbs"][0]["effort_md"] == 14.0
        assert after["wbs"][0]["source"]["refs"] == ["P-KYC"]
        assert after["needs_human"] is False


def test_close_large_variance_raises_risk():
    with tempfile.TemporaryDirectory() as tmp:
        b = Blackboard("proj")
        rep = close("P-BAD", "retail", [],
                    estimate={"wbs": [{"item": "x", "role": "dev", "effort_md": 10}], "effort_md_total": 10},
                    actuals={"items": {"x": 20}, "total_md": 20},
                    board=b, hist_base=tmp, metric_log=Path(tmp)/"m.jsonl")
        assert rep["within_band"] is False
        rr = b.get("risk_register")
        assert rr and rr[0]["source_agent"] == "reconcile"


def test_feedback_loop_suggest_then_accept():
    """VÒNG 1: sửa ở gate -> gợi ý -> người xác nhận -> ghi personal, KHÔNG đụng _core."""
    with tempfile.TemporaryDirectory() as tmp:
        b = Blackboard("proj")
        r = record_edit("G1", "a2_estimation", "estimation", "cost_range",
                        "S$100k", "S$120k", reviewer="sales_lead", user_id="u1",
                        board=b, base_log=Path(tmp)/"fb.jsonl")
        assert r["logged"] and "cost_range" in r["suggestion"]
        # log có 1 dòng
        assert (Path(tmp)/"fb.jsonl").read_text().count("\n") == 1
        # Blackboard ghi nhận (append-only, source_agent = gate_ui)
        assert b.get("risk_register")[0]["source_agent"] == "gate_ui"

        kn = Path(tmp)/"knowledge"
        path = accept_suggestion("estimation", "u1", r["suggestion"], kn_base=kn)
        body = Path(path).read_text(encoding="utf-8")
        assert "layer: personal" in body and "S$120k" in body
        assert "u1" in path and "personal" in path            # không phải _core/specs
