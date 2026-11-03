"""Tổng effort từ WBS. Số đến từ historical DB, không từ LLM."""


def total(wbs: list[dict]) -> float:
    return round(sum(i["effort_md"] for i in wbs), 2)
