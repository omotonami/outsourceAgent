"""Control Room — API cho trang `/` (đợt 2026-11-04): Phòng 3D "Tất cả dự án", chat CoS trong app, giao việc.

Quyền (luật 10, specs/02_agents/e2_cos.md):
- /api/cos/chat, /api/cos/brief: CHỈ gọi e2_cos.ask (CosBoard read-only) → khuôn 5 mục. Không ký gate, không gỡ
  ESCALATED, không ghi zone sản phẩm — không có code path nào làm việc đó ở đây (test: test_control_room.py).
- Tạo project: /api/cos/brief/confirm — NGƯỜI gõ tên (confirmed_by) + "XAC NHAN" → e2_cos.confirm + submit (ghi
  lead_brief bằng writer intake). Thiếu/sai → 400; bản xem trước bị sửa (hash) → 409.
- Giao việc phụ: POST /api/projects/{id}/side-tasks — người gõ tên + "XAC NHAN" → zone side_tasks (queued);
  scheduler chạy (agent chỉ đọc, ra note).
- Rate-limit đơn giản cho chat/brief: config/scheduler.yaml → cos_chat.rate_limit_per_min (toàn server).
- MOCK=1 → sender mock (không gọi mạng); không thì gateway thật (máy đã đăng nhập Claude Code — lệnh `claude`).
  Model không gọi được → thông báo chung NOT_READY (không lộ chi tiết lỗi/khoá).
Gate / Sự cố dùng endpoint sẵn có trong app.py (/api/projects/{id}/gate…, /escalation…).
"""
from __future__ import annotations

import collections
import os
import re
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema import ValidationError, validate

from src.agents import e2_cos
from src.blackboard import eventstore
from src.blackboard.store import Blackboard, PermissionDenied
from src.common import config
from src.governance import confirm
from src.orchestrator import room_stream
from src.orchestrator import status as pstatus

router = APIRouter()
CONFIRM_WORD = "XAC NHAN"
CONTROL_PID = "_control"            # project ảo cho chat CoS không gắn dự án (trace không vào store)
BRIEF_SCHEMA = "src/blackboard/schema/lead_brief.json"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FILE_EXT = (".md", ".txt", ".markdown")
COS_SENDER = None                    # test thay; None → MOCK=1 ? mock.sender : gateway thật
eventstore.register(CONTROL_PID, None)


def _cfg() -> dict:
    return config.section(config.load_yaml("config/scheduler.yaml"), "cos_chat", "config/scheduler.yaml")


class RateLimit:
    """Cửa sổ trượt 60s, toàn server (một người dùng nội bộ)."""

    def __init__(self):
        self.hits: collections.deque = collections.deque()
        self.lock = threading.Lock()

    def allow(self, limit: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            while self.hits and now - self.hits[0] > 60:
                self.hits.popleft()
            if len(self.hits) >= limit:
                return False
            self.hits.append(now)
            return True

    def reset(self) -> None:
        with self.lock:
            self.hits.clear()


RATE = RateLimit()


def _srv():
    from src.server import app as srv
    return srv


def _sender():
    if COS_SENDER is not None:
        return COS_SENDER
    if os.environ.get("MOCK") == "1":
        from src.ops import mock
        return mock.sender
    return None


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, code)


async def _body(request: Request):
    try:
        b = await request.json()
    except ValueError:
        return None
    return b if isinstance(b, dict) else None


def _limited():
    if not RATE.allow(int(_cfg()["rate_limit_per_min"])):
        return _err("quá nhiều yêu cầu CoS trong 1 phút — thử lại sau", 429)
    return None


def _overview() -> str:
    """Tình hình các dự án (Code tổng hợp, chỉ số liệu) — nối vào request khi chat không gắn dự án."""
    mf = room_stream.MultiFeed(_srv()._store())
    rows = [f"{p['id']}: {p['state']}" + (f" (chờ {p['gate']})" if p["gate"] else "")
            + (f", {p['side_open']} việc phụ mở" if p["side_open"] else "") for p in mf._view()["projects"]]
    return "; ".join(rows) or "(chưa có dự án)"


def _ask(request_text: str, pid: str | None) -> dict:
    srv = _srv()
    if pid:
        board = Blackboard.load(pid, srv._store())
        eventstore.register(pid, srv._gate_store())      # token chat vào số liệu của dự án (llm_usage)
        ctx_pid = pid
    else:
        board = Blackboard(CONTROL_PID, store=None)
        request_text += f"\n\n[Tình hình các dự án — Code tổng hợp, chỉ đọc]: {_overview()}"
        ctx_pid = CONTROL_PID
    return e2_cos.ask(board, request_text, {"project_id": ctx_pid}, user_id="control-room", sender=_sender())


NOT_READY = "Claude Code chưa sẵn sàng — chạy `claude` và đăng nhập"
MODEL_ERRORS = ("SCHEMA_FAIL", "FORBIDDEN", "INVALID_INPUT")   # lỗi nội dung (hiện nguyên); còn lại = không gọi được


def _cos_reply(res: dict) -> dict:
    if res.get("ok"):
        d = res["data"]
        return {"ok": True, "reply": {k: d.get(k) for k in ("understanding", "clarifications", "plan",
                                                             "assignments", "decisions_needed")}}
    return {"ok": False, "error": res.get("error"), "reason": res.get("reason")}


def _not_ready(e: BaseException) -> dict:
    import logging
    logging.getLogger(__name__).warning("CoS không gọi được model: %s: %s", type(e).__name__, e)
    return {"ok": False, "error": "NOT_READY", "reason": NOT_READY}


@router.post("/api/cos/chat")
async def cos_chat(request: Request):
    """{message, project?} → khuôn 5 mục của CoS (Hiểu việc · Cần làm rõ · Kế hoạch · Phân công · Cần quyết định)."""
    body = await _body(request)
    if body is None:
        return _err("body phải là JSON object")
    msg = str(body.get("message") or "").strip()[: int(_cfg()["max_message_chars"])]
    if not msg:
        return _err("thiếu message")
    pid = body.get("project") or None
    if pid and not _srv()._known(pid):
        return _err(f"không có project {pid!r}", 404)
    if (r := _limited()) is not None:
        return r
    try:
        reply = _cos_reply(_ask(msg, pid))
    except Exception as e:  # noqa: BLE001 — Claude Code chưa đăng nhập / mạng: báo chung, không sập server
        return JSONResponse({"project": pid, **_not_ready(e), "error_message": NOT_READY}, 503)
    return {"project": pid, **reply}


def _draft(body: dict) -> dict:
    lim = int(_cfg()["max_brief_chars"])
    content = str(body.get("content") or "").strip()
    fname = str(body.get("file_name") or "").strip()
    ftext = str(body.get("file_text") or "")
    if fname and not fname.lower().endswith(FILE_EXT):
        raise ValueError(f"file brief chỉ nhận {FILE_EXT}")
    desc = "\n\n".join(x for x in (content, ftext.strip()) if x)[:lim]
    if not desc:
        raise ValueError("thiếu nội dung brief (gõ hoặc tải file .md/.txt)")
    first = next((ln.strip(" #") for ln in desc.splitlines() if ln.strip(" #")), "")
    draft = {"client": str(body.get("client") or "").strip(), "source": str(body.get("source") or "").strip(),
             "received_at": str(body.get("received_at") or "").strip() or time.strftime("%Y-%m-%d"),
             "summary": first[:200], "description": desc}
    if fname:
        draft["attachments"] = [fname[:120]]
    if not draft["client"] or not draft["source"]:
        raise ValueError("thiếu client hoặc nguồn (source)")
    validate(draft, config.load_json(BRIEF_SCHEMA))
    return draft


def _hash(draft: dict) -> str:
    return confirm.payload_hash({"kind": "lead_brief", "payload": draft})


@router.post("/api/cos/brief")
async def cos_brief(request: Request):
    """Form "Giao việc" → CoS soạn lead_brief (bản XEM TRƯỚC, chưa ghi gì) + khuôn 5 mục + payload_hash."""
    body = await _body(request)
    if body is None:
        return _err("body phải là JSON object")
    try:
        draft = _draft(body)
    except (ValueError, ValidationError) as e:
        return _err(getattr(e, "message", str(e)))
    if (r := _limited()) is not None:
        return r
    req = (f"Brief mới cần nhận việc — client: {draft['client']} · nguồn: {draft['source']} · ngày "
           f"{draft['received_at']}. Nội dung: {draft['description'][:1500]}")
    try:
        cos = _cos_reply(_ask(req, None))
    except Exception as e:  # noqa: BLE001 — bản xem trước vẫn dùng được, chỉ thiếu phần CoS
        cos = _not_ready(e)
    from src.ops import common
    return {"draft": draft, "payload_hash": _hash(draft), "cos": cos, "suggested_id": common.new_id(),
            "confirm_word": CONFIRM_WORD}


@router.post("/api/cos/brief/confirm")
async def cos_brief_confirm(request: Request):
    """NGƯỜI xác nhận bản xem trước: {draft, payload_hash, confirmed_by, typed_confirm: "XAC NHAN", id?} → project mới
    (INTAKE) qua e2_cos.confirm + submit. Scheduler chạy tiếp tới G1."""
    body = await _body(request)
    if body is None:
        return _err("body phải là JSON object")
    if body.get("typed_confirm") != CONFIRM_WORD:
        return _err(f'phải gõ đúng "{CONFIRM_WORD}" vào typed_confirm')
    who = str(body.get("confirmed_by") or "").strip()
    if not who:
        return _err("thiếu confirmed_by (tên người xác nhận)")
    draft = body.get("draft")
    if not isinstance(draft, dict):
        return _err("thiếu draft (bản xem trước từ /api/cos/brief)")
    try:
        record = e2_cos.confirm("lead_brief", draft, confirmed_by=who)
    except PermissionDenied as e:
        return _err(str(e))
    if body.get("payload_hash") != record["payload_hash"]:
        return _err("bản xem trước đã bị sửa sau khi người xem (hash không khớp) — soạn lại", 409)
    from src.ops import common
    pid = str(body.get("id") or "").strip() or common.new_id()
    if not _ID.match(pid):
        return _err(f"id {pid!r}: chỉ chữ/số/._- (≤64)")
    store = _srv()._gate_store()
    if common.exists(store, pid):
        return _err(f"project {pid} đã có", 409)
    b = Blackboard(pid, store=store)
    try:
        res = e2_cos.submit(b, record)
    except (PermissionDenied, ValidationError) as e:
        return _err(getattr(e, "message", str(e)))
    pstatus.update(b, "INTAKE")
    return {"project": pid, "state": "INTAKE", **res,
            "next": "Scheduler (python -m src.ops.scheduler run) chạy tiếp tới G1; không có scheduler: "
                    f"python -m src.ops.project run --id {pid}"}


@router.get("/api/projects/{pid}/side-tasks")
def side_task_list(pid: str):
    from src.ops import side_tasks
    srv = _srv()
    if not srv._known(pid):
        return _err(f"không có project {pid!r}", 404)
    return {"project": pid, "tasks": side_tasks.tasks(Blackboard.load(pid, srv._store()))}


@router.post("/api/projects/{pid}/side-tasks")
async def side_task_add(pid: str, request: Request):
    """NGƯỜI giao việc phụ cho role: {role, title, detail?, kind: side|main, confirmed_by, typed_confirm}."""
    from src.ops import common, side_tasks
    body = await _body(request)
    if body is None:
        return _err("body phải là JSON object")
    if body.get("typed_confirm") != CONFIRM_WORD:
        return _err(f'phải gõ đúng "{CONFIRM_WORD}" vào typed_confirm')
    raw = str(body.get("confirmed_by") or "").strip()
    if not raw or not confirm.is_human(raw):
        return _err(f"confirmed_by {raw!r} không phải người")
    store = _srv()._gate_store()
    if not common.exists(store, pid):
        return _err(f"không có project {pid!r}", 404)
    b = common.resume(store, pid)
    if common.state_of(b) == "ABANDONED":
        return _err(f"project {pid} đã ABANDONED", 409)
    try:
        t = side_tasks.add(b, str(body.get("role") or ""), str(body.get("title") or ""),
                           detail=str(body.get("detail") or ""), kind=str(body.get("kind") or "side"),
                           via="dashboard", confirmed_by=raw)
    except side_tasks.TaskError as e:
        return _err(str(e))
    return {"project": pid, "task": t}


# ---------- Phòng 3D "Tất cả dự án" ----------
@router.get("/api/room/all")
def room_all(limit: int = 200):
    srv = _srv()
    return room_stream.MultiFeed(srv._store()).snapshot(srv._room_levels(), limit=max(1, min(int(limit), 500)))


@router.get("/api/room/all/stream")
async def room_all_stream(request: Request, since: int | None = None):
    """SSE như /api/room/stream nhưng gộp mọi project (id = seq toàn cục, Last-Event-ID nối lại)."""
    srv = _srv()
    hdr = request.headers.get("last-event-id")
    if hdr and hdr.strip().isdigit():
        since = int(hdr)
    feed = room_stream.MultiFeed(srv._store(), since=since)
    gen = room_stream.stream(feed, poll_s=float(srv._rt("poll_ms")) / 1000, heartbeat_s=float(srv._rt("heartbeat_s")),
                             levels=srv._room_levels(), initial=since is None, is_disconnected=request.is_disconnected)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
