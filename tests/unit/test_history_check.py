"""CLAUDE.md luật 9: history_check fail khi src/ specs/ config/ đổi mà docs/history/ không đổi (repo git tạm)."""
from pathlib import Path

import pytest

from src.tools.checks import history_check as hc
from src.tools.integrations import repo as git

INDEX = """# Lịch sử
| Ngày | Đợt | Tóm tắt | Commit range |
|---|---|---|---|
| 2026-11-01 | [đầu](2026-11-01_dau.md) | x | `a..b` |
"""


def _w(root: Path, rel: str, text: str = "x\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = git.init(tmp_path / "r", branch="main")
    _w(root, "src/a.py")
    _w(root, "docs/history/README.md", INDEX)
    _w(root, "docs/history/2026-11-01_dau.md", "# đầu\n")
    git.commit_all(root, "base")
    git.switch_branch(root, "feat/x", create=True)
    return root


def test_pass_when_nothing_changed(repo):
    assert hc.check(repo, "main") == []


def test_fail_code_change_without_history(repo, capsys):
    _w(repo, "src/a.py", "y\n")
    _w(repo, "config/b.yaml", "k: 1\n")
    git.commit_all(repo, "code")
    errs = hc.check(repo, "main")
    assert len(errs) == 1 and "luật 9" in errs[0] and "src/a.py" in errs[0]
    assert hc.main(["--base", "main", "--root", str(repo)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_fail_uncommitted_specs_change(repo):
    _w(repo, "specs/new_card.md")                         # untracked, chưa commit
    assert hc.check(repo, "main")


def test_pass_with_history_entry(repo, capsys):
    _w(repo, "src/a.py", "y\n")
    _w(repo, "docs/history/2026-11-04_dot-moi.md", "# mới\n")
    _w(repo, "docs/history/README.md", INDEX + "| 2026-11-04 | [mới](2026-11-04_dot-moi.md) | y | `b..` |\n")
    git.commit_all(repo, "code + history")
    assert hc.main(["--base", "main", "--root", str(repo)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_fail_history_file_missing_from_index_or_bad_name(repo):
    _w(repo, "src/a.py", "y\n")
    _w(repo, "docs/history/2026-11-04_khong-muc-luc.md")
    _w(repo, "docs/history/notes.md")
    git.commit_all(repo, "x")
    errs = hc.check(repo, "main")
    assert any("mục lục" in e for e in errs) and any("notes.md" in e for e in errs)


def test_docs_only_change_passes_and_bad_ref_rc2(repo, capsys):
    _w(repo, "docs/other.md")
    git.commit_all(repo, "docs")
    assert hc.check(repo, "main") == []                   # docs ngoài history không bị bắt
    assert hc.main(["--base=--evil", "--root", str(repo)]) == 2
    assert hc.main(["--base", "khong-co-ref", "--root", str(repo)]) == 2


def test_not_git_repo_gives_bundle_hint(tmp_path, capsys):
    """Folder giải nén từ zip (không .git) → rc 3 + hướng dẫn clone từ bundle, không traceback."""
    d = tmp_path / "unzipped"
    _w(d, "docs/history/README.md", INDEX)
    assert not hc.is_git_repo(d)
    assert hc.main(["--base", "b7731d3", "--root", str(d)]) == hc.NOT_GIT_RC
    err = capsys.readouterr().err
    assert "KHÔNG phải git repo" in err and "git clone outsourceAgent-company.bundle -b feat/v3-bot-team" in err
    assert "START_HERE.md" in err


def test_is_git_repo_true_for_repo(repo):
    assert hc.is_git_repo(repo)
