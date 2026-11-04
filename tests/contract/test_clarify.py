"""Clarify: phải trả lời open_questions về yêu cầu trước khi estimate."""
from src.blackboard.store import Blackboard
from src.agents import clarify

def test_clarify_gating():
    b = Blackboard("p")
    b.write("business_context", {"domain":"fintech","modules":[],
            "ambiguities":["Nhà cung cấp FX?","Ngân sách?"]}, agent="a1_bu")
    assert not clarify.done(b)
    assert len(clarify.pending(b)) == 2
    clarify.resolve(b, "Nhà cung cấp FX?", "Wise")
    clarify.resolve(b, "Ngân sách?", "500k SGD")
    assert clarify.done(b)


def test_clarify_suggest_recommendation():
    import json
    from src.agents import clarify
    b = Blackboard("p")
    b.write("business_context", {"domain":"fintech","flows":["KYC"],
            "ambiguities":["Nhà cung cấp FX?"]}, agent="a1_bu")
    fake = lambda model, system, messages, max_tokens: (
        json.dumps({"suggestions":[{"q":"Nhà cung cấp FX?","answer":"Đề xuất Wise Platform","why":"phí thấp"}]}),
        {"total_tokens":10})
    out = clarify.suggest(b, {"project_id":"p"}, sender=fake)
    assert out and out[0]["label"]=="RECOMMENDATION" and out[0]["confirmed"] is False
    assert "Wise" in out[0]["answer"]


# Review cuối #1: resolve() không còn fallback ghi thẳng board._data; danh tính cố định "clarify"
def test_clarify_resolve_rejects_foreign_agent_and_no_fallback():
    import pytest
    from src.blackboard.store import PermissionDenied
    from src.blackboard.proxy import ReadOnlyBoard
    b = Blackboard("p")
    with pytest.raises(PermissionDenied):
        clarify.resolve(b, "q-x", {"anything": ["not", "a", "string"]}, agent="c1_dev")
    assert b.get("clarify_answers") is None
    # qua proxy chỉ đọc: lỗi nổi lên, không có đường vòng
    with pytest.raises(PermissionDenied):
        clarify.resolve(ReadOnlyBoard(b), "q", "a")
    assert b.get("clarify_answers") is None
    clarify.resolve(b, "q", "a", agent="clarify")      # tương thích tham số cũ
    assert b.get("clarify_answers") == [{"q": "q", "answer": "a"}]
