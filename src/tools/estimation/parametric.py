"""Ước parametric khi chưa có historical data. S/M/L/XL -> khoảng MD (có hệ số domain).
Nâng cấp FP: fp_to_size() đổi số Function Point thành size rồi dùng chung bảng."""
from pathlib import Path
import yaml

MODEL = Path(__file__).parents[3] / "data/sizing_model.yaml"
_M = None
def _model():
    global _M
    if _M is None: _M = yaml.safe_load(MODEL.read_text(encoding="utf-8"))
    return _M

def size_to_md(size, domain="default"):
    m = _model()
    band = m["sizes"].get(size)
    if not band:
        return None
    mult = m["domain_multiplier"].get(domain, m["domain_multiplier"]["default"])
    return {k: round(v * mult, 1) for k, v in band.items()}   # {low,base,high}

def fp_to_size(fp):
    for lo, hi, s in _model()["fp_to_size"]:
        if lo <= fp <= hi:
            return s
    return "XL"
