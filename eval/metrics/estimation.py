def error_pct(estimate_md: float, actual_md: float) -> float:
    return (estimate_md - actual_md) / actual_md * 100


def within_band(estimate_md: float, actual_md: float, band: float = 20) -> bool:
    return abs(error_pct(estimate_md, actual_md)) <= band
