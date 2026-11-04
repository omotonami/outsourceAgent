"""DAG task + vùng `owns` (Code thuần): thứ tự topo, phát hiện vòng, giao nhau owns."""
from __future__ import annotations

import fnmatch
import re

from .settings import EXCLUSIVE


class CycleError(ValueError):
    pass


def check_dag(tasks: list[dict]) -> list[str]:
    """Kiểm DAG; trả thứ tự topo. Id trùng / dep không tồn tại / vòng → CycleError."""
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise CycleError("task id trùng")
    known = set(ids)
    indeg = {i: 0 for i in ids}
    children: dict[str, list[str]] = {i: [] for i in ids}
    for t in tasks:
        for d in t.get("deps") or []:
            if d not in known:
                raise CycleError(f"{t['id']} phụ thuộc task không tồn tại: {d}")
            indeg[t["id"]] += 1
            children[d].append(t["id"])
    queue = [i for i in ids if indeg[i] == 0]
    order = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(ids):
        raise CycleError(f"deps có vòng: {sorted(set(ids) - set(order))}")
    return order


_GLOB = re.compile(r"[*?\[]")


def _norm(p: str) -> str:
    p = p.strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_overlap(x: str, y: str) -> bool:
    x, y = _norm(x), _norm(y)
    gx, gy = _GLOB.search(x), _GLOB.search(y)
    if gx or gy:
        # glob: thận trọng — so phần tĩnh trước ký tự glob đầu tiên (có thể báo giao thừa, không sót)
        if (gx and fnmatch.fnmatchcase(y, x)) or (gy and fnmatch.fnmatchcase(x, y)):
            return True
        px = x[:gx.start()] if gx else x
        py = y[:gy.start()] if gy else y
        return px.startswith(py) or py.startswith(px)
    return x == y or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/")


def _overlap(a: list[str], b: list[str]) -> bool:
    if EXCLUSIVE in a or EXCLUSIVE in b:
        return True
    return any(_path_overlap(x, y) for x in a for y in b)


def default_owns(task: dict) -> list[str]:
    """`owns` từ task_backlog (field của B4, specs/04_schemas/b4_tech_lead.out.json).
    Thiếu/rỗng → ["*"] = độc quyền (an toàn, chạy tuần tự)."""
    return list(task.get("owns") or [EXCLUSIVE])


def owns_conflicts(tasks: list[dict]) -> list[tuple[str, str]]:
    """Cặp task có thể chạy song song (không task nào là tổ tiên của task kia) mà owns giao nhau.
    Kiểm Done-when của B4 bằng Code; task thiếu owns bị bỏ qua (Dispatcher đã chạy tuần tự)."""
    check_dag(tasks)
    deps = {t["id"]: set(t.get("deps") or []) for t in tasks}
    anc: dict[str, set[str]] = {}

    def ancestors(i):
        if i not in anc:
            anc[i] = set()
            for d in deps[i]:
                anc[i] |= {d} | ancestors(d)
        return anc[i]

    out = []
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            if not a.get("owns") or not b.get("owns"):
                continue
            if a["id"] in ancestors(b["id"]) or b["id"] in ancestors(a["id"]):
                continue
            if _overlap(a["owns"], b["owns"]):
                out.append((a["id"], b["id"]))
    return out
