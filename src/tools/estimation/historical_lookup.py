"""Tra dự án tương tự, có hỗ trợ loại holdout khi chạy eval."""


def similar(projects: list[dict], domain: str, modules: set[str], exclude_ids: set[str] = frozenset(), k: int = 5):
    pool = [p for p in projects if p["id"] not in exclude_ids]
    scored = sorted(pool, key=lambda p: (p["domain"] == domain, len(modules & set(p["modules"]))), reverse=True)
    return scored[:k]
