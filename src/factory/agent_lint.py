"""agent_lint (Code thuần): chặn agent không đủ chuẩn trước khi merge.

CLI:  python -m src.factory.agent_lint <agent_id> [--root DIR]
      python -m src.factory.agent_lint --all [--root DIR]
Exit 0 nếu không lỗi (ngoài allowlist), 1 nếu có lỗi.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).parents[2]

# Card v5: field -> (key frontmatter chấp nhận, nhãn bảng chấp nhận theo tiền tố)
CARD_FIELDS = {
    "Role": (["role"], ["Role"]),
    "Trigger": (["trigger"], ["Trigger"]),
    "Input": ([], ["Input"]),
    "Tools": (["tools"], ["Tools"]),
    "Output": ([], ["Output"]),
    "Autonomy": (["autonomy"], ["Autonomy"]),
    "Reflection": (["reflection"], ["Reflection"]),
    "Untrusted inputs": (["untrusted_inputs"], ["Untrusted inputs"]),
    "Version": (["version", "prompt_version"], ["Version"]),
    "Budget": (["budget"], ["Budget"]),
}
SECTIONS = ["Forbidden", "Done when"]          # phải có và không rỗng
ROUTING_FILES = ["specs/03_routing.yaml", "src/gateway/routing.yaml"]
ZONE_FILES = ["specs/05_blackboard.yaml", "src/blackboard/permissions.yaml"]

# Ngoại lệ có chủ đích cho agent HIỆN CÓ (không sửa spec của chúng). (agent, code) -> lý do
ALLOWLIST = {
    ("c1_dev", "ZONE_SHARED"): "build_results: thiết kế cố ý — mỗi agent ghi một field con (05_blackboard.yaml)",
    ("c2_tester", "ZONE_SHARED"): "build_results: thiết kế cố ý — mỗi agent ghi một field con (05_blackboard.yaml)",
    ("c3_qc", "ZONE_SHARED"): "build_results: thiết kế cố ý — mỗi agent ghi một field con (05_blackboard.yaml)",
}


def _front(text):
    m = re.match(r"\s*---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        return {}, text
    try:
        return yaml.safe_load(m.group(1)) or {}, m.group(2)
    except yaml.YAMLError:
        return {"__yaml_error__": True}, m.group(2)


def _table_labels(body):
    return {m.group(1).strip() for m in re.finditer(r"^\|\s*([^|]+?)\s*\|", body, re.M)}


def _section(body, title):
    """Nội dung giữa '## <title>...' và heading kế tiếp; None nếu không có."""
    m = re.search(rf"^##\s+{re.escape(title)}\b.*?$(.*?)(?=^#{{1,3}}\s|\Z)", body, re.M | re.S)
    return None if m is None else m.group(1)


def _load_yaml(root, rel):
    p = Path(root) / rel
    if not p.exists():
        return None
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"__yaml_error__": True}


def agent_ids(root=ROOT):
    return sorted(p.stem for p in (Path(root) / "specs/02_agents").glob("*.md"))


def lint_raw(agent_id, root=ROOT):
    """Trả list (code, message), CHƯA lọc allowlist."""
    root = Path(root)
    errs = []
    add = lambda code, msg: errs.append((code, msg))
    spec_p = root / f"specs/02_agents/{agent_id}.md"
    if not spec_p.exists():
        return [("NO_SPEC", f"thiếu specs/02_agents/{agent_id}.md")]
    meta, body = _front(spec_p.read_text(encoding="utf-8"))
    if meta.get("__yaml_error__"):
        add("FRONTMATTER", "frontmatter YAML lỗi")
        meta = {}
    labels = _table_labels(body)

    # 1. Card v5
    for field, (keys, prefixes) in CARD_FIELDS.items():
        in_meta = any(str(meta.get(k, "")).strip() for k in keys)
        in_table = any(l.startswith(p) for l in labels for p in prefixes)
        if not (in_meta or in_table):
            add("CARD_FIELD", f"Card v5 thiếu field '{field}'")
    for title in SECTIONS:
        sec = _section(body, title)
        if sec is None:
            add("CARD_FIELD", f"Card v5 thiếu mục '{title}'")
        elif not re.search(r"^\s*[-*]\s+\S", sec, re.M):
            add("FORBIDDEN_EMPTY" if title == "Forbidden" else "DONE_EMPTY", f"mục '{title}' rỗng")

    # 2. Prompt 4 module + chỗ chèn skill
    if "## Prompt" not in body:
        add("PROMPT", "thiếu '## Prompt'")
    else:
        prompt = body.split("## Prompt", 1)[1]
        for n in range(1, 5):
            if f"MODULE {n}" not in prompt:
                add("PROMPT", f"prompt thiếu MODULE {n}")
        if "{SKILL_GUIDANCE}" not in prompt:
            add("PROMPT", "prompt thiếu {SKILL_GUIDANCE}")

    # 3. role khớp hậu tố id (skill_loader) và duy nhất
    role = agent_id.split("_", 1)[-1]
    if meta.get("role") and meta["role"] != role:
        add("ROLE", f"role '{meta['role']}' khác hậu tố id '{role}' — skill_loader sẽ nạp sai")
    dup = [a for a in agent_ids(root) if a != agent_id and a.split("_", 1)[-1] == role]
    if dup:
        add("ROLE", f"role '{role}' trùng với {dup}")

    # 4. subordinates (nếu khai báo)
    subs = meta.get("subordinates", [])
    if subs is not None and not isinstance(subs, list):
        add("SUBORDINATES", "subordinates phải là list")
    for s in subs or []:
        if not (root / f"specs/02_agents/{s}.md").exists():
            add("SUBORDINATES", f"subordinate '{s}' không có spec")

    # 5. Schema I/O
    for kind in ("in", "out"):
        p = root / f"specs/04_schemas/{agent_id}.{kind}.json"
        if not p.exists():
            add("SCHEMA", f"thiếu {p.relative_to(root)}")
            continue
        try:
            sch = json.loads(p.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(sch)
            if sch.get("type") != "object":
                add("SCHEMA", f"{p.name}: type gốc phải là object")
        except (json.JSONDecodeError, SchemaError) as e:
            add("SCHEMA", f"{p.name} không hợp lệ: {str(e).splitlines()[0]}")

    # 6. Routing (spec + bản runtime)
    for rel in ROUTING_FILES:
        cfg = _load_yaml(root, rel)
        if cfg is None or cfg.get("__yaml_error__"):
            add("ROUTING", f"{rel} thiếu hoặc lỗi YAML")
        elif agent_id not in (cfg.get("agents") or {}):
            add("ROUTING", f"{rel}: thiếu entry agents.{agent_id}")

    # 7. Zone: owner_write không được trùng writer là agent khác; zone khai báo phải tồn tại ở cả 2 file
    agents = set(agent_ids(root))
    declared = meta.get("zone")
    for rel in ZONE_FILES:
        cfg = _load_yaml(root, rel)
        if cfg is None or cfg.get("__yaml_error__"):
            add("ZONE", f"{rel} thiếu hoặc lỗi YAML")
            continue
        zones = cfg.get("zones") or {}
        if declared:
            z = zones.get(declared)
            if not z or agent_id not in (z.get("writers") or []):
                add("ZONE", f"{rel}: zone '{declared}' không có hoặc {agent_id} không phải writer")
        for zname, z in zones.items():
            w = z.get("writers") or []
            if z.get("mode") == "owner_write" and agent_id in w:
                others = sorted(set(w) & agents - {agent_id})
                if others:
                    add("ZONE_SHARED", f"{rel}: zone owner_write '{zname}' trùng writer với {others}")

    # 8. Runner + test
    if not (root / f"src/agents/{agent_id}.py").exists():
        add("RUNNER", f"thiếu src/agents/{agent_id}.py")
    if not (root / f"tests/contract/test_{agent_id}.py").exists():
        mentioned = any(agent_id in p.read_text(encoding="utf-8", errors="ignore")
                        for p in (root / "tests").rglob("test_*.py"))
        if not mentioned:
            add("TEST", f"thiếu tests/contract/test_{agent_id}.py (và không test nào nhắc tới {agent_id})")
    return errs


def lint(agent_id, root=ROOT, allowlist=None):
    """Trả list chuỗi lỗi (đã bỏ ngoại lệ trong allowlist). Rỗng = pass."""
    allow = ALLOWLIST if allowlist is None else allowlist
    return [f"{agent_id}: [{c}] {m}" for c, m in lint_raw(agent_id, root) if (agent_id, c) not in allow]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m src.factory.agent_lint")
    ap.add_argument("agent_id", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    if not a.all and not a.agent_id:
        ap.error("cần <agent_id> hoặc --all")
    ids = agent_ids(a.root) if a.all else [a.agent_id]
    bad = 0
    for aid in ids:
        errs = lint(aid, a.root)
        allowed = [(c, m) for c, m in lint_raw(aid, a.root) if (aid, c) in ALLOWLIST]
        print(f"{'FAIL' if errs else 'PASS'} {aid}")
        for e in errs:
            print(f"  - {e}")
        for c, m in allowed:
            print(f"  ~ allowlist [{c}] {m} — {ALLOWLIST[(aid, c)]}")
        bad += bool(errs)
    print(f"{len(ids) - bad}/{len(ids)} pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
