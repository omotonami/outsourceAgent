"""A1 Business-Understanding. Spec: specs/02_agents/a1_bu.md"""
from src.agents import runtime

ID, ROLE, PHASE = "a1_bu", "bu", "presales"


def _post(data, inp, ctx):
    return data  # BU chủ yếu định tính


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, post=_post, **kw)
    if res.get("ok"):
        board.write("business_context", res["data"], agent=ID)
        for q in res["data"].get("ambiguities", []):
            board.append("risk_register", {"risk": q, "severity": "Medium", "status": "open",
                                           "phase": PHASE}, agent=ID)
    return res
