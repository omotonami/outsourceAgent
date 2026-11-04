"""factory_interviewer. Spec: specs/02_agents/factory_interviewer.md. Sinh bởi src/factory (H1); autonomy L0.
Không ghi Blackboard. Prompt lấy từ spec, chạy qua runtime.run (gateway + routing entry riêng,
không fallback tier âm thầm). Kết quả chỉ là NHÁP (RECOMMENDATION); người xác nhận ở
src/factory/interview.confirm."""
from src.agents import runtime

ID, ROLE, PHASE = "factory_interviewer", "interviewer", "factory"


def run(board, user_id, ctx, inp, **kw):
    return runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)


def draft(idea, questions, enums, sender=None, project_id="factory", user_id=None):
    """Nháp answers cho bộ câu hỏi. Lỗi input/schema → ValueError (không đoán thay)."""
    inp = {"idea": idea, "questions": [dict(q) for q in questions], "enums": dict(enums)}
    res = run(None, user_id, {"project_id": project_id}, inp, sender=sender)
    if not res.get("ok"):
        raise ValueError(f"{ID}: {res.get('error')}: {res.get('reason')}")
    data = dict(res["data"])
    data.pop("reflection", None)
    return data
