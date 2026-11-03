"""e2_cos. Spec: specs/02_agents/e2_cos.md. Sinh bởi src/factory (H1); autonomy L0.
Không ghi Blackboard.

H5 Chief of Staff — một cửa cho người:
- ask(): đọc project_status + risk_register + decision_records + trace_log (qua Team feed, chỉ summary)
  qua CosBoard read-only → LLM (runtime, gateway) → {understanding, clarifications, plan, assignments,
  decisions_needed}. Code kiểm assignments trỏ tới agent có spec (không gate_ui, không chính cos).
- confirm() + submit(): CHỈ 2 loại yêu cầu thay người, chỉ khi người xác nhận (confirmed_by là người,
  hash payload khớp — cơ chế chung src/governance/confirm.py):
    lead_brief      → ghi với danh tính "intake" (writer hiện có, schema lead_brief) — chỉ khi CHƯA có
    clarify_answers → clarify.resolve (câu hỏi phải đang pending)
  Mọi thứ khác (ký/thu hồi gate, zone sản phẩm, risk, trace) → PermissionDenied.
"""
from pathlib import Path

from jsonschema import validate

from src.agents import clarify, runtime
from src.blackboard.proxy import ReadOnlyBoard
from src.blackboard.store import PermissionDenied, _schema as _zone_schema
from src.governance import confirm as human
from src.orchestrator import team_feed

ID, ROLE, PHASE = "e2_cos", "cos", "presales"
ROOT = Path(__file__).parents[2]
REQUEST_KINDS = ("lead_brief", "clarify_answers")
TRACE_LIMIT = 20                       # số event Team feed đưa vào input (summary, không raw)
FORBIDDEN_ASSIGNEES = {"gate_ui", ID, "cos"}


class CosBoard(ReadOnlyBoard):
    """Board proxy cho cos: CHỈ đọc (src/blackboard/proxy.py)."""
    __slots__ = ()

    def __init__(self, board):
        super().__init__(board, owner=ID)


def run(board, user_id, ctx, inp, **kw):
    return runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)


def _agent_ids():
    return {p.stem for p in (ROOT / "specs/02_agents").glob("*.md")}


def gather(board, request):
    """Input cho LLM — chỉ đọc; trace_log đi qua Team feed (summary, không raw)."""
    rb = CosBoard(board)
    inp = {"request": request}
    if (ps := rb.get("project_status")) is not None:
        inp["project_status"] = ps
    risks = [r for r in rb.get("risk_register") or []
             if str(r.get("status") or "open").lower() not in ("closed", "resolved")]
    if risks:
        inp["risk_register"] = risks
    if (dr := rb.get("decision_records")):
        inp["decision_records"] = dr
    feed = team_feed.build(rb, limit=TRACE_LIMIT)["events"]
    if feed:
        inp["trace_summary"] = feed
    return inp


def check_output(data):
    """Forbidden do Code kiểm. Trả list lỗi (rỗng = ok)."""
    errs, known = [], _agent_ids()
    for i, a in enumerate(data.get("assignments") or []):
        who = str((a or {}).get("agent") or "")
        if who in FORBIDDEN_ASSIGNEES:
            errs.append(f"assignments[{i}]: không được giao cho {who!r}")
        elif who not in known:
            errs.append(f"assignments[{i}]: agent {who!r} không có spec")
    return errs


def ask(board, request, ctx, user_id=None, sender=None):
    """Người hỏi CoS → khuôn 5 mục. KHÔNG ghi Blackboard (board là CosBoard)."""
    res = run(CosBoard(board), user_id, {"phase": PHASE, **ctx}, gather(board, request), sender=sender)
    if not res.get("ok"):
        return res
    errs = check_output(res["data"])
    if errs:
        return {"error": "FORBIDDEN", "reason": "; ".join(errs), "escalate": True, "tier": res.get("tier")}
    return res


# ---------- 2 loại yêu cầu thay người, có xác nhận ----------
def confirm(kind, payload, *, confirmed_by):
    """Người xác nhận yêu cầu cos soạn → bản ghi cho submit(). confirmed_by phải là người."""
    if kind not in REQUEST_KINDS:
        raise PermissionDenied(f"{ID} không được tạo yêu cầu loại {kind!r} (chỉ {REQUEST_KINDS})")
    who = human.require_human(confirmed_by, PermissionDenied, "confirmed_by phải là người, không phải {who!r}")
    return {"kind": kind, "payload": payload, "confirmed": True, "confirmed_by": who,
            "payload_hash": human.payload_hash({"kind": kind, "payload": payload})}


def _verified(record):
    if not isinstance(record, dict) or record.get("confirmed") is not True:
        raise PermissionDenied("yêu cầu chưa được người xác nhận (dùng e2_cos.confirm)")
    kind = record.get("kind")
    if kind not in REQUEST_KINDS:
        raise PermissionDenied(f"{ID} không được tạo yêu cầu loại {kind!r}")
    human.require_human(record.get("confirmed_by"), PermissionDenied, "confirmed_by không hợp lệ: {who!r}")
    if record.get("payload_hash") != human.payload_hash({"kind": kind, "payload": record.get("payload")}):
        raise PermissionDenied("payload bị sửa sau khi xác nhận (hash không khớp)")
    return kind, record["payload"]


def submit(board, record):
    """Thực thi yêu cầu đã xác nhận. Trả {ok, kind, confirmed_by}."""
    kind, p = _verified(record)
    if kind == "lead_brief":
        if board.get("lead_brief") is not None:
            raise PermissionDenied("lead_brief đã có — nguồn gốc, không sửa sau khi tạo")
        if (s := _zone_schema("lead_brief")):
            validate(p, s)
        board.write("lead_brief", {**p, "requested_via": ID, "confirmed_by": record["confirmed_by"]},
                    agent="intake")
    else:
        q, ans = (p or {}).get("q"), (p or {}).get("answer")
        if not q or ans is None:
            raise ValueError("clarify_answers cần {q, answer}")
        if q not in clarify.pending(board):
            raise PermissionDenied(f"câu hỏi không nằm trong clarify.pending: {q!r}")
        clarify.resolve(board, q, ans)
    return {"ok": True, "kind": kind, "confirmed_by": record["confirmed_by"]}
