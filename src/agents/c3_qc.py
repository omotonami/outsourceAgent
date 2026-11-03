"""c3_qc. Spec: specs/02_agents/c3_qc.md. Tool ngoài (repo/jira/figma/scan) còn stub."""
from src.agents import runtime

ID, ROLE, PHASE = "c3_qc", "qc", "build"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    return res
