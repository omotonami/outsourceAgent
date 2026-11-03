"""c1_dev. Spec: specs/02_agents/c1_dev.md. Tool ngoài (repo/jira/figma/scan) còn stub."""
from src.agents import runtime

ID, ROLE, PHASE = "c1_dev", "dev", "build"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    return res
