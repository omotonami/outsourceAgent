"""Hằng số + config của Dispatcher. Số đọc từ config/budgets.yaml → dispatcher; thiếu → ConfigError."""
from __future__ import annotations

from src.common import config
from src.common.config import ConfigError

ROOT = config.ROOT
_BUDGETS = config.load_yaml(config.BUDGETS)
_TRANS = config.load_yaml(config.TRANSITIONS)
SUBSTATES: list[str] = _TRANS["task_substates"]
CONTRACT_SCHEMA = config.load_json("specs/04_schemas/spawn_contract.json")

AGENT = "orchestrator"                       # writer của task_backlog / build_results / trace_log
PARENT = "orchestrator.dispatcher"
DONE = {"await_merge", "merged"}             # dep coi là xong (merged chỉ do G3 ngoài dispatcher)
WORKER_STATES = ("in_dev", "testing", "qc")  # worker được tự báo trong khoảng này
BLOCKED = "blocked"                          # trạng thái ngoài luồng, không thuộc task_substates
EXCLUSIVE = "*"                              # owns mặc định: giữ độc quyền repo → chạy tuần tự
DEFAULT_AGENT = "c1_dev"                     # agent/task_type mặc định khi task không khai (autonomy)
DEFAULT_TASK_TYPE = "build_task"             # khớp gate_task_types.G3 trong config/autonomy.yaml
ANTI_JOB = [
    "Không gọi Dispatcher / không spawn worker khác (depth tối đa 1)",
    "Không sửa file ngoài owns",
    "Không merge, không push, không mở PR",
    "Không tự chuyển task sang merged (chỉ sau G3 do người ký)",
]
PHASE = "build"

__all__ = ["ConfigError", "ROOT", "SUBSTATES", "CONTRACT_SCHEMA", "AGENT", "PARENT", "DONE", "WORKER_STATES",
           "BLOCKED", "EXCLUSIVE", "DEFAULT_AGENT", "DEFAULT_TASK_TYPE", "ANTI_JOB", "PHASE",
           "cfg", "worker_agents"]


def cfg(key: str, nullable: bool = False):
    """Đọc dispatcher.<key> trong config/budgets.yaml. Không có default hard-code."""
    sec = _BUDGETS.get("dispatcher")
    if not isinstance(sec, dict) or key not in sec:
        raise ConfigError(f"config/budgets.yaml: thiếu 'dispatcher.{key}'")
    v = sec[key]
    if v is None and not nullable:
        raise ConfigError(f"config/budgets.yaml: 'dispatcher.{key}' không được null")
    return v


def worker_agents() -> list[str]:
    """Agent con được chạy trong worker = subordinates của B4 (specs/02_agents/b4_tech_lead.md)."""
    subs = config.agent_spec_meta("b4_tech_lead").get("subordinates")
    if not subs:
        raise ConfigError("specs/02_agents/b4_tech_lead.md: thiếu 'subordinates'")
    return list(subs)
