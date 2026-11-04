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


# ---------- Gate dự án thật (event store) — dữ liệu trình gate, chỉ đọc ----------
# Dùng chung cho src/ops/gate.py (ký/trả lại), GET /api/projects/{id}/gate và Phòng 3D (chế độ project).
GATE_STATE = {"G1": "AWAIT_G1", "G2": "AWAIT_G2", "G3": "AWAIT_G3", "G4": "AWAIT_G4"}
STATE_GATE = {v: k for k, v in GATE_STATE.items()}
GATE_SIGNER = {"G1": "Sales Lead (giá)", "G2": "Client + PM (scope)", "G3": "Tech Lead (merge)",
               "G4": "Tech Lead + bảo mật (release)"}
# Agent có output được duyệt ở gate (khớp các phase trong src/ops/pipeline.py).
GATE_AGENTS = {"G1": ("a1_bu", "a2_estimation", "a3_proposal"),
               "G2": ("b1_ba", "b2_ux", "b3_ui", "b4_tech_lead"),
               "G3": ("c1_dev", "c2_tester", "c3_qc"),
               "G4": ("d1_soc",)}
GATE_ZONES = {"G1": ("business_context", "estimate", "proposal"),
              "G2": ("requirements", "design_spec", "ui_system", "architecture", "task_backlog"),
              "G3": ("task_backlog", "build_results"),
              "G4": ("security_report",)}
# Trả lại mặc định: về phase làm ra output của gate đó.
BACKTRACK = {"G1": "PRESALES", "G2": "DISCOVERY", "G3": "BUILD", "G4": "SECURITY"}
WORK_STATES = ("PRESALES", "DISCOVERY", "BUILD", "SECURITY")
HIGH = ("High", "Critical")


def _highlights(board, gate) -> dict:
    """Phần rủi ro hiện TRƯỚC nội dung (specs/06_gates.md cột 'Màn hình hiện gì trước')."""
    h: dict = {}
    if gate == "G1":
        est = board.get("estimate") or {}
        bc = board.get("business_context") or {}
        h["assumptions"] = list(est.get("assumptions") or [])
        h["exclusions"] = list(est.get("exclusions") or [])
        h["assumption_items"] = [m.get("name") for m in bc.get("modules") or [] if m.get("label") == "ASSUMPTION"]
        h["ambiguities"] = list(bc.get("ambiguities") or [])
    elif gate == "G2":
        req = board.get("requirements") or {}
        h["stories"] = len(req.get("stories") or [])
        h["open_questions"] = list(req.get("open_questions") or [])
        h["scope_delta"] = req.get("scope_delta")
    elif gate == "G3":
        tb = board.get("task_backlog") or []
        br = board.get("build_results") or {}
        h["tasks"] = {t.get("id"): t.get("status") for t in tb}
        h["qc_critical"] = br.get("qc_critical")
        h["all_checks_pass"] = br.get("all_checks_pass")
    elif gate == "G4":
        rep = board.get("security_report") or {}
        h["open_high_findings"] = [f for f in rep.get("findings") or []
                                   if f.get("severity") in HIGH and not f.get("remediation")]
    h["open_high_risks"] = [{k: r.get(k) for k in ("risk", "severity", "status", "phase")}
                            for r in board.get("risk_register") or []
                            if r.get("severity") in HIGH and r.get("status") not in ("closed", "resolved")]
    return h


def packet(board, gate) -> dict:
    """Dữ liệu trình gate: highlights + zone output của phase + decision_records (không huddle_log /
    transcript / raw thinking). Ổn định để băm (confirm.payload_hash)."""
    from src.blackboard.eventstore import scrub
    if gate not in GATE_STATE:
        raise ValueError(f"gate lạ: {gate!r}")
    return scrub({"gate": gate, "signer_role": GATE_SIGNER[gate], "highlights": _highlights(board, gate),
                  "zones": {z: board.get(z) for z in GATE_ZONES[gate]},
                  "decision_records": decision_records(board, gate)})


def pending_gate(board) -> str | None:
    """Gate đang chờ theo project_status.state (Orchestrator ghi)."""
    return STATE_GATE.get((board.get("project_status") or {}).get("state"))


def room_gate(board) -> dict | None:
    """{id, agents, who} cho Phòng 3D chế độ project khi đang chờ gate."""
    g = pending_gate(board)
    if g and board.is_signed(g):            # đã ký, chờ `project run` đi tiếp
        return None
    return {"id": g, "agents": list(GATE_AGENTS[g]), "who": GATE_SIGNER[g]} if g else None
