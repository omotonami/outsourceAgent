"""Bước Clarify: xác nhận YÊU CẦU với người/client trước khi estimate.
Gộp câu hỏi (từ open_questions của BU), không hỏi effort. Chỉ khi trả lời xong mới cho estimate."""

def pending(board):
    """Câu hỏi chưa trả lời: ambiguities của BU + open_questions từ decision_records (H3 Huddle).
    Giữ thứ tự, bỏ trùng; API không đổi (list[str])."""
    bc = board.get("business_context") or {}
    answered = {a["q"] for a in (board.get("clarify_answers") or [])}
    qs = list(bc.get("ambiguities", []))
    for rec in board.get("decision_records") or []:
        qs += rec.get("open_questions") or []
    out = []
    for q in qs:
        if q not in answered and q not in out:
            out.append(q)
    return out

WRITER = "clarify"


def resolve(board, q, answer, agent=WRITER):
    """Ghi câu trả lời vào zone clarify_answers với danh tính cố định "clarify".
    `agent` chỉ giữ để tương thích chữ ký cũ: giá trị khác → PermissionDenied.
    Lỗi ghi (zone/quyền/proxy chỉ đọc) nổi lên nguyên vẹn — không có đường ghi vòng."""
    if agent != WRITER:
        from src.blackboard.store import PermissionDenied
        raise PermissionDenied(f"clarify.resolve chỉ ghi với danh tính {WRITER!r}, không phải {agent!r}")
    cur = board.get("clarify_answers") or []
    board.write("clarify_answers", cur + [{"q": q, "answer": answer}], agent=WRITER)

def done(board):
    return len(pending(board)) == 0


def suggest(board, ctx, sender=None):
    """AI đề xuất câu trả lời (RECOMMENDATION) cho các open_question → nháp đề án cho client.
    Người duyệt/sửa rồi resolve. Dùng A1 BU (gateway); `sender` để test offline."""
    import json
    from src.gateway import llm_client, router
    qs = pending(board)
    if not qs:
        return []
    bc = board.get("business_context") or {}
    system = ("Bạn là A1 BU tư vấn giải pháp (outsourcing SG). Với mỗi câu hỏi yêu cầu, đề xuất 1 câu "
              "trả lời KHUYẾN NGHỊ (RECOMMENDATION) làm nháp đề án cho client, kèm lý do ngắn. "
              'Trả CHỈ JSON: {"suggestions":[{"q":string,"answer":string,"why":string}]}')
    tier = router.pick_tier("a1_bu")
    text, _ = llm_client.call("a1_bu", tier, system,
                              [{"role": "user", "content": json.dumps({"flows": bc.get("flows", []), "questions": qs}, ensure_ascii=False)}],
                              ctx.get("project_id", "p"), "presales", sender=sender)
    data = json.loads(text)
    # gắn nhãn RECOMMENDATION — chưa phải quyết định, chờ người chốt
    return [{**s, "label": "RECOMMENDATION", "confirmed": False} for s in data.get("suggestions", [])]
