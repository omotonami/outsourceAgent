"""Validate output agent theo schema Blackboard + bắt buộc nhãn FACT có nguồn."""
from jsonschema import validate as _v


def validate(value, schema):
    _v(value, schema)


def facts_have_source(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("label") == "FACT" and not i.get("source")]
