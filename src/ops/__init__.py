"""Bộ vận hành hằng ngày (Chief / Claude Code ở account công ty). Code deterministic.

project.py  new | run | huddle | list | status   (KHÔNG có lệnh ký gate — gate chỉ qua gate_ui, người)
report.py   daily [--redact]                      prune.py  retention event store      doctor.py  kiểm môi trường
Mọi gọi model đi qua src/gateway/llm_client.py (qua agent runner); mọi ghi đi qua Blackboard → event store.
"""
