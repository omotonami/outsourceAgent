"""Dashboard thật — FastAPI cắm vào engine Python.
Hiển thị data flow Blackboard, model routing, gate duyệt/trả lại, token & chi phí THẬT.
Chạy: uvicorn src.server.app:app --reload   rồi mở http://localhost:8000
Mock mặc định (không tốn token). Đặt LIVE=1 + ANTHROPIC_API_KEY để gọi model thật."""
import os, json, time
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.blackboard.store import Blackboard
from src.gateway import router
from src.store import historical
from src.agents.a2_estimation import enrich
from src.iso import impact, conformance, inventory
from src.orchestrator import status as pstatus
from src.orchestrator import gate_view
from src.agents import e1_pm

HERE = Path(__file__).parent
app = FastAPI(title="Outsourcing Agents Dashboard")
app.mount("/static", StaticFiles(directory=HERE/"static"), name="static")

# giá minh hoạ USD / token (in, out)
PRICE = {"haiku":(1e-6,5e-6), "sonnet":(3e-6,15e-6), "opus":(15e-6,75e-6)}

# ----- kịch bản bước, nối vào router thật để lấy tier + lý do -----
def plan(dev_high):
    risk = "high" if dev_high else "normal"
    dev = router.pick_tier("c1_dev", risk_level=risk)
    qc  = router.pick_tier("c3_qc", risk_level=risk, generator_tier=dev)
    def t(agent, **kw): return router.pick_tier(agent, **kw)
    return [
      dict(k="intake", phase="A", agent="intake", tier="haiku", why="chuẩn hoá RFP",
           reads=[], writes=["lead_brief"], tok=[300,120]),
      dict(k="a1", phase="A", agent="A1 BU", tier=t("a1_bu"), why="brief rõ, không mâu thuẫn",
           reads=["lead_brief"], writes=["business_context"], tok=[1800,900]),
      dict(k="a2", phase="A", agent="A2 Estimation", tier=t("a2_estimation"), why="việc chính",
           reads=["business_context"], writes=["estimate"], tok=[2200,1400], special="estimate"),
      dict(k="a3", phase="A", agent="A3 Proposal", tier=t("a3_proposal", step="final_review"),
           why="bản cuối luôn Opus", reads=["business_context","estimate"], writes=["proposal"], tok=[2600,1700]),
      dict(k="g1", phase="A", gate="G1", who="Sales Lead",
           surface=["hạng mục ASSUMPTION","exclusions","khoảng chi phí"]),
      dict(k="b1", phase="B", agent="B1 BA", tier=t("b1_ba"), why="story thường",
           reads=["proposal","business_context"], writes=["requirements"], tok=[2000,1500]),
      dict(k="b2", phase="B", agent="B2 Designer", tier=t("b2_ux"), why="mặc định Sonnet",
           reads=["requirements"], writes=["design_spec"], tok=[1500,1100]),
      dict(k="b2b", phase="B", agent="B3 UI", tier=t("b3_ui"), why="design system",
           reads=["design_spec"], writes=["ui_system"], tok=[2200,1800]),
      dict(k="b3", phase="B", agent="B4 Tech Lead", tier=t("b4_tech_lead"), why="kiến trúc luôn Opus",
           reads=["requirements","design_spec"], writes=["architecture","task_backlog"], tok=[2800,2000]),
      dict(k="g2", phase="B", gate="G2", who="Client + PM", surface=["scope_delta","open_questions"]),
      dict(k="c1", phase="C", agent="C1 Dev", tier=dev, why=("task high-risk" if dev_high else "task thường"),
           reads=["task_backlog"], writes=["build_results"], tok=([3000,2400] if dev_high else [2200,1600])),
      dict(k="c2", phase="C", agent="C2 Tester", tier=t("c2_tester", step="generate"),
           why="sinh test Sonnet + tóm tắt Haiku", reads=["build_results"], writes=["build_results"], tok=[1400,1000]),
      dict(k="c3f", phase="C", agent="C3 QC", tier=qc, why="reviewer ≥ tier Dev",
           reads=["build_results"], writes=[], tok=[1200,700], verdict="FAIL", loop="1 Critical → Dev"),
      dict(k="c1r", phase="C", agent="C1 Dev (retry)", tier=t("c1_dev", signals=["retry_2"]),
           why="retry lần 2 → escalate", reads=["task_backlog"], writes=["build_results"], tok=[3000,2400]),
      dict(k="c3p", phase="C", agent="C3 QC", tier="opus", why="reviewer ≥ tier Dev",
           reads=["build_results"], writes=["build_results"], tok=[1200,700], verdict="PASS"),
      dict(k="g3", phase="C", gate="G3", who="Tech Lead", surface=["Important còn mở","file ngoài scope"]),
      dict(k="d1", phase="D", agent="D1 SOC", tier=t("d1_soc", step="triage"),
           why="triage Haiku + remediation Sonnet", reads=["build_results"], writes=["security_report"], tok=[1600,900]),
      dict(k="g4", phase="D", gate="G4", who="Tech Lead + bảo mật", surface=["High/Critical: 0","UAT"]),
      dict(k="done", phase="D", agent="RELEASED", tier=None, reads=[], writes=[], tok=[0,0]),
    ]

# ----- state phiên (1 dự án demo) -----
class Session:
    def __init__(self):
        self.reset(True)
    def reset(self, dev_high=True):
        self.dev_high = dev_high
        self.steps = plan(dev_high)
        self.i = 0
        self.board = Blackboard("DASH")
        self.board.write("lead_brief", {"client":"NeoPay","source":"email","received_at":"2026-10-25"}, agent="intake")
        self.log = []
        self.signed = {}
        # seed historical để A2 ra FACT
        self._tmp = None
    def zones(self):
        return [z for z in self.board._data.keys()]

S = Session()

def _estimate_payload():
    # function list CÓ size (S/M/L/XL); enrich: historical->FACT, nếu không->parametric (không hỏi người)
    draft = {"wbs":[
        {"id":"W1","item":"KYC onboarding flow","module":"KYC","role":"dev","size":"L"},
        {"id":"W2","item":"Gửi tiền quốc tế qua cổng FX","module":"Remit","role":"dev","size":"XL"},
        {"id":"W3","item":"Dashboard đối soát admin","module":"Admin","role":"dev","size":"L"},
        {"id":"W4","item":"Thông báo đẩy","module":"Notif","role":"dev","size":"S"}],
        "assumptions":[], "exclusions":["Pentest bên thứ ba"], "risks":[],
        "reflection":{"passed":True,"issues":[]}}
    return enrich(draft, {"domain":"fintech","blended_rate":650,"tech_stack":[]})

def _do_step():
    if S.i >= len(S.steps): return {"status":"done"}
    st = S.steps[S.i]
    if st.get("gate"):  # H3: kèm decision_records (không transcript) cho G1/G2
        return {"status":"gate", "gate":st, "decision_records":gate_view.decision_records(S.board, st["gate"])}
    # ghi Blackboard thật, đúng agent-writer của từng vùng (nếu sai, Blackboard sẽ chặn)
    ZW = {"lead_brief":"intake","business_context":"a1_bu","estimate":"a2_estimation",
          "proposal":"a3_proposal","requirements":"b1_ba","design_spec":"b2_ux","ui_system":"b3_ui",
          "architecture":"b4_tech_lead","task_backlog":"b4_tech_lead",
          "build_results":"c1_dev","security_report":"d1_soc"}
    for z in st.get("writes", []):
        wa = ZW.get(z, st.get("k","agent"))
        if z == "estimate": S.board.write("estimate", _estimate_payload(), agent="a2_estimation")
        elif z == "security_report": S.board.write("security_report", {"findings":[],"gate":"pass"}, agent="d1_soc")
        elif z == "requirements": S.board.write("requirements",
             {"stories":[{"as_a":"user","i_want":"KYC","so_that":"dùng ví","AC":["Given..When..Then.."]}],
              "open_questions":[{"q":"FX?","owner":"PM","due":"2026-11-01"}]}, agent="b1_ba")
        elif z == "task_backlog": S.board.write("task_backlog",
             [{"id":"T1","story_ref":"KYC","spec":"...","AC":["..."],"risk_level":"high"}], agent="b4_tech_lead")
        else:
            try: S.board.write(z, {"_by":st["agent"]}, agent=wa)
            except Exception: pass
    if st.get("tier"):
        pin, pout = PRICE[st["tier"]]
        c = st["tok"][0]*pin + st["tok"][1]*pout
        S.log.append({"agent":st["agent"], "tier":st["tier"], "tok":st["tok"], "cost":round(c,6)})
    S.i += 1
    return {"status":"ran"}

@app.get("/api/state")
def state():
    try:
        pstatus.update(S.board, S.steps[S.i]["phase"] if S.i<len(S.steps) else "done",
                       budget_ratio=min(0.99, S.total if False else 0))
    except Exception: pass
    est = S.board.get("estimate")
    cur = S.steps[S.i] if S.i < len(S.steps) else {}
    return {
      "gate_decisions": (gate_view.decision_records(S.board, cur["gate"]) if cur.get("gate") else []),
      "i": S.i, "steps": S.steps, "zones": S.zones(), "signed": S.signed,
      "log": S.log, "total_cost": round(sum(e["cost"] for e in S.log),6),
      "total_tok": sum(e["tok"][0]+e["tok"][1] for e in S.log),
      "dev_high": S.dev_high,
      "estimate_preview": (est["wbs"] if est else None),
    }

@app.post("/api/step")
def step(): return {**_do_step(), **state()}

@app.post("/api/gate/{decision}")
def gate(decision: str):
    st = S.steps[S.i]
    if not st.get("gate"): return JSONResponse({"error":"not at gate"}, 400)
    gid = st["gate"]
    if decision == "approve":
        S.signed[gid] = "approved"; S.board.sign(gid, by="human", via="gate_ui"); S.i += 1
    else:
        S.signed[gid] = "rejected"
        back = next((j for j,s in enumerate(S.steps) if s["phase"]==st["phase"] and not s.get("gate")), 0)
        S.signed.pop(gid, None); S.i = back
    return state()

@app.get("/api/gate/{gid}/decisions")
def gate_decisions(gid: str):
    """H3: decision_record của huddle cho gate (G1 presales, G2 discovery). Không có transcript."""
    return {"gate": gid, "decision_records": gate_view.decision_records(S.board, gid)}

@app.post("/api/reset")
def reset(dev_high: bool = True):
    S.reset(dev_high); return state()

@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE/"static"/"index.html").read_text(encoding="utf-8")


@app.get("/api/iso")
def iso_state():
    bc = S.board.get("business_context") or {"domain":"fintech","modules":[]}
    flags = bc.get("compliance_flags", ["PDPA","MAS TRM"])
    tri = impact.triage(flags)
    inventory.register("DASH", "ví điện tử NeoPay", ["sonnet","opus","haiku"], "PII (KYC)", "SG")
    conf = conformance.check(S.board, "DASH",
             trace_path=None, inventory_base=None,
             impact_present=True)
    return {"triage": tri, "impact": impact.draft(bc, flags), "conformance": conf}


@app.get("/api/pm")
def pm_report():
    """PM reporting (chỉ đọc): tổng hợp tiến độ + cảnh báo cho PM người."""
    try:
        pstatus.update(S.board, S.steps[S.i]["phase"] if S.i < len(S.steps) else "done", budget_ratio=0.0)
    except Exception:
        pass
    return {"project_status": S.board.get("project_status"), "report": e1_pm.report(S.board)}
