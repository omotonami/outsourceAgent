"""Dữ liệu hiển thị kèm gate (Code thuần, chỉ đọc). Không đụng guard trong gates.py.
H3: G1 hiển thị decision_records phase presales, G2 phase discovery. KHÔNG trả huddle_log (transcript)."""

GATE_PHASE = {"G1": "presales", "G2": "discovery"}
FIELDS = ("question", "options", "chosen", "rationale", "risks", "open_questions",
          "participants", "rounds", "phase", "trigger", "stopped_reason")


def decision_records(board, gate):
    phase = GATE_PHASE.get(gate)
    if not phase:
        return []
    return [{k: r[k] for k in FIELDS if k in r}
            for r in board.get("decision_records") or [] if r.get("phase") == phase]
