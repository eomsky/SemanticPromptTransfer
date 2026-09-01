import json
from pathlib import Path

from semantic_prompt_transfer.credit_reasoning import CreditReasoningLayer
from semantic_prompt_transfer.domain import CaseContext, EvidenceRecord, FewShotExample, ReviewItem, SourceTier
from semantic_prompt_transfer.prompt_budget import PromptTokenBudgetManager
from semantic_prompt_transfer.review import ReviewPromptBuilder


def ev(item, eid, text, tier=SourceTier.ATTACHMENT, score=0.5, **meta):
    return EvidenceRecord(eid, item, tier, text, "doc", "report.pdf", 1, {"score": score, **meta})


def test_token_budget_caps_prompt_and_keeps_priority_evidence():
    manager = PromptTokenBudgetManager(
        max_model_tokens=5000,
        generation_reserve_tokens=500,
        completion_reserve_tokens=150,
        safety_margin_tokens=150,
        token_counter=lambda text: max(1, len(text) // 4),
    )
    builder = ReviewPromptBuilder(token_budget_manager=manager)
    item = ReviewItem.MAJOR_ACCOUNTS
    rows = [ev(item, f"ATT_{i:020x}", ("재고자산 차입금 운전자금 " if i == 0 else "기타 참고 ") + ("자료" * 140), score=0.9-i*0.02) for i in range(12)]
    blueprint = {"judgment_focus": "상환영향", "priority_evidence_ids": [rows[0].evidence_id], "priority_issues": [{"materiality":"HIGH","evidence_ids":[rows[0].evidence_id]}]}
    prompt = builder.build(CaseContext("t","c","운전자금","*"), item, "재고 차입금", rows, [], blueprint)
    assert prompt.manifest["prompt_tokens"] <= prompt.manifest["input_budget_tokens"]
    assert rows[0].evidence_id in {r["evidence_id"] for r in prompt.evidence}
    assert prompt.manifest["selection_mode"] == "token_budget_materiality_diversity"


def test_reasoning_fallback_prioritizes_material_credit_topics():
    item = ReviewItem.CASH_FLOW
    rows = [
        ev(item, "ATT_00000000000000000001", "2024년 영업활동현금흐름 100백만원, 2025년 영업활동현금흐름 500백만원, 차입금 상환재원 개선"),
        ev(item, "ATT_00000000000000000002", "기타 주석 참고"),
    ]
    portfolio = CreditReasoningLayer(None).plan(CaseContext("t","c","운전자금","*"), {r: (rows if r is item else []) for r in ReviewItem.ordered()})
    bp = portfolio.item_blueprint(item)
    assert "ATT_00000000000000000001" in bp["priority_evidence_ids"]
    assert bp["priority_issues"]


def test_prompt_contains_credit_reasoning_and_depth_instruction():
    item = ReviewItem.PROFITABILITY
    row = ev(item, "ATT_00000000000000000003", "2025년 영업이익 개선 및 원가율 하락")
    prompt = ReviewPromptBuilder().build(
        CaseContext("t","c","운전자금","*"), item, "영업이익", [row], [],
        {"judgment_focus":"수익성 지속성","priority_evidence_ids":[row.evidence_id],"priority_issues":[]},
    )
    combined = "\n".join(m["content"] for m in prompt.messages)
    assert "CREDIT_REASONING_BLUEPRINT" in combined
    assert "사실→의미→위험→완화요인→상환능력 영향" in combined
    assert "현재보다 충분히 상세하게" in combined


def test_v02610_notebook_has_expanded_context_and_role_generators():
    path = Path(__file__).resolve().parents[1] / "notebooks/SemanticPromptTransfer_v0.26.10_COLAB_LAUNCHER.ipynb"
    nb = json.loads(path.read_text(encoding="utf-8"))
    code = "\n".join("".join(c.get("source", [])) for c in nb["cells"] if c.get("cell_type") == "code")
    assert "MODEL_CONTEXT_TOKENS = 28672" in code
    assert '"--max-num-seqs", "2"' in code
    assert "max_new_tokens=3600" in code
    assert "reasoning_generator" in code and "verification_generator" in code and "completion_generator" in code
    assert "count_prompt_tokens" in code
    assert 'verification_mode="ENFORCE"' in code
    assert "136,281" in code
    assert "500,000, 2024년 575,000" not in code


def test_ui_does_not_keep_permanent_yellow_claim_background():
    html = (Path(__file__).resolve().parents[1] / "src/semantic_prompt_transfer/examples/operational/credit_review_upload_demo.html").read_text(encoding="utf-8")
    assert ".claim {" in html
    claim_css = html.split(".claim {", 1)[1].split("}", 1)[0]
    assert "background:transparent" in claim_css
    assert "#fff8ce" not in claim_css
