"""Lớp skill học được qua vận hành: revision_log → reflection trong phiên → distiller → proposed."""
import tempfile
from pathlib import Path
from src.blackboard.store import Blackboard
from src.learning import revision, distiller

def test_revision_log_and_reflection_context():
    b = Blackboard("p")
    revision.record(b, "a2_estimation", "estimation", "login 3 MD", "login 5 MD (thêm MFA)", "client cần MFA")
    revision.record(b, "a2_estimation", "estimation", "audit 0", "audit 4 MD", kind="preference")  # nhiễu
    # history chỉ lấy 'edit', bỏ 'preference'
    h = revision.history(b, agent="a2_estimation")
    assert len(h) == 1 and h[0]["after"].startswith("login 5")
    ctx = revision.reflection_context(b, "a2_estimation")
    assert "login 5 MD" in ctx and "MFA" in ctx

def test_distiller_needs_repeat_then_proposed_not_auto_used():
    # 3 lần cùng một kiểu sửa -> 1 proposal; 1 lần thì không
    entries = [{"role":"estimation","kind":"edit","before":"x","after":"thêm audit log cho MAS"} for _ in range(3)]
    entries += [{"role":"ba","kind":"edit","before":"y","after":"một lần duy nhất"}]
    props = distiller.distill(entries, min_count=3)
    assert len(props) == 1 and props[0]["role"]=="estimation" and props[0]["status"]=="proposed"

    with tempfile.TemporaryDirectory() as tmp:
        path = distiller.write_proposed(props[0], kn_base=Path(tmp))
        body = Path(path).read_text(encoding="utf-8")
        assert "learned" in path and "PROPOSED.md" in path     # vào lớp learned, file PROPOSED
        assert "status: proposed" in body                       # CHƯA dùng
        # KHÔNG phải SKILL.md -> skill_loader sẽ không nạp cho tới khi người promote
        assert not (Path(tmp)/"estimation"/"learned"/"SKILL.md").exists()
