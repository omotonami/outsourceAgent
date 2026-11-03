"""B2b UI Designer. Spec: specs/02_agents/b3_ui.md. Ra design system (tokens + components)."""
from src.agents import runtime

ID, ROLE, PHASE = "b3_ui", "ui", "discovery"


def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
    if res.get("ok"):
        board.write("ui_system", res["data"], agent=ID)
    return res
