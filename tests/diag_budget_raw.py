"""Diagnostic: capture raw LLM router output for budget case #125."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import send_chat_stream

def main():
    query = "3000到5000之间的手机"
    session_id = "budget_raw_125_v2"
    resp = send_chat_stream(query, session_id=session_id)

    for evt in resp["events"]:
        etype = evt.get("event", "")
        edata = evt.get("data", {})
        if etype == "tool_call":
            rt = edata.get("routing_trace", {})

            # LLM router raw output
            llm_raw = rt.get("llm", {})
            print("=== LLM Router Raw Output ===")
            print(json.dumps(llm_raw, ensure_ascii=False, indent=2))

            # Local router
            local_raw = rt.get("local", {})
            print("\n=== Local Router Output ===")
            print(json.dumps(local_raw, ensure_ascii=False, indent=2))

            # Final
            final = rt.get("final", {})
            print("\n=== Final Route ===")
            print(json.dumps(final, ensure_ascii=False, indent=2))

            # Key fields
            args = edata.get("arguments", {})
            print(f"\n=== Extracted Budget Fields ===")
            print(f"  budget:    {args.get('budget')}")
            print(f"  price_min: {args.get('price_min')}")
            print(f"  price_max: {args.get('price_max')}")
            print(f"  category:  {args.get('category')}")
            print(f"  source:    {rt.get('router_final_source')}")

            # Check if LLM report shows the prompt
            llm_report = llm_raw.get("llm_report", {})
            print(f"\n=== LLM Report ===")
            print(json.dumps(llm_report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
