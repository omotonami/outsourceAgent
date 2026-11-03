"""b2_ux. Spec: specs/02_agents/b2_ux.md. Tool ngoài (repo/jira/figma/scan) còn stub."""
from src.agents import runtime

ID, ROLE, PHASE = "b2_ux", "ux", "discovery"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    if res.get("ok"):
        board.write("design_spec", res["data"], agent=ID)
    return res
