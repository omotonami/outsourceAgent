"""e2_cos. Spec: specs/02_agents/e2_cos.md. Sinh bởi src/factory (H1); autonomy L0.
Không ghi Blackboard."""
from src.agents import runtime

ID, ROLE, PHASE = "e2_cos", "cos", "presales"


def run(board, user_id, ctx, inp, **kw):
    return runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, **kw)
