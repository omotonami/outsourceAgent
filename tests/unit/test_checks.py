from src.tools.checks.ac_lint import ac_pass_rate
from src.tools.checks.assumption_coverage import coverage
from src.tools.checks.scope_diff import out_of_scope
from src.gateway.pii_mask import mask
from src.gateway.router import pick_tier


def test_ac_lint():
    good = {"AC": ["Given login When sai mật khẩu 3 lần Then khoá 15 phút"]}
    bad = {"AC": ["Phải nhanh"]}
    assert ac_pass_rate([good, bad]) == 0.5


def test_assumption_coverage():
    est = {"assumptions": ["Client cung cấp API"], "exclusions": ["Pentest bên thứ ba"]}
    prop = {"assumptions_section": "client cung cấp API", "exclusions_section": ""}
    r = coverage(est, prop)
    assert r["ratio"] == 0.5 and r["missing"] == ["Pentest bên thứ ba"]


def test_scope_diff():
    assert out_of_scope(["src/auth/a.py", "src/pay/b.py"], ["src/auth/*"]) == ["src/pay/b.py"]


def test_pii_mask():
    assert "[NRIC]" in mask("NRIC S1234567D") and "[EMAIL]" in mask("a@b.com")


def test_routing():
    assert pick_tier("b4_tech_lead") == "opus"
    assert pick_tier("a2_estimation") == "sonnet"
    assert pick_tier("a2_estimation", signals={"variance_gt_30pct"}) == "opus"
    assert pick_tier("c3_qc", generator_tier="opus") == "opus"
