"""Sender MOCK cho `--mock`: chạy thử toàn luồng không cần API key, không gọi mạng.

Thay `sender` của gateway (src/gateway/llm_client.call vẫn chạy đủ: PII mask, budget, trace, usage):
nhận diện agent từ system prompt (prompt spec của agent, hoặc vai huddle) rồi trả JSON đúng schema
output của agent đó. Không phải LLM — nội dung cố định + lấy lại một phần input (để test redact thật).
"""
from __future__ import annotations

import json
from functools import lru_cache

from src.agents import runtime

AGENTS = ("a1_bu", "a2_estimation", "a3_proposal", "b1_ba", "b2_ux", "b3_ui", "b4_tech_lead",
          "c1_dev", "c2_tester", "c3_qc", "d1_soc")
TOKENS = {"in": 1200, "out": 600}       # usage giả mỗi lần gọi (để báo cáo có số)


@lru_cache(maxsize=1)
def _prefixes() -> dict[str, str]:
    return {a: runtime._prompt(a).split("{SKILL_GUIDANCE}")[0] for a in AGENTS}


def identify(system: str) -> str:
    """Agent của system prompt (prefix dài nhất khớp); vai huddle → 'huddle:<role>'."""
    for role in ("PROPOSER", "CRITIC", "DECIDER"):
        if f"vai {role}" in system:
            return f"huddle:{role.lower()}"
    best = max((a for a, p in _prefixes().items() if system.startswith(p)),
               key=lambda a: len(_prefixes()[a]), default=None)
    if best is None:
        raise ValueError("mock: không nhận ra agent từ system prompt")
    return best


def _input(messages) -> dict:
    try:
        v = json.loads(messages[0]["content"])
        return v if isinstance(v, dict) else {}
    except (ValueError, KeyError, IndexError, TypeError):
        return {}


def _out(agent: str, inp: dict) -> dict:
    if agent == "a1_bu":
        lb = inp.get("lead_brief") or {}
        summ = str(lb.get("summary") or lb.get("description") or "mock")
        return {"business_context": {"summary": summ, "goals": lb.get("goals") or []},
                "domain": str(lb.get("domain") or "fintech"),
                "modules": [{"name": "Onboarding", "desc": summ[:80], "label": "ASSUMPTION"},
                            {"name": "Payments", "desc": "luồng thanh toán (mock)", "label": "ASSUMPTION"}],
                "tech_hints": ["react", "node"], "compliance_flags": ["PDPA"],
                "ambiguities": ["Nhà cung cấp cổng thanh toán?", "SLA uptime?", "Hạn mức giao dịch?"]}
    if agent == "a2_estimation":
        mods = inp.get("modules") or [{"name": "Core"}]
        return {"wbs": [{"id": f"W{i + 1}", "item": f"{m['name']} flow", "module": m["name"], "role": "dev",
                         "size": "M"} for i, m in enumerate(mods)],
                "assumptions": ["Client cung cấp sandbox cổng thanh toán"],
                "exclusions": ["Pentest bên thứ ba"], "risks": [], "reflection": {"passed": True, "issues": []}}
    if agent == "a3_proposal":
        est = inp.get("estimate") or {}
        return {"proposal_doc": "Proposal (mock)",
                "assumptions_section": "\n".join(est.get("assumptions") or []),
                "exclusions_section": "\n".join(est.get("exclusions") or []), "review_notes": []}
    if agent == "b1_ba":
        return {"epics": ["Onboarding"],
                "stories": [{"as_a": "user", "i_want": f"story {i}", "so_that": "dùng app",
                             "AC": [f"Given trạng thái {i} When người dùng gửi Then hệ thống lưu"]} for i in (1, 2)],
                "open_questions": []}
    if agent == "b2_ux":
        return {"flows": ["onboarding"], "screens": [{"id": "S1", "purpose": "story 1", "states": ["empty", "error"],
                                                      "story_refs": ["S1"]},
                                                     {"id": "S2", "purpose": "story 2", "states": ["default"],
                                                      "story_refs": ["S2"]}]}
    if agent == "b3_ui":
        return {"tokens_css": ":root{--primary:#0F6E6E}",
                "components": [{"name": "Button", "html": "<button>OK</button>", "states": ["default"]}],
                "screens": [{"id": "S1", "html": "<main></main>", "macrostructure": "single-column"}]}
    if agent == "b4_tech_lead":
        return {"architecture": {"components": ["app", "api"]},
                "adr": [{"decision": "monolith trước", "reason": "MVP"}],
                "task_backlog": [
                    {"id": "T1", "story_ref": "S1", "spec": "story 1", "AC": ["Given a When b Then c"],
                     "deps": [], "size": "M", "risk_level": "normal", "owns": ["src/onboarding/"]},
                    {"id": "T2", "story_ref": "S2", "spec": "story 2", "AC": ["Given a When b Then c"],
                     "deps": ["T1"], "size": "S", "risk_level": "low", "owns": ["src/payments/"]}]}
    if agent == "c1_dev":
        return {"plan": ["làm theo spec"], "tests_to_add": ["test AC"], "files_touched": ["src/x.py"]}
    if agent == "c2_tester":
        return {"test_cases": [{"ac_ref": "AC1", "kind": "unit", "steps": ["chạy"], "expected": "pass"}]}
    if agent == "c3_qc":
        return {"verdict": "pass", "issues": []}
    if agent == "d1_soc":
        return {"findings": inp.get("raw_findings") or [], "gate": "pass"}
    role = agent.split(":", 1)[1]
    if role == "proposer":
        return {"summary": "đề xuất (mock): phương án A / B", "options": ["A", "B"]}
    if role == "critic":
        return {"summary": "phản biện (mock)", "risks": ["phụ thuộc đối tác"], "open_questions": [],
                "converged": True}
    return {"question": inp.get("question") or "?", "options": ["A", "B"], "chosen": "A",
            "rationale": "ít rủi ro hơn (mock)", "risks": ["phụ thuộc đối tác"], "open_questions": []}


def sender(model, system, messages, max_tokens):
    """Chữ ký giống llm_client._send: trả (text, usage)."""
    out = _out(identify(system), _input(messages))
    return json.dumps(out, ensure_ascii=False), {"input_tokens": TOKENS["in"], "output_tokens": TOKENS["out"],
                                                 "total_tokens": TOKENS["in"] + TOKENS["out"]}
