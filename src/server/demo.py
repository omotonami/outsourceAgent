"""Kịch bản demo cho dashboard (1 dự án NeoPay): các bước, ai ghi zone nào, payload mẫu.

Nối vào router thật để lấy tier + lý do. Không gọi model (token là số minh hoạ trong kịch bản).
"""
from src.agents.a2_estimation import enrich
from src.common import config
from src.gateway import router
from src.orchestrator import usage

# zone → agent-writer (dùng cho ghi demo và để biết output của agent nào được duyệt ở gate)
ZONE_WRITER = {"lead_brief": "intake", "business_context": "a1_bu", "estimate": "a2_estimation",
               "proposal": "a3_proposal", "requirements": "b1_ba", "design_spec": "b2_ux", "ui_system": "b3_ui",
               "architecture": "b4_tech_lead", "task_backlog": "b4_tech_lead",
               "build_results": "c1_dev", "security_report": "d1_soc"}
# bước demo mà writer zone không nói đúng agent (C2/C3 cùng ghi build_results)
STEP_AGENT = {"c2": "c2_tester", "c3f": "c3_qc", "c3p": "c3_qc", "c1r": "c1_dev"}
AGENT_IDS = config.agent_ids()

LEAD_BRIEF = {"client": "NeoPay", "source": "email", "received_at": "2026-10-25"}
REQUIREMENTS = {"stories": [{"as_a": "user", "i_want": "KYC", "so_that": "dùng ví", "AC": ["Given..When..Then.."]}],
                "open_questions": [{"q": "FX?", "owner": "PM", "due": "2026-11-01"}]}
TASK_BACKLOG = [{"id": "T1", "story_ref": "KYC", "spec": "...", "AC": ["..."], "risk_level": "high"}]
SECURITY_REPORT = {"findings": [], "gate": "pass"}


def plan(dev_high):
    """Danh sách bước demo (agent / gate) theo phase A→D."""
    risk = "high" if dev_high else "normal"
    dev = router.pick_tier("c1_dev", risk_level=risk)
    qc = router.pick_tier("c3_qc", risk_level=risk, generator_tier=dev)

    def t(agent, **kw):
        return router.pick_tier(agent, **kw)
    return [
        dict(k="intake", phase="A", agent="intake", tier="haiku", why="chuẩn hoá RFP",
             reads=[], writes=["lead_brief"], tok=[300, 120]),
        dict(k="a1", phase="A", agent="A1 BU", tier=t("a1_bu"), why="brief rõ, không mâu thuẫn",
             reads=["lead_brief"], writes=["business_context"], tok=[1800, 900]),
        dict(k="a2", phase="A", agent="A2 Estimation", tier=t("a2_estimation"), why="việc chính",
             reads=["business_context"], writes=["estimate"], tok=[2200, 1400], special="estimate"),
        dict(k="a3", phase="A", agent="A3 Proposal", tier=t("a3_proposal", step="final_review"),
             why="bản cuối luôn Opus", reads=["business_context", "estimate"], writes=["proposal"], tok=[2600, 1700]),
        dict(k="g1", phase="A", gate="G1", who="Sales Lead",
             surface=["hạng mục ASSUMPTION", "exclusions", "khoảng chi phí"]),
        dict(k="b1", phase="B", agent="B1 BA", tier=t("b1_ba"), why="story thường",
             reads=["proposal", "business_context"], writes=["requirements"], tok=[2000, 1500]),
        dict(k="b2", phase="B", agent="B2 Designer", tier=t("b2_ux"), why="mặc định Sonnet",
             reads=["requirements"], writes=["design_spec"], tok=[1500, 1100]),
        dict(k="b2b", phase="B", agent="B3 UI", tier=t("b3_ui"), why="design system",
             reads=["design_spec"], writes=["ui_system"], tok=[2200, 1800]),
        dict(k="b3", phase="B", agent="B4 Tech Lead", tier=t("b4_tech_lead"), why="kiến trúc luôn Opus",
             reads=["requirements", "design_spec"], writes=["architecture", "task_backlog"], tok=[2800, 2000]),
        dict(k="g2", phase="B", gate="G2", who="Client + PM", surface=["scope_delta", "open_questions"]),
        dict(k="c1", phase="C", agent="C1 Dev", tier=dev, why=("task high-risk" if dev_high else "task thường"),
             reads=["task_backlog"], writes=["build_results"], tok=([3000, 2400] if dev_high else [2200, 1600])),
        dict(k="c2", phase="C", agent="C2 Tester", tier=t("c2_tester", step="generate"),
             why="sinh test Sonnet + tóm tắt Haiku", reads=["build_results"], writes=["build_results"],
             tok=[1400, 1000]),
        dict(k="c3f", phase="C", agent="C3 QC", tier=qc, why="reviewer ≥ tier Dev",
             reads=["build_results"], writes=[], tok=[1200, 700], verdict="FAIL", loop="1 Critical → Dev"),
        dict(k="c1r", phase="C", agent="C1 Dev (retry)", tier=t("c1_dev", signals=["retry_2"]),
             why="retry lần 2 → escalate", reads=["task_backlog"], writes=["build_results"], tok=[3000, 2400]),
        dict(k="c3p", phase="C", agent="C3 QC", tier="opus", why="reviewer ≥ tier Dev",
             reads=["build_results"], writes=["build_results"], tok=[1200, 700], verdict="PASS"),
        dict(k="g3", phase="C", gate="G3", who="Tech Lead", surface=["Important còn mở", "file ngoài scope"]),
        dict(k="d1", phase="D", agent="D1 SOC", tier=t("d1_soc", step="triage"),
             why="triage Haiku + remediation Sonnet", reads=["build_results"], writes=["security_report"],
             tok=[1600, 900]),
        dict(k="g4", phase="D", gate="G4", who="Tech Lead + bảo mật", surface=["High/Critical: 0", "UAT"]),
        dict(k="done", phase="D", agent="RELEASED", tier=None, reads=[], writes=[], tok=[0, 0]),
    ]


def estimate_payload():
    """Function list CÓ size (S/M/L/XL); enrich: historical → FACT, nếu không → parametric (không hỏi người)."""
    draft = {"wbs": [
        {"id": "W1", "item": "KYC onboarding flow", "module": "KYC", "role": "dev", "size": "L"},
        {"id": "W2", "item": "Gửi tiền quốc tế qua cổng FX", "module": "Remit", "role": "dev", "size": "XL"},
        {"id": "W3", "item": "Dashboard đối soát admin", "module": "Admin", "role": "dev", "size": "L"},
        {"id": "W4", "item": "Thông báo đẩy", "module": "Notif", "role": "dev", "size": "S"}],
        "assumptions": [], "exclusions": ["Pentest bên thứ ba"], "risks": [],
        "reflection": {"passed": True, "issues": []}}
    return enrich(draft, {"domain": "fintech", "blended_rate": 650, "tech_stack": []})


def gate_agents(steps, idx):
    """Agent (có spec) có output được duyệt ở gate tại steps[idx]: các bước cùng phase trước gate."""
    st = steps[idx]
    out = []
    for s in steps[:idx]:
        if s["phase"] != st["phase"] or s.get("gate"):
            continue
        cand = [STEP_AGENT[s["k"]]] if s.get("k") in STEP_AGENT else [ZONE_WRITER.get(z) for z in s.get("writes", [])]
        for a in cand:
            if a in AGENT_IDS and a not in out:
                out.append(a)
    return out


def step_cost(step):
    """Chi phí minh hoạ (USD) của một bước có tier."""
    return usage.step_cost(step["tier"], step["tok"])      # đơn giá: config/pricing.yaml
