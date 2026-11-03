"""Chạy THẬT một agent (mặc định A1 BU) với API key, in model + token + chi phí + ghi Blackboard.
Cần: pip install anthropic ; export ANTHROPIC_API_KEY=sk-...
Chạy: python -m src.demo.live_one
"""
import os, sys, json
from src.blackboard.store import Blackboard
from src.agents import a1_bu

BRIEF = ("NeoPay — ví điện tử Singapore (chịu MAS). Cần app mobile + portal admin cho "
         "chuyển tiền quốc tế: KYC, liên kết thẻ, gửi tiền ra nước ngoài, lịch sử giao dịch, "
         "thông báo đẩy; admin cần dashboard đối soát. Tích hợp cổng FX bên thứ ba (chưa chốt). "
         "Go-live kỳ vọng 5 tháng.")

def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("THIẾU ANTHROPIC_API_KEY. export ANTHROPIC_API_KEY=sk-... rồi chạy lại.")
        sys.exit(1)
    b = Blackboard("LIVE-NeoPay")
    b.write("lead_brief", {"client":"NeoPay","source":"email","received_at":"2026-10-25"}, agent="intake")
    ctx = {"project_id":"LIVE-NeoPay","risk_level":"normal"}
    inp = {"project_id":"LIVE-NeoPay","lead_brief":{"text":BRIEF}}
    print("Gọi A1 BU thật... (xem eval/runs/trace.jsonl để biết model + token)")
    res = a1_bu.run(b, user_id=None, ctx=ctx, inp=inp)
    if res.get("ok"):
        print("\nKẾT QUẢ (đã ghi Blackboard → business_context):")
        print(json.dumps(res["data"], ensure_ascii=False, indent=2))
        print("\nskill versions dùng:", res["skill_versions"], " | tier:", res["tier"])
        # đọc dòng trace cuối
        try:
            last = open("eval/runs/trace.jsonl",encoding="utf-8").read().strip().splitlines()[-1]
            t = json.loads(last)
            print(f"TRACE: model={t['model']} tokens={t['usage'].get('total_tokens')} phase={t['phase']}")
        except Exception:
            pass
    else:
        print("Lỗi:", res)

if __name__ == "__main__":
    main()
