# Hiến pháp — outsourcing-agents

## Bản đồ thư mục
- `specs/`     Con người viết. NGUỒN SỰ THẬT. Sửa hành vi agent = sửa ở đây.
- `knowledge/` Người dùng thêm kinh nghiệm (team/personal). Chỉ THÊM, không nới specs.
- `src/`       Mày (Claude Code) sinh. Code deterministic + agent runner.
- `data/ eval/ tests/`  Dữ liệu, đánh giá, test.

## Luật tuyệt đối
1. Chỉ `src/agents/` chứa logic LLM. Mọi thứ khác deterministic.
2. Mọi lần gọi model đi qua `src/gateway/llm_client.py`. Không import SDK provider chỗ khác.
3. KHÔNG sửa `specs/` nếu tao không yêu cầu rõ. Agent đọc specs, không hard-code lại.
4. KHÔNG nới lỏng guard trong `src/orchestrator/gates.py` để test pass.
5. knowledge/ chỉ chèn vào "MODULE 3 / Additional guidance". Không đụng Module 1/2/4 hay Forbidden.
6. Con số (effort, rate) nằm trong `data/`, không hard-code trong code hay skill.
7. Output agent gắn nhãn FACT / ASSUMPTION / RECOMMENDATION; FACT phải có nguồn.
8. Mỗi tính năng mới kèm test trong `tests/`. Chạy `pytest -q` trước khi báo xong.

## Mỗi agent
- Đặc tả (card + prompt 4 module + I/O schema): `specs/02_agents/<id>.md`
- Code runner (1 file): `src/agents/<id>.py`
- Schema I/O: `specs/04_schemas/<id>.in.json` / `.out.json`
