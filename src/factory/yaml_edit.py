"""Chèn entry vào YAML của specs/ dạng TEXT (giữ comment), rồi parse lại để chắc không phá file."""
import re

import yaml


class ScaffoldError(Exception):
    pass


def insert_in_block(text, block, line):
    """Chèn `line` (chưa thụt lề) vào cuối block mapping top-level `block:` — giữ nguyên comment."""
    lines = text.splitlines(keepends=True)
    head = next((i for i, l in enumerate(lines) if re.match(rf"^{re.escape(block)}:\s*(#.*)?$", l.rstrip("\r\n"))), None)
    if head is None:
        raise ScaffoldError(f"không thấy block '{block}:'")
    last, indent = head, "  "
    for j in range(head + 1, len(lines)):
        l = lines[j]
        if not l.strip():
            continue
        if l[0] in " \t":
            if last == head:
                indent = l[: len(l) - len(l.lstrip())]
            last = j
            continue
        break
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    lines.insert(last + 1, indent + line + "\n")
    return "".join(lines)


def yaml_append(text, block, key, line, rel):
    """Chèn `line` (entry `block.key`) vào text YAML; từ chối ghi đè hoặc làm đổi phần khác."""
    before = yaml.safe_load(text) or {}
    if key in (before.get(block) or {}):
        raise ScaffoldError(f"{rel}: '{block}.{key}' đã tồn tại — không ghi đè")
    new = insert_in_block(text, block, line)
    after = yaml.safe_load(new)
    expect = dict(before)
    expect[block] = {**(before.get(block) or {}), key: after.get(block, {}).get(key)}
    if after != expect or after[block][key] is None:
        raise ScaffoldError(f"{rel}: chèn '{key}' làm thay đổi phần khác của YAML — dừng")
    return new


def routing_line(spec):
    rule = {"default": spec["model_tier"]}
    if spec["model_tier"] != "opus":
        rule["escalate_to"] = "opus"
    if spec["risk_level"] == "high":
        rule["high_risk"] = "opus"
    body = ", ".join(f"{k}: {v}" for k, v in rule.items())
    return f"{spec['id']}: {{{body}}}  # factory: phase={spec['phase']} risk={spec['risk_level']}"


def zone_line(spec):
    return (f'{spec["zone"]}: {{writers: ["{spec["id"]}"], mode: owner_write}}'
            f"  # factory: {spec['id']}")
