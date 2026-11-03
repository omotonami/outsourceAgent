"""d1_soc. Spec: specs/02_agents/d1_soc.md. Tool ngoài (repo/jira/figma/scan) còn stub."""
from src.agents import runtime

ID, ROLE, PHASE = "d1_soc", "soc", "security"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    if res.get("ok"):
        board.write("security_report", res["data"], agent=ID)
    return res
