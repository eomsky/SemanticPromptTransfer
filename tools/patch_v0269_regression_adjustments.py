from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The legacy v0.26 notebook test must keep its historical 1400-token expectation.
path = ROOT / "tests" / "test_colab_poc.py"
text = path.read_text(encoding="utf-8")
text = text.replace("max_new_tokens=1800", "max_new_tokens=1400")
path.write_text(text, encoding="utf-8")

# v0.26.7 originally allowed unsupported numerics to mutate. v0.26.9 deliberately
# downgrades unsupported claims to WARN; a hard direct factual contradiction remains
# the supported automatic FAIL case.
path = ROOT / "tests" / "test_v0267_llm_verifier.py"
text = path.read_text(encoding="utf-8")
text = text.replace("UNSUPPORTED_NUMERIC", "FACT_CONTRADICTION")
path.write_text(text, encoding="utf-8")

print("v0.26.9 regression adjustments complete")
