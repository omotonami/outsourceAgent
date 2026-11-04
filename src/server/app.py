"""Dashboard thật — FastAPI cắm vào engine Python.
Hiển thị data flow Blackboard, model routing, gate duyệt/trả lại, token & chi phí THẬT.
Chạy: uvicorn src.server.app:app --reload   rồi mở http://localhost:8000 (Control Room; dashboard cũ: /legacy)
Mock mặc định (không tốn token). Đặt LIVE=1 + ANTHROPIC_API_KEY để gọi model thật.
Kịch bản bước + payload mẫu: src/server/demo.py."""
import copy
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jsonschema import ValidationError

from src.agents import e1_pm
from src.blackboard import eventstore
from src.blackboard.store import Blackboard, PermissionDenied
from src.common import config
from src.common.config import ConfigError
from src.governance import confirm
from src.iso import conformance, impact, inventory
from src.orchestrator import gate_view, room_state, room_stream, team_feed, usage
from src.orchestrator import status as pstatus
from src.orchestrator.autonomy import Autonomy
from src.server import demo
from src.server.demo import plan

HERE = Path(__file__).parent
log = logging.getLogger(__name__)
app = FastAPI(title="Outsourcing Agents Dashboard")
app.mount("/static", StaticFiles(directory=HERE/"static"), name="static")


@app.middleware("http")
async def _guard_writes(request: Request, call_next):
    """Mọi POST/PUT/PATCH/DELETE: kiểm Content-Type JSON + Host/Origin cục bộ (write_refusal) trước handler."""
    bad = write_refusal(request.method, request.headers)
    if bad:
        return JSONResponse({"error": bad[1]}, bad[0])
    return await call_next(request)
ZW = demo.ZONE_WRITER                 # tên cũ
AGENT_IDS = demo.AGENT_IDS
FEED_LIMIT_MAX = 500                  # trần số event /api/feed trả về
REVIEW_LOG_ERRORS = (OSError, ValueError, ConfigError)   # lỗi ghi review autonomy được bắt sau khi ký
REVIEW_RISK_SEVERITY = "Medium"
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# ----- chặn ghi chéo nguồn (CSRF / DNS rebinding) cho MỌI request ghi -----
def allowed_hosts() -> set[str]:
    """Host/Origin được phép ghi: localhost, 127.0.0.1, ::1 + dashboard.allowed_hosts (config/runtime.yaml)."""
    extra = (config.load_yaml("config/runtime.yaml").get("dashboard") or {}).get("allowed_hosts") or []
    return {"localhost", "127.0.0.1", "::1"} | {str(h).strip().lower() for h in extra}


def _hostname(netloc: str) -> str | None:
    try:
        return urlsplit("//" + netloc).hostname
    except ValueError:
        return None


def write_refusal(method: str, headers) -> tuple[int, str] | None:
    """(mã, lý do) nếu request ghi bị từ chối, None nếu cho qua.
    - Content-Type phải là application/json (form/text từ trang lạ không gửi được mà không preflight) → 415.
    - Host phải thuộc allowed_hosts() (chống DNS rebinding) → 403.
    - Origin (nếu có) phải http(s) với hostname thuộc allowed_hosts(); "null"/lạ → 403. Thiếu Origin (curl,
      script cục bộ) chỉ qua khi Host hợp lệ."""
    if method.upper() not in WRITE_METHODS:
        return None
    ok = allowed_hosts()
    host = _hostname(headers.get("host") or "")
    if not host or host.lower() not in ok:
        return 403, "Host không được phép ghi (chỉ localhost/127.0.0.1 hoặc dashboard.allowed_hosts)"
    origin = headers.get("origin")
    if origin is not None:
        u = urlsplit(origin) if origin.strip().lower() != "null" else None
        oh = u.hostname if u and u.scheme in ("http", "https") else None
        if not oh or oh.lower() not in ok:
            return 403, "Origin không được phép ghi"
    ctype = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        return 415, "Content-Type phải là application/json"
    return None


# ----- state phiên (1 dự án demo) -----
class Session:
    def __init__(self):
        self.reset(True)

    def reset(self, dev_high=True):
        self.dev_high = dev_high
        self.steps = plan(dev_high)
        self.i = 0
        self.board = Blackboard("DASH", store=None)   # board demo trong RAM: server không ghi event store
        self.board.write("lead_brief", copy.deepcopy(demo.LEAD_BRIEF), agent="intake")
        self.log = []
        self.signed = {}

    def zones(self):
        return list(self.board._data.keys())


S = Session()
AUTONOMY = None   # Autonomy() lười tạo; test thay bằng instance có state/log ở tmp


def _autonomy():
    global AUTONOMY
    if AUTONOMY is None:
        AUTONOMY = Autonomy()
    return AUTONOMY


def _gate_agents(idx):
    return demo.gate_agents(S.steps, idx)


def _record_gate_reviews(idx, gid, reviewer):
    """H4: người duyệt ký gate KHÔNG sửa → ghi review cho từng agent (autonomy.record_review).
    CHỈ ghi log — chữ ký đã do người ký qua gate_ui ở trên; autonomy không ký thay."""
    ax = _autonomy()
    tt = (ax.cfg.get("gate_task_types") or {}).get(gid, gid.lower())
    return [ax.record_review(a, tt, gid, reviewer=reviewer, edited=False, approved=True)
            for a in _gate_agents(idx)]


def _record_reviews_safely(idx, gid, reviewer):
    """Chữ ký gate đã có hiệu lực: lỗi ghi review autonomy (file log, config) KHÔNG làm request lỗi —
    ghi risk_register (writer gate_ui) để người xử lý. Lỗi khác vẫn nổi lên."""
    try:
        return _record_gate_reviews(idx, gid, reviewer)
    except REVIEW_LOG_ERRORS as e:
        msg = f"Gate {gid} đã ký nhưng không ghi được review autonomy: {type(e).__name__}: {e}"
        log.warning(msg)
        S.board.append("risk_register", {"risk": msg, "severity": REVIEW_RISK_SEVERITY, "status": "open",
                                         "phase": "autonomy", "gate": gid}, agent="gate_ui")
        return None


def _update_status():
    """project_status cho bước hiện tại. Schema project_status không khớp → bỏ qua (dashboard chỉ đọc)."""
    try:
        pstatus.update(S.board, S.steps[S.i]["phase"] if S.i < len(S.steps) else "done", budget_ratio=0.0)
    except ValidationError as e:
        log.warning("project_status không ghi được: %s", e.message)


def _demo_write(zone, step):
    """Ghi zone theo đúng agent-writer (Blackboard chặn nếu sai). Payload giả không khớp schema
    zone (vd lead_brief, ui_system) → bỏ qua như trước: demo chỉ minh hoạ luồng."""
    if zone == "estimate":
        S.board.write("estimate", demo.estimate_payload(), agent="a2_estimation")
    elif zone == "security_report":
        S.board.write("security_report", copy.deepcopy(demo.SECURITY_REPORT), agent="d1_soc")
    elif zone == "requirements":
        S.board.write("requirements", copy.deepcopy(demo.REQUIREMENTS), agent="b1_ba")
    elif zone == "task_backlog":
        S.board.write("task_backlog", copy.deepcopy(demo.TASK_BACKLOG), agent="b4_tech_lead")
    else:
        try:
            S.board.write(zone, {"_by": step["agent"]}, agent=ZW.get(zone, step.get("k", "agent")))
        except (ValidationError, PermissionDenied):
            pass


def _do_step():
    if S.i >= len(S.steps):
        return {"status": "done"}
    st = S.steps[S.i]
    if st.get("gate"):  # H3: kèm decision_records (không transcript) cho G1/G2
        return {"status": "gate", "gate": st, "decision_records": gate_view.decision_records(S.board, st["gate"])}
    for z in st.get("writes", []):
        _demo_write(z, st)
    if st.get("tier"):
        S.log.append({"agent": st["agent"], "tier": st["tier"], "tok": st["tok"],
                      "cost": round(demo.step_cost(st), 6)})
    S.i += 1
    return {"status": "ran"}


@app.get("/api/state")
def state():
    _update_status()
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
def gate(decision: str, edited: bool = False, reviewer: str = "human"):
    """edited=True: người duyệt đã sửa output (sửa được ghi qua gate_feedback.record_edit) → không
    tính là duyệt-không-sửa cho autonomy."""
    st = S.steps[S.i]
    if not st.get("gate"): return JSONResponse({"error":"not at gate"}, 400)
    gid = st["gate"]
    if decision == "approve" and not confirm.is_human(reviewer):
        return JSONResponse({"error": f"reviewer {reviewer!r} không phải người"}, 400)
    if decision == "approve":
        S.signed[gid] = "approved"; S.board.sign(gid, by="human", via="gate_ui")
        if not edited:
            _record_reviews_safely(S.i, gid, reviewer)
        S.i += 1
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
    """Control Room (một trang): Phòng 3D tất cả dự án + Chat CoS | Việc | Gate | Sự cố | Token."""
    return (HERE/"static"/"control.html").read_text(encoding="utf-8")


@app.get("/legacy", response_class=HTMLResponse)
def legacy():
    """Dashboard cũ (pipeline demo DASH, Team feed, Gate dự án) — giữ để không vỡ thói quen/link cũ."""
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
    _update_status()
    return {"project_status": S.board.get("project_status"), "report": e1_pm.report(S.board)}


@app.get("/api/feed")
def feed(limit: int = 100):
    """H5 Team feed (chỉ đọc): timeline ASSIGN/SPAWN/RESULT/BLOCKED/DECISION/KILL + cây spawn.
    Chỉ summary — không raw thinking/transcript (src/orchestrator/team_feed.py)."""
    return team_feed.build(S.board, limit=max(1, min(int(limit), FEED_LIMIT_MAX)))


def _step_agents(st):
    """Agent (có spec) của một bước demo: STEP_AGENT theo k, không thì writer các zone bước đó ghi."""
    cand = [demo.STEP_AGENT[st["k"]]] if st.get("k") in demo.STEP_AGENT else \
        [demo.ZONE_WRITER.get(z) for z in st.get("writes", [])]
    return [a for a in cand if a in AGENT_IDS]


def _room_levels():
    """Mức autonomy cao nhất của mỗi agent (L0–L2). Lỗi đọc state/config → {} (view chỉ đọc)."""
    try:
        ax = _autonomy()
        tts = set((ax.cfg.get("gate_task_types") or {}).values()) | {"build_task"}
        return {a: max((ax.level(a, tt) for tt in tts), default="L0") for a in AGENT_IDS}
    except REVIEW_LOG_ERRORS:
        return {}


# ----- Phòng 3D realtime: đọc event store dùng chung (process khác ghi), CHỈ ĐỌC (ghi duy nhất: gate dự án bên dưới) -----
EVENTSTORE = None     # test thay bằng EventStore(tmp, readonly=True); None → store theo config (readonly)


def _store():
    return EVENTSTORE if EVENTSTORE is not None else eventstore.EventStore(eventstore.default_path(), readonly=True)


def _rt(key):
    return eventstore.settings()[key]


def _known(project: str) -> bool:
    return _store().last_seq(project) > 0


@app.get("/api/projects")
def projects():
    """Project có trong event store: {id, last_ts, phase, state, active (ghi trong live_window_s)}; live trước."""
    return {"projects": room_stream.projects(_store(), live_window_s=float(_rt("live_window_s"))),
            "live_window_s": _rt("live_window_s"), "now": time.time(),
            "pricing": {t: {"in": pin * 1e6, "out": pout * 1e6} for t, (pin, pout) in usage.prices().items()}}


@app.get("/api/room/frames")
def room_frames(project: str, since: int = 0):
    """Replay (chỉ đọc): frame theo seq từ `since` — cùng dạng frame của /api/room/stream."""
    if not _known(project):
        return JSONResponse({"error": f"không có project {project!r} trong event store"}, 404)
    feed = room_stream.RoomFeed(_store(), project, since=max(0, int(since)))
    return {"project": project, "frames": feed.poll(_room_levels(), limit=room_stream.FRAMES_MAX)}


@app.get("/api/room/stream")
async def room_stream_sse(request: Request, project: str, since: int | None = None):
    """SSE (text/event-stream): frame mới theo seq. Last-Event-ID (nối lại) ưu tiên hơn `since`;
    không có cả hai → gửi event `snapshot` trước. Chỉ đọc store."""
    if not _known(project):
        return JSONResponse({"error": f"không có project {project!r} trong event store"}, 404)
    hdr = request.headers.get("last-event-id")
    if hdr and hdr.strip().isdigit():
        since = int(hdr)
    feed = room_stream.RoomFeed(_store(), project, since=since)
    gen = room_stream.stream(feed, poll_s=float(_rt("poll_ms")) / 1000, heartbeat_s=float(_rt("heartbeat_s")),
                             levels=_room_levels(), initial=since is None, is_disconnected=request.is_disconnected)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/room")
def room(limit: int = room_state.EVENT_LIMIT, project: str | None = None):
    """Phòng 3D (chỉ đọc): agent + trạng thái, task, event trao đổi (chỉ summary, không raw thinking).
    `project`: snapshot từ event store (+ seq để nối /api/room/stream); không có → board DASH như cũ."""
    lim = max(1, min(int(limit), FEED_LIMIT_MAX))
    if project:
        if not _known(project):
            return JSONResponse({"error": f"không có project {project!r} trong event store"}, 404)
        return room_stream.RoomFeed(_store(), project).snapshot(_room_levels(), limit=lim)
    cur = S.steps[S.i] if S.i < len(S.steps) else {}
    gate = {"id": cur["gate"], "agents": _gate_agents(S.i)} if cur.get("gate") else None
    active = [] if gate or not cur else _step_agents(cur)
    return {**room_state.snapshot(S.board, active=active, gate=gate, levels=_room_levels(), limit=lim),
            "usage": _dash_usage()}


def _dash_usage():
    """Token của board DASH: log bước demo (tier + tok in/out); đơn giá config/pricing.yaml."""
    u = usage.Usage()
    for e in S.log:
        u.add(e["agent"], e["tier"], e["tok"][0], e["tok"][1])
    return u.to_dict()


# ----- Gate dự án thật (event store): NGƯỜI duyệt / trả lại — lõi src/ops/gate.py -----
# Endpoint ghi event store: gate + escalation (dưới đây) và Control Room (src/server/control.py: tạo project / giao
# việc phụ, đều cần người gõ tên + XAC NHAN). Claude/bot không gọi (.claude/settings.json deny, luật 10).
GATE_STORE = None     # test thay bằng EventStore(tmp) ghi được; None → store theo config (ghi được)
GATE_FEEDBACK_LOG = None    # test thay; None → eval/runs/gate_feedback.jsonl
GATE_ERR_STATUS = {"machine": 400, "input": 400, "missing": 404, "state": 409, "hash": 409, "guard": 409}


def _gate_store():
    return GATE_STORE if GATE_STORE is not None else eventstore.EventStore(eventstore.default_path())


@app.get("/api/projects/{pid}/gate")
def project_gate(pid: str):
    """Gate đang chờ + dữ liệu trình gate (highlights, zone output, decision_records — không transcript /
    raw thinking) + packet_hash để gửi lại khi ký."""
    from src.ops import gate as ops_gate
    try:
        return ops_gate.view(_store(), pid)
    except ops_gate.GateError as e:
        return JSONResponse({"error": str(e)}, GATE_ERR_STATUS.get(e.kind, 400))


@app.post("/api/projects/{pid}/gate/{gid}/{decision}")
async def project_gate_decide(pid: str, gid: str, decision: str, request: Request):
    """Body JSON: signer (tên NGƯỜI, bắt buộc), typed_confirm == "XAC NHAN" (bắt buộc), packet_hash (khuyên
    gửi: dữ liệu đổi → 409), approve: edited, note · reject: reason (bắt buộc), backtrack_to."""
    from src.ops import gate as ops_gate
    if decision not in ("approve", "reject"):
        return JSONResponse({"error": "decision phải là approve | reject"}, 404)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "body phải là JSON object"}, 400)
    if body.get("typed_confirm") != ops_gate.CONFIRM_WORD:
        return JSONResponse({"error": f'phải gõ đúng "{ops_gate.CONFIRM_WORD}" vào typed_confirm'}, 400)
    if not str(body.get("signer") or "").strip():
        return JSONResponse({"error": "thiếu signer (tên người ký)"}, 400)
    kw = dict(store=_gate_store(), expected_hash=body.get("packet_hash") or None, autonomy=_autonomy(),
              feedback_log=GATE_FEEDBACK_LOG)
    try:
        if decision == "approve":
            res = ops_gate.sign(pid, gid, body["signer"], "dashboard", edited=bool(body.get("edited")),
                                note=body.get("note"), **kw)
        else:
            res = ops_gate.reject(pid, gid, body["signer"], "dashboard", body.get("reason"),
                                  body.get("backtrack_to") or None, **kw)
    except ops_gate.GateError as e:
        return JSONResponse({"error": str(e)}, GATE_ERR_STATUS.get(e.kind, 400))
    return {**res, "view": ops_gate.view(kw["store"], pid)}


@app.get("/api/projects/{pid}/escalation")
def project_escalation(pid: str):
    """Tình trạng ESCALATED (lý do, phase lỗi, agent, lựa chọn gỡ) + packet_hash để gửi lại khi gỡ."""
    from src.ops import escalation as ops_esc
    try:
        return ops_esc.view(_store(), pid)
    except ops_esc.EscalationError as e:
        return JSONResponse({"error": str(e)}, GATE_ERR_STATUS.get(e.kind, 400))


@app.post("/api/projects/{pid}/escalation/{action}")
async def project_escalation_resolve(pid: str, action: str, request: Request):
    """NGƯỜI gỡ ESCALATED (cùng chuẩn ký gate, luật 10). action: retry | backtrack:<PHASE> | abandon.
    Body JSON: signer (tên NGƯỜI, bắt buộc), typed_confirm == "XAC NHAN" (bắt buộc), note, packet_hash (khuyên
    gửi: tình trạng đổi → 409). UI chưa có nút (đợt UI bên công ty) — API dùng được ngay."""
    from src.ops import escalation as ops_esc
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"error": "body phải là JSON object"}, 400)
    if body.get("typed_confirm") != ops_esc.CONFIRM_WORD:
        return JSONResponse({"error": f'phải gõ đúng "{ops_esc.CONFIRM_WORD}" vào typed_confirm'}, 400)
    if not str(body.get("signer") or "").strip():
        return JSONResponse({"error": "thiếu signer (tên người quyết)"}, 400)
    store = _gate_store()
    try:
        res = ops_esc.resolve(pid, action, body["signer"], "dashboard", body.get("note"), store=store,
                              expected_hash=body.get("packet_hash") or None)
    except ops_esc.EscalationError as e:
        return JSONResponse({"error": str(e)}, GATE_ERR_STATUS.get(e.kind, 400))
    return {**res, "view": ops_esc.view(store, pid)}


@app.get("/room", response_class=HTMLResponse)
def room_page():
    return (HERE/"static"/"room.html").read_text(encoding="utf-8")


# ----- Control Room (trang /): chat CoS, giao việc, Phòng 3D tất cả dự án — src/server/control.py -----
from src.server import control  # noqa: E402  (import sau khi app + helper đã định nghĩa)

app.include_router(control.router)
