"""A2 ước parametric khi chưa có DB (không hỏi người từng dòng); historical thì FACT."""
import tempfile
from src.store import historical
from src.agents.a2_estimation import enrich
from src.tools.estimation import parametric

def test_parametric_band():
    b = parametric.size_to_md("M", "fintech")
    assert b["base"] == 10.0            # 8 * 1.25
    assert parametric.size_to_md("S", "retail")["base"] == 3.0

def test_enrich_parametric_no_prompt_per_item():
    with tempfile.TemporaryDirectory() as tmp:
        draft = {"wbs": [
            {"id":"W1","item":"KYC onboarding flow","role":"dev","size":"L"},
            {"id":"W2","item":"Thông báo đẩy","role":"dev","size":"S"}],
            "assumptions":[], "exclusions":[], "risks":[], "reflection":{"passed":True,"issues":[]}}
        out = enrich(draft, {"domain":"fintech","blended_rate":650,"tech_stack":[],"hist_base":tmp})
        # không có DB -> parametric, KHÔNG null, KHÔNG hỏi người
        assert all(i["effort_md"] is not None for i in out["wbs"])
        assert all(i["label"]=="ASSUMPTION" for i in out["wbs"])
        assert "parametric" in out["wbs"][0]["basis"]
        assert out["effort_md_total"] > 0

def test_enrich_historical_beats_parametric():
    with tempfile.TemporaryDirectory() as tmp:
        historical.append({"id":"P1","domain":"fintech","estimate_md_total":20,"actual_md_total":25,
            "wbs":[{"item":"KYC onboarding flow","role":"dev","estimate_md":20,"actual_md":25}]}, base=tmp)
        draft = {"wbs":[{"id":"W1","item":"KYC onboarding flow","role":"dev","size":"L"}],
                 "assumptions":[], "exclusions":[], "risks":[], "reflection":{"passed":True,"issues":[]}}
        out = enrich(draft, {"domain":"fintech","blended_rate":650,"hist_base":tmp})
        assert out["wbs"][0]["label"]=="FACT" and out["wbs"][0]["effort_md"]==25.0
