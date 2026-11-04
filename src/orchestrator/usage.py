"""Token đã đốt (Code thuần, chỉ số liệu) — tổng hợp từ trace gateway cho dashboard / Phòng 3D.

Nguồn: dòng `llm_usage` trong event store (src/gateway/trace.py ghi khi project có store) hoặc log bước
dashboard demo. Mỗi lần gọi model chỉ mang {agent, tier, model, phase, tokens_in, tokens_out, total} —
KHÔNG có prompt/messages/output.
Đơn giá ước tính: config/pricing.yaml (USD / 1M token theo tier). Budget: config/budgets.yaml
(per_project_tokens: tổng các phase có số; warn_at_ratio). Không có số → budget None.
"""
from __future__ import annotations

from src.common import config

PRICING = "config/pricing.yaml"
ZONE = "llm_usage"            # zone ảo trong event store (không phải zone Blackboard)
TOP_AGENTS = 8


def prices(root=None) -> dict:
    """tier → (USD/token in, USD/token out) từ config/pricing.yaml."""
    tab = config.require(config.load_yaml(PRICING, root), "per_million_tokens", PRICING)
    return {t: (float(v["in"]) / 1e6, float(v["out"]) / 1e6) for t, v in tab.items()}


def budget(root=None) -> dict | None:
    """{limit, warn_at_ratio} nếu budgets.yaml có trần token dự án; không thì None."""
    cfg = config.load_yaml(config.BUDGETS, root)
    lim = [v for v in (cfg.get("per_project_tokens") or {}).values() if isinstance(v, (int, float))]
    if not lim:
        return None
    return {"limit": int(sum(lim)), "warn_at_ratio": float(cfg.get("warn_at_ratio") or 0.8)}


def record(fields: dict) -> dict:
    """Trace gateway → bản ghi chỉ số liệu (whitelist field)."""
    u = fields.get("usage") or {}
    tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    total = int(u.get("total_tokens") or (tin + tout))
    return {"agent": fields.get("agent"), "tier": fields.get("tier"), "model": fields.get("model"),
            "phase": fields.get("phase"), "tokens_in": tin, "tokens_out": tout, "total": total}


class Usage:
    def __init__(self):
        self.tokens_in = self.tokens_out = self.total = self.calls = 0
        self.by_agent: dict[str, int] = {}
        self.by_tier: dict[str, dict] = {}

    def add(self, agent, tier, tokens_in=0, tokens_out=0, total=None) -> None:
        tin, tout = int(tokens_in or 0), int(tokens_out or 0)
        tot = int(total if total is not None else tin + tout)
        self.tokens_in += tin
        self.tokens_out += tout
        self.total += tot
        self.calls += 1
        a = agent or "?"
        self.by_agent[a] = self.by_agent.get(a, 0) + tot
        t = self.by_tier.setdefault(tier or "?", {"tokens_in": 0, "tokens_out": 0, "total": 0, "calls": 0})
        t["tokens_in"] += tin
        t["tokens_out"] += tout
        t["total"] += tot
        t["calls"] += 1

    def add_record(self, r: dict) -> None:
        self.add(r.get("agent"), r.get("tier"), r.get("tokens_in"), r.get("tokens_out"), r.get("total"))

    def to_dict(self, price: dict | None = None, bud: dict | None = ...) -> dict:
        price = prices() if price is None else price
        bud = budget() if bud is ... else bud
        cost = 0.0
        for tier, t in self.by_tier.items():
            pin, pout = price.get(tier, (0.0, 0.0))
            rest = max(0, t["total"] - t["tokens_in"] - t["tokens_out"])   # không rõ in/out → giá in
            cost += (t["tokens_in"] + rest) * pin + t["tokens_out"] * pout
        top = dict(sorted(self.by_agent.items(), key=lambda kv: -kv[1])[:TOP_AGENTS])
        out = {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "total": self.total,
               "calls": self.calls, "by_agent": top, "by_tier": self.by_tier,
               "est_cost_usd": round(cost, 6), "budget": None}
        if bud:
            ratio = self.total / bud["limit"] if bud["limit"] else 0.0
            out["budget"] = {**bud, "used": self.total, "ratio": round(ratio, 4)}
        return out


def step_cost(tier: str, tok, price: dict | None = None) -> float:
    """Chi phí ước tính (USD) của (tokens_in, tokens_out) theo tier."""
    pin, pout = (price or prices())[tier]
    return tok[0] * pin + tok[1] * pout
