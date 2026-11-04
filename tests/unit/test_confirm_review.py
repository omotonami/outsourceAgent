"""Review PR #2 (2026-11-04) — biến thể danh tính máy từ /tmp/soi3/t_who.py. confirm là lớp CHỐNG DÙNG NHẦM,
không phải xác thực."""
import pytest

from src.common import config
from src.governance import confirm

MACHINE_VARIANTS = ["Claude", "claude-code", "Claude Code", "ClaudeCode", "Soi", "Chief", "Chief@TechTus",
                    "e2_cos", "E2 COS", "e2cos", "ｏｒｃｈｅｓｔｒａｔｏｒ", "orch estrator", "c1dev", "C1DEV", "a1bu",
                    "factoryinterviewer", "bot", "assistant", "gate ui", "gateui", "GATEUI", "g​ate_ui",
                    "orche​strator", "c1­dev", "﻿Claude", "or⁠chestrator", "huddle",
                    "factory_interviewer", "chief-ops", "chief ops", "chiefops", "va",
                    "chief-of-staff", "claude/assistant"]
HUMANS = ["Daipc", "Lan Nguyen", "Nguyễn Văn An", "Tanaka 太郎", "Hoàng", "Ngân", "lan@company.vn"]


@pytest.mark.parametrize("who", MACHINE_VARIANTS)
def test_machine_variants_not_human(who):
    assert not confirm.is_human(who), who


@pytest.mark.parametrize("who", HUMANS)
def test_humans_still_human(who):
    assert confirm.is_human(who), who


def test_cf_characters_stripped_before_nfkc():
    assert confirm._tokens("g​ate‍_ui") == ("gate", "ui")
    assert confirm._tokens("﻿Lan​") == ("lan",)


def test_joined_forms_generated_from_agent_ids():
    for aid in config.agent_ids():
        assert confirm.is_machine(aid.replace("_", "")), aid


def test_governance_extra_loaded_from_config():
    extra = config.load_yaml(confirm.GOVERNANCE)["confirm"]["machine_ids_extra"]
    assert set(extra) <= confirm.MACHINE_IDS
    assert confirm.BUILTIN_MACHINE_IDS <= confirm.MACHINE_IDS


def test_require_human_refuses_claude():
    with pytest.raises(ValueError):
        confirm.require_human("Claude", ValueError, "không phải người: {who}")


def test_docstring_says_not_authentication():
    assert "KHÔNG PHẢI XÁC THỰC" in confirm.__doc__
