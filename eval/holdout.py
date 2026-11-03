"""Holdout: loại dự án đang test khỏi mọi nguồn agent tra cứu."""


def excluded_ids(test_project_id: str, final_holdout: set[str]) -> set[str]:
    return {test_project_id} | set(final_holdout)
