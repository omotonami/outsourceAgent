"""cost_range từ phân phối sai số của dự án tương tự (actual/estimate)."""
import statistics


def cost_range(base_cost: float, ratios: list[float]) -> dict:
    if len(ratios) < 3:
        return {"low": base_cost * 0.8, "base": base_cost, "high": base_cost * 1.4,
                "label": "ASSUMPTION", "reason": "ít hơn 3 dự án tương tự"}
    q = statistics.quantiles(ratios, n=10)
    return {"low": round(base_cost * q[0], 2), "base": round(base_cost * statistics.median(ratios), 2),
            "high": round(base_cost * q[-1], 2), "label": "FACT", "source": f"{len(ratios)} dự án tương tự"}
