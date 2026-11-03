"""H1 Agent Factory: sinh x9_demo trong tmp_path (KHÔNG ghi repo thật) → lint pass → test sinh ra pass;
lint bắt lỗi cố ý; scaffold không ghi đè; draft LLM chỉ là RECOMMENDATION."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src.factory import agent_lint, interview, new, scaffold

REPO = Path(__file__).parents[2]

ANSWERS = {
    "id": "x9_demo", "name": "X9 Demo Summarizer",
    "job": "Tóm tắt meeting note thành danh sách action item.",
    "done_when": ["Mọi action item có owner", "Không có item ngoài meeting note"],
    "anti_jobs": ["Không giao việc thay PM", "Không ghi zone khác ngoài demo_output"],
    "forbidden": ["Không bịa owner không có trong note"],
    "input": [{"name": "note", "type": "string", "required": True},
              {"name": "attendees", "type": "string[]", "required": False}],
    "untrusted_inputs": "note (client viết) → dữ liệu",
    "output": [{"name": "action_items", "type": "object[]", "required": True},
               {"name": "summary", "type": "string", "required": True}],
    "zone": "demo_output",
    "tools": [],
    "phase": "discovery", "trigger": "người kích hoạt", "risk_level": "normal", "model_tier": "sonnet",
}


def _minimal_repo(dst: Path):
    """Copy phần repo tối thiểu để agent chạy được: specs, src, config, knowledge, khung tests."""
    ign = shutil.ignore_patterns("__pycache__", "*.pyc")
    for d in ("specs", "src", "config", "knowledge"):
        shutil.copytree(REPO / d, dst / d, ignore=ign)
    (dst / "tests/contract").mkdir(parents=True)
    for f in ("tests/__init__.py", "tests/conftest.py", "tests/contract/__init__.py"):
        shutil.copy(REPO / f, dst / f)
    return dst


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = _minimal_repo(tmp_path_factory.mktemp("repo"))
    spec = interview.build_spec(ANSWERS, root=root)
    scaffold.scaffold(spec, root=root, dry_run=False)
    return root


@pytest.fixture
def repo(built, tmp_path):
    dst = tmp_path / "r"
    shutil.copytree(built, dst)
    return dst


def test_interview_at_most_7_questions():
    assert len(interview.QUESTIONS) <= 7
    keys = {k for q in interview.QUESTIONS for k in q["keys"]}
    assert {"id", "job", "anti_jobs", "input", "output", "tools", "phase", "risk_level", "model_tier", "zone"} <= keys


def test_build_spec_rejects_bad_answers():
    with pytest.raises(ValueError):
        interview.build_spec({**ANSWERS, "anti_jobs": []})
    with pytest.raises(ValueError):
        interview.build_spec({**ANSWERS, "id": "Demo"})
    with pytest.raises(ValueError):
        interview.build_spec({**ANSWERS, "phase": "nowhere"})


def test_draft_from_llm_is_recommendation_until_confirmed():
    fake = lambda model, system, messages, max_tokens: (json.dumps(ANSWERS), {"total_tokens": 5})
    d = interview.draft_from_llm("bot tóm tắt meeting", sender=fake)
    assert d["label"] == "RECOMMENDATION" and d["confirmed"] is False
    with pytest.raises(ValueError):
        interview.build_spec(d)                      # chưa xác nhận → chặn
    spec = interview.build_spec(interview.confirm(d, {"name": "X9"}))
    assert spec["id"] == "x9_demo" and spec["name"] == "X9"


def test_dry_run_writes_nothing(tmp_path):
    root = _minimal_repo(tmp_path / "r")
    before = (root / "specs/03_routing.yaml").read_text(encoding="utf-8")
    out = scaffold.scaffold(interview.build_spec(ANSWERS, root=root), root=root)
    assert any(s.startswith("create specs/02_agents/x9_demo.md") for s in out)
    assert any("specs/05_blackboard.yaml" in s for s in out)
    assert not (root / "specs/02_agents/x9_demo.md").exists()
    assert (root / "specs/03_routing.yaml").read_text(encoding="utf-8") == before


def test_generated_agent_passes_lint_and_keeps_yaml(built):
    assert agent_lint.lint("x9_demo", built) == []
    for rel in ("specs/03_routing.yaml", "specs/05_blackboard.yaml"):
        new_t, old_t = (built / rel).read_text(encoding="utf-8"), (REPO / rel).read_text(encoding="utf-8")
        old_comments = [l for l in old_t.splitlines() if "#" in l]
        assert all(c in new_t.splitlines() for c in old_comments)      # giữ comment
    bb = yaml.safe_load((built / "specs/05_blackboard.yaml").read_text(encoding="utf-8"))
    assert bb["zones"]["demo_output"] == {"writers": ["x9_demo"], "mode": "owner_write"}
    assert "retention" in bb                                              # không phá phần sau
    md = (built / "specs/02_agents/x9_demo.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(re.match(r"---\n(.*?)\n---", md, re.S).group(1))
    assert meta["subordinates"] == [] and meta["anti_jobs"] == ANSWERS["anti_jobs"]
    assert "{SKILL_GUIDANCE}" in md


def test_generated_contract_test_passes_offline(built):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/contract/test_x9_demo.py"], cwd=built, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4 passed" in r.stdout


def _edit(p: Path, old, new_):
    t = p.read_text(encoding="utf-8")
    assert old in t
    p.write_text(t.replace(old, new_, 1), encoding="utf-8")


def _break_card(r):
    _edit(r / "specs/02_agents/x9_demo.md", 'trigger: "người kích hoạt"\n', "")
    _edit(r / "specs/02_agents/x9_demo.md", "| Trigger |", "| Trig |")


def _break_forbidden(r):
    p = r / "specs/02_agents/x9_demo.md"
    t = p.read_text(encoding="utf-8")
    p.write_text(re.sub(r"(## Forbidden[^\n]*\n)(?:- [^\n]*\n)+", r"\1", t), encoding="utf-8")


def _break_schema(r):
    (r / "specs/04_schemas/x9_demo.out.json").write_text('{"type": "object", "properties": ', encoding="utf-8")


def _break_zone(r):
    _edit(r / "specs/05_blackboard.yaml", 'writers: ["x9_demo"]', 'writers: ["x9_demo", "b4_tech_lead"]')


def _break_routing(r):
    p = r / "specs/03_routing.yaml"
    p.write_text("".join(l for l in p.read_text(encoding="utf-8").splitlines(True)
                         if not l.strip().startswith("x9_demo:")), encoding="utf-8")


def _break_runner(r):
    (r / "src/agents/x9_demo.py").unlink()


def _break_test(r):
    (r / "tests/contract/test_x9_demo.py").unlink()


@pytest.mark.parametrize("breaker, code", [
    (_break_card, "CARD_FIELD"),
    (_break_forbidden, "FORBIDDEN_EMPTY"),
    (_break_schema, "SCHEMA"),
    (_break_zone, "ZONE_SHARED"),
    (_break_routing, "ROUTING"),
    (_break_runner, "RUNNER"),
    (_break_test, "TEST"),
])
def test_lint_catches_deliberate_errors(repo, breaker, code):
    breaker(repo)
    errs = agent_lint.lint("x9_demo", repo)
    assert any(f"[{code}]" in e for e in errs), errs


def test_scaffold_never_overwrites(built, repo):
    spec = interview.build_spec(ANSWERS, root=repo)
    with pytest.raises(scaffold.ScaffoldError):          # chạy lại → đã tồn tại
        scaffold.scaffold(spec, root=repo, dry_run=False)
    fresh = _minimal_repo(repo.parent / "fresh")
    (fresh / "src/agents/x9_demo.py").write_text("# tay viết\n", encoding="utf-8")
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.scaffold(interview.build_spec(ANSWERS, root=fresh), root=fresh, dry_run=False)
    assert (fresh / "src/agents/x9_demo.py").read_text(encoding="utf-8") == "# tay viết\n"
    assert not (fresh / "specs/02_agents/x9_demo.md").exists()          # không ghi dở dang
    with pytest.raises(scaffold.ScaffoldError):                         # zone đã có
        scaffold.scaffold(interview.build_spec({**ANSWERS, "id": "x8_other", "zone": "estimate"},
                                               root=fresh), root=fresh)


def test_cli_new_dry_run_and_write(tmp_path, capsys):
    root = _minimal_repo(tmp_path / "r")
    sp = tmp_path / "spec.yaml"
    sp.write_text(yaml.safe_dump(ANSWERS, allow_unicode=True), encoding="utf-8")
    assert new.main(["--spec", str(sp), "--root", str(root)]) == 0
    assert not (root / "src/agents/x9_demo.py").exists()
    assert new.main(["--spec", str(sp), "--root", str(root), "--write"]) == 0
    assert "agent_lint: PASS" in capsys.readouterr().out
    assert new.main(["--spec", str(sp), "--root", str(root), "--write"]) == 2   # lần 2: không đè


def test_lint_all_runs_on_real_repo(capsys):
    rc = agent_lint.main(["--all"])
    out = capsys.readouterr().out
    assert rc in (0, 1) and "pass" in out
    assert not (REPO / "specs/02_agents/x9_demo.md").exists()           # không rò x9_demo vào repo thật
