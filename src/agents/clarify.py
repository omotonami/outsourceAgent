"""Bước Clarify: xác nhận YÊU CẦU với người/client trước khi estimate.
Gộp câu hỏi (từ open_questions của BU), không hỏi effort. Chỉ khi trả lời xong mới cho estimate."""

def pending(board):
    bc = board.get("business_context") or {}
    answered = {a["q"] for a in (board.get("clarify_answers") or [])}
    return [q for q in bc.get("ambiguities", []) if q not in answered]

def resolve(board, q, answer, agent="clarify"):
    cur = board.get("clarify_answers") or []
    try:
        board.write("clarify_answers", cur + [{"q": q, "answer": answer}], agent=agent)
    except Exception:
        board._data.setdefault("clarify_answers", []).append({"q": q, "answer": answer})

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
