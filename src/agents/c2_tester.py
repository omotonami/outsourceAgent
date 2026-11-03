"""c2_tester. Spec: specs/02_agents/c2_tester.md. Tool ngoài (repo/jira/figma/scan) còn stub."""
from src.agents import runtime

ID, ROLE, PHASE = "c2_tester", "tester", "build"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    return res
