"""b4_tech_lead. Spec: specs/02_agents/b4_tech_lead.md. Tool ngoài (repo/jira/figma/scan) còn stub."""
from src.agents import runtime

ID, ROLE, PHASE = "b4_tech_lead", "tech_lead", "discovery"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    return res
