"""ISO 42001/42005 như tooling deterministic."""
import tempfile
from pathlib import Path
from src.blackboard.store import Blackboard
from src.iso import inventory, impact, conformance

def test_impact_triage_fintech_high():
    t = impact.triage(["PDPA","MAS TRM"])
    assert t["level"]=="high" and t["full_required"] is True and t["reasons"]

def test_impact_draft_has_areas():
    d = impact.draft({"domain":"fintech","modules":[{"name":"KYC"}]}, ["PDPA"])
    areas = {i["area"] for i in d["impacts"]}
    assert {"privacy","fairness","safety"} <= areas
    assert all(i["needs_human"] for i in d["impacts"])

def test_conformance_gaps_then_ready():
    with tempfile.TemporaryDirectory() as tmp:
        b = Blackboard("p")
        tp = Path(tmp)/"trace.jsonl"
        r1 = conformance.check(b, "p", trace_path=tp, inventory_base=Path(tmp)/"inv", impact_present=False)
        assert not r1["ready"] and r1["gaps"]
        # bổ sung bằng chứng
        tp.write_text("{}\n", encoding="utf-8")
        b.append("risk_register", {"risk":"x","severity":"Low"}, agent="a1_bu")
        b.write("estimate", {"wbs":[{"item":"x","effort_md":1,"source":{"type":"none","refs":[]},"label":"ASSUMPTION"}],
                "effort_md_total":1,"cost_range":{"low":1,"base":1,"high":1},"assumptions":[]}, agent="a2_estimation")
        b.sign("G1", by="sales_lead", via="gate_ui")
        inventory.register("p","ví điện tử",["sonnet"],"PII","SG", base=Path(tmp)/"inv")
        r2 = conformance.check(b, "p", trace_path=tp, inventory_base=Path(tmp)/"inv", impact_present=True)
        assert r2["ready"], r2["gaps"]
