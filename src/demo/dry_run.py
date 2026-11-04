"""Dry-run toàn pipeline (4 phase) với LLM mock — KHÔNG cần API key.
In rõ: mỗi agent ĐỌC vùng nào của Blackboard, CHỌN model tier nào (vì sao),
GHI vùng nào, và lúc nào GATE chờ người ký. Trọng tâm: dữ liệu chảy qua Blackboard.
Chạy: python -m src.demo.dry_run
"""
import tempfile
from src.blackboard.store import Blackboard
from src.gateway import router
from src.store import historical
from src.agents.a2_estimation import enrich

AR = "\u2192"
def line(c="\u2500"): print(c * 72)
def zones(b): 
    z = [k for k in b._data.keys()]
    print("   Blackboard hiện có:", ", ".join(z) if z else "(trống)")

def model_of(tier):
    return {"haiku":"Haiku","sonnet":"Sonnet","opus":"Opus"}[tier]

def step(b, n, agent, role, phase, reads, tier_call, writes_zone, payload, why_model):
    print(f"\n[{n}] {agent}  ({phase})")
    print(f"   ĐỌC Blackboard: {', '.join(reads) if reads else '(không)'}")
    for z in reads:
        got = b.get(z)
        mark = "có" if got is not None else "THIẾU"
        print(f"      - {z}: {mark}")
    tier = tier_call()
    print(f"   MODEL {AR} {model_of(tier)}   ({why_model})")
    if writes_zone:
        b.write(writes_zone, payload, agent=agent)
        print(f"   GHI Blackboard {AR} {writes_zone}")
    zones(b)

def gate(b, name, who, surface):
    line()
    print(f"\u25B6 {name} \u2014 chờ {who} ký (agent không ghi được vùng gate_signatures)")
    for s in surface:
        print(f"   người duyệt thấy trước: {s}")
    b.sign(name, by=who, via="gate_ui")
    print(f"   {who} đã ký {AR} Orchestrator cho đi tiếp")
    line()

def run():
    tmp = tempfile.mkdtemp()
    # seed historical DB fintech để Estimation ra FACT
    historical.append({"id":"H-fin-01","domain":"fintech","estimate_md_total":210,"actual_md_total":242,
        "wbs":[{"item":"KYC onboarding flow","role":"dev","estimate_md":18,"actual_md":22},
               {"item":"Lịch sử giao dịch","role":"dev","estimate_md":10,"actual_md":11}]}, base=tmp)

    b = Blackboard("DEMO-NeoPay")
    print("PIPELINE DRY-RUN \u2014 client NeoPay (fintech SG, chịu MAS)\n")
    b.write("lead_brief", {"client":"NeoPay","source":"email","received_at":"2026-10-25"}, agent="intake")
    print("[0] intake (Code, Haiku chuẩn hoá) GHI lead_brief"); zones(b)

    line("\u2550"); print("PHASE A \u2014 PRE-SALES"); line("\u2550")
    step(b,"A1","a1_bu","bu","presales",["lead_brief"],
        lambda: router.pick_tier("a1_bu"), "business_context",
        {"domain":"fintech","modules":[{"name":"KYC","desc":"onboarding","label":"FACT"}]},
        "brief rõ ràng, không mâu thuẫn")

    # Estimation: draft (LLM) -> enrich tra DB
    draft = {"wbs":[{"id":"W1","item":"KYC onboarding flow","module":"KYC","role":"dev"},
                    {"id":"W2","item":"Gửi tiền quốc tế qua cổng FX","module":"Remit","role":"dev"}],
             "assumptions":[],"exclusions":["Pentest bên thứ ba"],"risks":[],
             "reflection":{"passed":True,"issues":[]}}
    est = enrich(draft, {"domain":"fintech","blended_rate":650,"tech_stack":[],"hist_base":tmp})
    step(b,"A2","a2_estimation","estimation","presales",["business_context"],
        lambda: router.pick_tier("a2_estimation"), "estimate", est,
        "việc chính, chưa lệch bất thường")
    print("   \u2192 effort DB-driven:")
    for it in est["wbs"]:
        print(f"      {it['item'][:34]:34} {str(it['effort_md']):>5} MD   [{it['label']}]"
              + (f"  nguồn {it['source']['refs']}" if it['label']=="FACT" else ""))

    print("\n   (nếu Estimation lệch >30% so dự án tương tự:",
          model_of(router.pick_tier("a2_estimation", signals={"variance_gt_30pct"})), "\u2014 escalate)")

    step(b,"A3","a3_proposal","proposal","presales",["business_context","estimate"],
        lambda: router.pick_tier("a3_proposal", step="final_review"), "proposal",
        {"assumptions_section":est["assumptions"],"exclusions_section":est["exclusions"]},
        "bản cuối proposal luôn Opus review")

    gate(b,"G1","Sales Lead",
         [f"{sum(1 for i in est['wbs'] if i['label']=='ASSUMPTION')}/{len(est['wbs'])} hạng mục là ASSUMPTION",
          "exclusions: Pentest bên thứ ba", "khoảng chi phí (JS tính)"])

    line("\u2550"); print("PHASE B \u2014 DISCOVERY & DESIGN"); line("\u2550")
    step(b,"B1","b1_ba","ba","discovery",["proposal","business_context"],
        lambda: router.pick_tier("b1_ba"), "requirements",
        {"stories":[{"as_a":"user","i_want":"KYC","so_that":"dùng ví","AC":["Given ... When ... Then ..."]}],
         "open_questions":[{"q":"Nhà cung cấp FX?","owner":"PM","due":"2026-11-01"}]},
        "story thường")
    step(b,"B2","b2_ux","ux","discovery",["requirements"],
        lambda: router.pick_tier("b2_ux"), "design_spec",
        {"flows":["onboarding"],"screens":[{"id":"S1","purpose":"KYC","states":["empty","loading","error"]}]},
        "designer mặc định Sonnet")
    step(b,"B3","b3_ui","ui","discovery",["design_spec"],
        lambda: router.pick_tier("b3_ui"), "ui_system",
        {"tokens_css":":root{--primary:#0F6E6E}","components":[{"name":"Button","html":"<button>OK</button>","states":["default"]}],
         "screens":[{"id":"S1","html":"<main></main>","macrostructure":"split-hero"}]},
        "B3 UI dựng design system, dùng hallmark chống slop")
    step(b,"B4","b4_tech_lead","tech_lead","discovery",["requirements","design_spec"],
        lambda: router.pick_tier("b4_tech_lead"), "architecture",
        {"components":["app","api","kyc-svc"]}, "quyết định kiến trúc luôn Opus")
    b.write("task_backlog", [{"id":"T1","story_ref":"KYC","spec":"...","AC":["..."],"risk_level":"high"}], agent="b4_tech_lead")
    print("   GHI Blackboard \u2192 task_backlog (T1, risk_level=high)")

    gate(b,"G2","Client + PM", ["scope_delta","open_questions còn mở: Nhà cung cấp FX?"])

    line("\u2550"); print("PHASE C \u2014 BUILD (vòng Dev\u2192Tester\u2192QC)"); line("\u2550")
    print("\n[C1] c1_dev (task T1, risk_level=high)")
    print(f"   MODEL {AR} {model_of(router.pick_tier('c1_dev', risk_level='high'))}   (task high-risk)")
    print("   (tool stub: repo, run_tests, lint) GHI \u2192 build_results.dev")
    print("\n[C2] c2_tester")
    print(f"   MODEL {AR} sinh test {model_of(router.pick_tier('c2_tester', step='generate'))}"
          f" + tóm tắt {model_of(router.pick_tier('c2_tester', step='summarize'))}")
    print("\n[C3] c3_qc  \u2014 reviewer không yếu hơn generator")
    qc1 = router.pick_tier("c3_qc", risk_level="high")
    print(f"   MODEL {AR} {model_of(qc1)}   (high-risk)")
    print("   verdict = FAIL (còn 1 Critical) \u2192 quay lại Dev (vòng 1/3)")
    print("\n[C1'] c1_dev retry")
    print(f"   MODEL {AR} {model_of(router.pick_tier('c1_dev', signals={'retry_2'}))}   (retry lần 2 \u2192 escalate Opus)")
    print("[C3'] c3_qc \u2192 verdict = PASS")
    b.write("build_results", {"all_checks_pass":True,"qc_critical":False}, agent="c3_qc")
    print("   GHI Blackboard \u2192 build_results (pass)"); zones(b)
    gate(b,"G3","Tech Lead", ["Important còn mở","file ngoài scope","dependency mới"])

    line("\u2550"); print("PHASE D \u2014 SECURITY & RELEASE"); line("\u2550")
    print("\n[D1] d1_soc (defensive)")
    print(f"   MODEL {AR} triage {model_of(router.pick_tier('d1_soc', step='triage'))}"
          f" + remediation {model_of(router.pick_tier('d1_soc', step='remediation'))} (chỉ High/Critical)")
    print("   (tool: sast, dependency_scan, secret_scan) GHI \u2192 security_report")
    b.write("security_report", {"findings":[],"gate":"pass"}, agent="d1_soc")
    zones(b)
    gate(b,"G4","Tech Lead + bảo mật", ["finding High/Critical: 0","QC Critical: 0","UAT"])

    line("\u2550"); print("RELEASED \u2014 toàn bộ dữ liệu đã chảy qua Blackboard:"); line("\u2550")
    zones(b)
    print("\nMỗi vùng do đúng 1 agent ghi; gate_signatures chỉ người ký.")
    print("Khi đóng dự án: close_project ghi actual vào historical DB \u2192 lần sau thêm item thành FACT.")

if __name__ == "__main__":
    run()
