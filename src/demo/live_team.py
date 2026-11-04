"""Chạy thử Phòng 3D realtime: một dự án mẫu ở PROCESS RIÊNG ghi vào event store thật.

Không API key, không gọi model: huddle dùng `sender` mock, Dispatcher dùng worker mock (có sleep để
mắt người kịp thấy). Mọi ghi đi qua Blackboard (kiểm quyền + schema) rồi ghi xuyên store; KHÔNG ký gate
(gate chỉ do người ký qua gate_ui) — dự án dừng ở "chờ G3".

Chạy (2 terminal):
  uvicorn src.server.app:app                       # dashboard đọc store (config/runtime.yaml)
  python -m src.demo.live_team [--pace 0.8] [--project LIVE-x] [--db path]
rồi mở http://localhost:8000/room?project=<id in ra> (hoặc chọn trong dropdown, chấm xanh = live).
"""
from __future__ import annotations

import argparse
import copy
import json
import tempfile
import threading
import time
from pathlib import Path

from src.agents import huddle
from src.blackboard.eventstore import EventStore, default_path
from src.blackboard.store import Blackboard
from src.gateway import llm_client, trace
from src.orchestrator import status as pstatus
from src.orchestrator.autonomy import Autonomy
from src.orchestrator.dispatcher import Dispatcher
from src.server import demo

BACKLOG = [
    ("T1", "eKYC onboarding 4 bước + OCR", [], ["src/kyc/"], "high"),
    ("T2", "Chuyển tiền quốc tế qua cổng FX", [], ["src/remit/"], "high"),
    ("T3", "Dashboard đối soát admin", [], ["src/admin/"], "normal"),
    ("T4", "Hạn mức giao dịch theo cấp KYC", ["T1"], ["src/limits/"], "normal"),
    ("T5", "Thông báo đẩy giao dịch", ["T2", "T3"], ["src/notif/"], "low"),
]


def _sender(pace: float, question: str, options: list[str], chosen: str):
    """LLM giả cho huddle: trả JSON theo vai (đọc từ system prompt), ngủ `pace` mỗi lượt."""
    n = {"proposer": 0, "critic": 0}

    def send(model, system, messages, max_tokens):
        time.sleep(pace)
        if "PROPOSER" in system:
            n["proposer"] += 1
            out = {"summary": f"đề xuất v{n['proposer']}: {' / '.join(options)}", "options": options}
        elif "CRITIC" in system:
            n["critic"] += 1
            out = {"summary": f"phản biện v{n['critic']}: rủi ro tích hợp, cần hỏi client",
                   "risks": ["phụ thuộc đối tác"], "open_questions": ["Client chốt SLA chưa?"],
                   "converged": n["critic"] >= 2}
        else:
            out = {"question": question, "options": options, "chosen": chosen, "rationale": "ít rủi ro hơn",
                   "risks": ["phụ thuộc đối tác"], "open_questions": ["Client chốt SLA chưa?"]}
        return json.dumps(out, ensure_ascii=False), {"input_tokens": 900, "output_tokens": 300, "total_tokens": 1200}
    return send


def _burn(project_id: str, agent: str, tier: str, phase: str, tin: int, tout: int) -> None:
    """Một lần "gọi model" qua gateway thật (budget + trace) với sender mock: chỉ sinh số token."""
    llm_client.call(agent, tier, "", [{"role": "user", "content": ""}], project_id, phase, max_tokens=tout,
                    sender=lambda m, s, msg, mt: ("{}", {"input_tokens": tin, "output_tokens": tout,
                                                         "total_tokens": tin + tout}))


def _worker(pace: float, fail_once: set[str]):
    """Worker mock C1→C2→C3: báo substate, ngủ; task trong fail_once trượt QC lần đầu rồi retry."""
    seen: set[str] = set()
    lock = threading.Lock()

    def work(contract: dict, h) -> dict:
        tid = contract["task_id"]
        pid = h.board.project_id
        tier = "opus" if contract["input_slice"]["risk_level"] == "high" else "sonnet"
        time.sleep(pace * 1.5)
        _burn(pid, "c1_dev", tier, "build", 3000, 2400)
        h.report("testing")
        time.sleep(pace * 1.2)
        _burn(pid, "c2_tester", "sonnet", "build", 1400, 1000)
        h.report("qc")
        time.sleep(pace)
        _burn(pid, "c3_qc", tier, "build", 1200, 700)
        with lock:
            first = tid not in seen
            seen.add(tid)
        if tid in fail_once and first:
            return {"ok": False, "reason": "c3_qc verdict fail: 1 Critical (thiếu kiểm hạn mức)",
                    "qc_critical": True, "tier": "sonnet"}
        return {"ok": True, "qc_critical": False, "tier": "sonnet", "tokens_used": 0}
    return work


def run(project_id: str, store: EventStore, pace: float = 0.8, fail_once=("T2",), log=print) -> dict:
    b = Blackboard(project_id, store=store)
    tmp = Path(tempfile.mkdtemp(prefix="live_team_"))
    trace.LOG = tmp / "trace.jsonl"          # trace của sender mock không lẫn vào eval/runs/
    ctx = {"project_id": project_id}

    def step(msg, fn):
        log(f"  · {msg}")
        fn()
        time.sleep(pace)

    log(f"[{project_id}] PRESALES")
    step("project_status presales", lambda: pstatus.update(b, "presales"))
    step("intake → lead_brief", lambda: b.write(
        "lead_brief", {"client": "NeoPay", "source": "email", "received_at": "2026-10-25"}, agent="intake"))
    step("a1_bu → business_context", lambda: _burn(project_id, "a1_bu", "sonnet", "presales", 1800, 900) or b.write(
        "business_context", {"domain": "fintech", "modules": [{"name": "KYC"}, {"name": "Remit"}],
                             "ambiguities": ["FX", "SLA", "hạn mức"]}, agent="a1_bu"))
    step("a1_bu → risk FX (Medium)", lambda: b.append(
        "risk_register", {"risk": "Cổng FX chưa chốt", "severity": "Medium", "status": "open"}, agent="a1_bu"))
    step("huddle presales", lambda: huddle.run(b, "presales", "Chọn cổng FX?", {"modules": 2}, ctx,
                                               sender=_sender(pace, "Chọn cổng FX?", ["Wise", "Nium"], "Wise"), trigger="ambiguities"))
    step("a2_estimation → estimate", lambda: (_burn(project_id, "a2_estimation", "sonnet", "presales", 2200, 1400),
                                               b.write("estimate", demo.estimate_payload(), agent="a2_estimation")))
    step("a3_proposal → proposal", lambda: _burn(project_id, "a3_proposal", "opus", "presales", 2600, 1700)
         or b.write("proposal", {"summary": "proposal bản cuối"}, agent="a3_proposal"))

    log(f"[{project_id}] DISCOVERY")
    step("project_status discovery", lambda: pstatus.update(b, "discovery"))
    step("huddle discovery", lambda: huddle.run(b, "discovery", "Flow KYC?", {"stories": 12}, ctx,
                                                sender=_sender(pace, "Flow KYC?", ["7 bước", "4 bước + OCR"], "4 bước + OCR"),
                                                trigger="ambiguities"))
    step("b1_ba → requirements", lambda: _burn(project_id, "b1_ba", "sonnet", "discovery", 2000, 1500)
         or b.write("requirements", copy.deepcopy(demo.REQUIREMENTS), agent="b1_ba"))
    step("b2_ux → design_spec", lambda: _burn(project_id, "b2_ux", "sonnet", "discovery", 1500, 1100)
         or b.write("design_spec", {"screens": 9}, agent="b2_ux"))
    step("b4_tech_lead → task_backlog", lambda: _burn(project_id, "b4_tech_lead", "opus", "discovery", 2800, 2000)
         or b.write("task_backlog", [
        {"id": i, "story_ref": "S1", "spec": t, "title": t, "AC": [f"AC {i}"], "deps": d, "owns": o,
         "size": "M", "risk_level": r} for i, t, d, o, r in BACKLOG], agent="b4_tech_lead"))

    log(f"[{project_id}] BUILD (Dispatcher, worker mock)")
    pstatus.update(b, "build")
    ax = Autonomy(state_path=tmp / "autonomy_state.json", reviews_log=tmp / "reviews.jsonl",
                  feedback_log=tmp / "feedback.jsonl", regression_log=tmp / "regression.jsonl")
    res = Dispatcher(b, worker=_worker(pace, set(fail_once)), autonomy=ax).run()
    log(f"  · dispatcher: done={res['done']} blocked={res['blocked']} max_concurrency={res['max_concurrency']}")
    time.sleep(pace)
    step("project_status build (chờ G3 — người ký trên Dashboard)", lambda: pstatus.update(b, "build"))
    return {"project": project_id, "dispatcher": res, "last_seq": store.last_seq(project_id)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dự án mẫu ghi event store thật (process riêng, không API key)")
    ap.add_argument("--project", default=None, help="project id (mặc định LIVE-NeoPay-<giờ>)")
    ap.add_argument("--db", default=None, help="đường dẫn SQLite (mặc định config/runtime.yaml / OA_EVENTSTORE_PATH)")
    ap.add_argument("--pace", type=float, default=0.8, help="giây nghỉ giữa các bước (0 = nhanh nhất)")
    a = ap.parse_args(argv)
    pid = a.project or time.strftime("LIVE-NeoPay-%H%M%S")
    store = EventStore(a.db or default_path())
    print(f"event store: {store.path}\nproject: {pid}\nmở: http://localhost:8000/room?project={pid}")
    out = run(pid, store, pace=a.pace)
    print(f"xong: last_seq={out['last_seq']}")


if __name__ == "__main__":
    main()
