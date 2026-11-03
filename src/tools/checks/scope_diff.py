"""Dev chỉ được sửa file trong scope task."""
from fnmatch import fnmatch


def out_of_scope(files_touched: list[str], allowed_globs: list[str]) -> list[str]:
    return [f for f in files_touched if not any(fnmatch(f, g) for g in allowed_globs)]
