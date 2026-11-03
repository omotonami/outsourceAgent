"""Guard cho từng transition. Trả (ok, reason)."""
from src.tools.checks.ac_lint import ac_pass_rate
from src.tools.checks.assumption_coverage import coverage


def _signed(board, gate):
    return (board.is_signed(gate), f"thiếu chữ ký {gate}")


def lead_brief_valid(board):
    return (board.get("lead_brief") is not None, "chưa có lead_brief")


def presales_ready(board):
    est, prop = board.get("estimate"), board.get("proposal")
    if not est or not prop:
        return False, "thiếu estimate hoặc proposal"
    cov = coverage(est, prop)
    return (cov["ratio"] == 1.0, f"assumption chưa vào proposal: {cov['missing']}")


def g1_signed(board):
    return _signed(board, "G1")


def discovery_ready(board):
    stories = (board.get("requirements") or {}).get("stories", [])
    rate = ac_pass_rate(stories)
    return (rate >= 0.95, f"AC pass rate {rate:.0%} < 95%")


def g2_signed(board):
    return _signed(board, "G2")


def build_ready(board):
    br = board.get("build_results") or {}
    return (br.get("all_checks_pass") and not br.get("qc_critical"), "build/QC chưa sạch")


def g3_signed(board):
    return _signed(board, "G3")


def security_scanned(board):
    return (board.get("security_report") is not None, "chưa có security_report")


def g4_signed(board):
    rep = board.get("security_report") or {}
    open_hc = [f for f in rep.get("findings", [])
               if f.get("severity") in ("High", "Critical") and not f.get("remediation")]
    if open_hc:
        return False, f"{len(open_hc)} finding High/Critical chưa xử lý"
    return _signed(board, "G4")
