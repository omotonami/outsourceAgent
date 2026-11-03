"""A3 Proposal. Spec: specs/02_agents/a3_proposal.md. Reflection + Opus review cuối."""
from src.agents import runtime
from src.tools.checks.assumption_coverage import coverage

ID, ROLE, PHASE = "a3_proposal", "proposal", "presales"


def _post(data, inp, ctx):
    est = inp.get("estimate", {})
    prop = {"assumptions_section": data.get("assumptions_section", ""),
            "exclusions_section": data.get("exclusions_section", "")}
    cov = coverage(est, prop)
    data["assumption_coverage"] = cov
    data["needs_human"] = True  # proposal luôn qua G1
    return data


def run(board, user_id, ctx, inp, **kw):
    # Proposal: review cuối luôn Opus
    kw.setdefault("signals", set())
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, post=_post, **kw)
    if res.get("ok"):
        board.write("proposal", res["data"], agent=ID)
    return res
