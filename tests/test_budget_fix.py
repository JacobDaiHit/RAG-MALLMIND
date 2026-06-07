"""Test budget extraction after fix."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv()

from rag.recommendation.tool_router import extract_budget
from rag.recommendation.recommendation_pipeline import extract_price_range, extract_exclusions

tests = [
    "有没有2000到5000的护肤品",
    "不要超过3000的耳机",
    "500元以下的零食",
    "8000以下的手机",
    "不要辣的",
]

print("extract_budget:")
for q in tests:
    print(f"  {q}: {extract_budget(q)}")

print("\nextract_price_range:")
for q in tests:
    print(f"  {q}: {extract_price_range(q)}")

print("\nextract_exclusions:")
for q in tests:
    excl, brands = extract_exclusions(q)
    print(f"  {q}: terms={excl}, brands={brands}")
