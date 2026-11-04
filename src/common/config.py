"""Đọc config/*.yaml, specs/*.yaml, specs/04_schemas/*.json — MỘT chỗ.

- Mỗi lần gọi đọc lại file và trả object MỚI (không cache dùng chung): module giữ hằng số riêng
  như trước, test monkeypatch dict của module này không lan sang module khác.
- Thiếu file / thiếu khoá bắt buộc → ConfigError (không default hard-code, luật 6).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]
BUDGETS = "config/budgets.yaml"
AUTONOMY = "config/autonomy.yaml"
MODELS_LOCK = "config/models.lock"
TRANSITIONS = "src/orchestrator/transitions.yaml"
_FRONT = re.compile(r"\s*---\n(.*?)\n---\n(.*)", re.S)
_MISSING = object()


class ConfigError(KeyError):
    """Thiếu file hoặc khoá cấu hình bắt buộc."""


def path(rel: str | Path, root: str | Path | None = None) -> Path:
    return Path(root or ROOT) / rel


def _read(rel: str | Path, root: str | Path | None) -> str:
    p = path(rel, root)
    if not p.is_file():
        raise ConfigError(f"thiếu file cấu hình {rel}")
    return p.read_text(encoding="utf-8")


def load_yaml(rel: str | Path, root: str | Path | None = None) -> Any:
    """YAML → object; file rỗng → {}."""
    data = yaml.safe_load(_read(rel, root))
    return {} if data is None else data


def load_json(rel: str | Path, root: str | Path | None = None) -> Any:
    return json.loads(_read(rel, root))


def section(cfg: dict, key: str, source: str) -> dict:
    """cfg[key] phải là dict (vd budgets.yaml → dispatcher). Thiếu → ConfigError."""
    sec = cfg.get(key) if isinstance(cfg, dict) else None
    if not isinstance(sec, dict):
        raise ConfigError(f"{source}: thiếu '{key}'")
    return sec


def require(cfg: dict, dotted: str, source: str, *, nullable: bool = False) -> Any:
    """Giá trị tại khoá 'a.b.c'. Thiếu → ConfigError; null mà không nullable → ConfigError."""
    cur: Any = cfg
    for part in dotted.split("."):
        cur = cur.get(part, _MISSING) if isinstance(cur, dict) else _MISSING
        if cur is _MISSING:
            raise ConfigError(f"{source}: thiếu '{dotted}'")
    if cur is None and not nullable:
        raise ConfigError(f"{source}: '{dotted}' không được null")
    return cur


def split_front_matter(text: str) -> tuple[dict, str]:
    """'---\\n<yaml>\\n---\\n<body>' → (meta, body). Không có frontmatter → ({}, text).
    YAML lỗi → yaml.YAMLError (caller quyết xử lý)."""
    m = _FRONT.match(text)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def agent_spec_meta(agent_id: str, root: str | Path | None = None) -> dict:
    """Frontmatter của specs/02_agents/<id>.md."""
    return split_front_matter(_read(f"specs/02_agents/{agent_id}.md", root))[0]


def agent_ids(root: str | Path | None = None) -> set[str]:
    """Agent có spec (specs/02_agents/*.md)."""
    return {p.stem for p in path("specs/02_agents", root).glob("*.md")}
