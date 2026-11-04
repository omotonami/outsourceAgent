"""H5 Chief of Staff (e2_cos): chỉ đọc + 2 loại yêu cầu có xác nhận người. Offline (sender mock)."""
import json

import pytest

from src.agents import e2_cos as cos
from src.blackboard.store import Blackboard, PermissionDenied

OUT = {"understanding": "Khách muốn thêm module KYC", "clarifications": ["FX provider nào?"],
       "plan": ["B1 viết story KYC"], "assignments": [{"agent": "b1_ba", "task": "story KYC"}],
       "decisions_needed": ["Duyệt thêm scope KYC?"]}


def _fake(data, seen=None):
    def send(model, system, messages, max_tokens):
        if seen is not None:
            seen.append(json.loads(messages[0]["content"]))
        return json.dumps(data), {"total_tokens": 10}
    return send


def _board():
    b = Blackboard("p")
    b.write("business_context", {"ambiguities": ["FX provider nào?"]}, agent="a1_bu")
    b.write("project_status", {"phase": "presales", "tasks_done": 0, "tasks_total": 1}, agent="orchestrator")
    b.append("risk_register", {"risk": "FX chưa rõ", "severity": "High", "status": "open"}, agent="a1_bu")
    b.append("risk_register", {"risk": "cũ", "severity": "High", "status": "closed"}, agent="a1_bu")
    b.append("trace_log", {"event": "SPAWN", "task_id": "T1", "ts": 1.0, "by": "orchestrator.dispatcher",
                           "thinking": "RAW-SECRET"}, agent="orchestrator")
    return b


def test_ask_returns_5_part_template_and_reads_only():
    b, seen = _board(), []
    before = json.dumps(b._data, sort_keys=True, default=str)
    res = cos.ask(b, "Tình hình dự án?", {"project_id": "p"}, sender=_fake(OUT, seen))
    assert res.get("ok"), res
    assert set(OUT) <= set(res["data"])
    assert json.dumps(b._data, sort_keys=True, default=str) == before          # không ghi gì
    inp = seen[0]
    assert inp["project_status"]["phase"] == "presales"
    assert [r["risk"] for r in inp["risk_register"]] == ["FX chưa rõ"]           # chỉ risk mở
    assert inp["trace_summary"][0]["type"] == "SPAWN"
    assert "RAW-SECRET" not in json.dumps(inp)                                  # không raw thinking


@pytest.mark.parametrize("who", ["gate_ui", "e2_cos", "x9_ghost"])
def test_ask_rejects_bad_assignee(who):
    bad = {**OUT, "assignments": [{"agent": who, "task": "ký G1"}]}
    res = cos.ask(_board(), "ký giúp G1", {"project_id": "p"}, sender=_fake(bad))
    assert res["error"] == "FORBIDDEN"


def test_cos_cannot_sign_or_revoke_gate():
    b = _board()
    with pytest.raises(PermissionDenied):
        cos.CosBoard(b).sign("G1", by="human", via="gate_ui")
    with pytest.raises(PermissionDenied):
        cos.CosBoard(b).revoke_signature("G1", "x")
    with pytest.raises(PermissionDenied):                     # Blackboard thật: e2_cos là danh tính máy
        b.sign("G1", by="e2_cos", via="gate_ui")
    assert not b.is_signed("G1")


def test_cos_cannot_write_product_zone():
    b = _board()
    with pytest.raises(PermissionDenied):
        b.write("requirements", {"stories": []}, agent="e2_cos")
    with pytest.raises(PermissionDenied):
        cos.CosBoard(b).write("requirements", {"stories": []})
    with pytest.raises(PermissionDenied):
        cos.CosBoard(b).append("risk_register", {"risk": "x"})
    for kind in ("requirements", "gate_signatures", "estimate"):
        with pytest.raises(PermissionDenied):
            cos.confirm(kind, {}, confirmed_by="daipc")
    assert b.get("requirements") is None


def test_clarify_answers_requires_human_confirmation():
    b = _board()
    payload = {"q": "FX provider nào?", "answer": "Wise"}
    with pytest.raises(PermissionDenied):                     # không xác nhận
        cos.submit(b, {"kind": "clarify_answers", "payload": payload})
    with pytest.raises(PermissionDenied):                     # máy tự xác nhận
        cos.confirm("clarify_answers", payload, confirmed_by="e2_cos")
    with pytest.raises(PermissionDenied):
        cos.confirm("clarify_answers", payload, confirmed_by="auto-L2")
    rec = cos.confirm("clarify_answers", payload, confirmed_by="daipc")
    tampered = {**rec, "payload": {**payload, "answer": "Stripe"}}
    with pytest.raises(PermissionDenied):                     # sửa sau xác nhận
        cos.submit(b, tampered)
    assert b.get("clarify_answers") is None
    assert cos.submit(b, rec)["ok"]
    assert b.get("clarify_answers") == [{"q": "FX provider nào?", "answer": "Wise"}]
    with pytest.raises(PermissionDenied):                     # câu hỏi không pending (đã trả lời)
        cos.submit(b, rec)


def test_lead_brief_new_only_via_intake_with_confirmation():
    b = Blackboard("p")
    brief = {"client": "NeoPay", "source": "chat", "received_at": "2026-11-03"}
    with pytest.raises(PermissionDenied):
        cos.submit(b, {"kind": "lead_brief", "payload": brief, "confirmed": False})
    rec = cos.confirm("lead_brief", brief, confirmed_by="daipc")
    assert cos.submit(b, rec)["ok"]
    lb = b.get("lead_brief")
    assert lb["client"] == "NeoPay" and lb["confirmed_by"] == "daipc" and lb["requested_via"] == "e2_cos"
    assert b._history[-1][1] == "intake"                      # đi qua writer intake hiện có
    with pytest.raises(PermissionDenied):                     # lead_brief là nguồn gốc: không ghi đè
        cos.submit(b, cos.confirm("lead_brief", {**brief, "client": "X"}, confirmed_by="daipc"))
    with pytest.raises(Exception):                            # schema lead_brief vẫn áp dụng
        cos.submit(Blackboard("q"), cos.confirm("lead_brief", {"client": "x"}, confirmed_by="daipc"))
