"""b1_ba. Spec: specs/02_agents/b1_ba.md. Tool ngoài (repo/jira/figma/scan) còn stub."""
from src.agents import runtime

ID, ROLE, PHASE = "b1_ba", "ba", "discovery"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    if res.get("ok"):
        board.write("requirements", res["data"], agent=ID)
    return res
