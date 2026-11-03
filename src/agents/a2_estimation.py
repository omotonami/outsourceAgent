"""A2 Estimation. LLM chỉ ra function list + size (S/M/L/XL); KHÔNG đặt số.
Code: historical match -> FACT; nếu không -> parametric từ size -> ASSUMPTION (không hỏi người từng dòng)."""
from src.agents import runtime
from src.store import historical
from src.tools.estimation import variance_model, parametric

ID, ROLE, PHASE = "a2_estimation", "estimation", "presales"


def enrich(draft, ctx):
    exclude = set(ctx.get("exclude_project_ids", []))
    domain = ctx.get("domain", "default")
    rate = ctx.get("blended_rate", 0)
    base = ctx.get("hist_base")
    wbs = []
    for d in draft["wbs"]:
        look = historical.lookup(d["item"], domain, ctx.get("tech_stack", []), exclude, base=base)
        if look["effort_md"] is not None:
            wbs.append({**d, "effort_md": look["effort_md"], "label": "FACT",
                        "source": {"type": "historical", "refs": [m["project_id"] for m in look["matches"]]},
                        "basis": f"median {look['n']} dự án tương tự"})
        else:
            band = parametric.size_to_md(d.get("size", "M"), domain)
            md = band["base"] if band else None
            wbs.append({**d, "effort_md": md, "label": "ASSUMPTION",
                        "source": {"type": "none", "refs": []},
                        "basis": f"parametric size {d.get('size','M')} (domain {domain})"})
    total = round(sum(i["effort_md"] for i in wbs if i["effort_md"] is not None), 1)
    ratios = historical.variance_ratios(domain, exclude, base=base)
    cost = variance_model.cost_range(total * rate, ratios)
    n_assume = sum(1 for i in wbs if i["label"] == "ASSUMPTION")
    return {
        "wbs": wbs, "effort_md_total": total, "cost_range": cost,
        "assumptions": draft.get("assumptions", []),
        "exclusions": draft.get("exclusions", []),
        "risks": draft.get("risks", []),
        "reflection": draft.get("reflection", {"passed": True, "issues": []}),
        # KHÔNG hỏi người từng dòng; chỉ gộp 1 lần ở G1
        "review_summary": f"{n_assume}/{len(wbs)} hạng mục là parametric (ASSUMPTION) — xem 1 lần ở G1",
        "needs_human": n_assume > 0,
    }


def _post(data, inp, ctx): return enrich(data, ctx)

def run(board, user_id, ctx, inp, **kw):
    res = runtime.run(ID, ROLE, PHASE, board, user_id, ctx, inp, post=_post, **kw)
    if res.get("ok"): board.write("estimate", res["data"], agent=ID)
    return res
