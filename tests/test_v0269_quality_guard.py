from semantic_prompt_transfer.domain import CreditFact, EvidenceRecord, ReviewItem, SourceTier
from semantic_prompt_transfer.review import EvidenceAssembler
from semantic_prompt_transfer.verification_flow import Claim, LLMVerificationAgent, VerificationStatus
from semantic_prompt_transfer.orchestration import ReviewGenerationOrchestrator


class JsonGenerator:
    def __init__(self, value):
        self.value = value
    def generate(self, messages):
        return self.value


def test_credit_won_is_normalized_to_million():
    fact = CreditFact(
        "f", "x", "총차입금", 164134131441, "원", "2025",
        (ReviewItem.MAJOR_ACCOUNTS,), False, "d", "c.xlsx", "S", "A1"
    )
    rows = EvidenceAssembler().assemble(ReviewItem.MAJOR_ACCOUNTS, [fact], {"hits": []})
    assert "164,134.1" in rows[0].content
    assert "단위=백만원" in rows[0].content
    assert "164134131441" not in rows[0].content


def test_unsupported_causal_is_not_auto_fail():
    claim = Claim("A-001", ReviewItem.MAJOR_ACCOUNTS, "원가 부담이 확대됨.", 0, 11, (), 1)
    raw = '{"status":"FAIL","severity":"MINOR","problem_span":"원가 부담","reason_code":"UNSUPPORTED_CAUSAL","reason":"근거 부족","evidence_ids":["CR_00000000000000000001"],"repair_instruction":"삭제"}'
    evidence = EvidenceRecord(
        "CR_00000000000000000001", ReviewItem.MAJOR_ACCOUNTS,
        SourceTier.CREDIT_REPORT_ITEM, "매출액=100; 단위=백만원", "d", "c.xlsx"
    )
    result = LLMVerificationAgent(JsonGenerator(raw)).verify(claim, [evidence])
    assert result.status is VerificationStatus.WARN


def test_claim_rewrite_scope_is_downgraded():
    claim = Claim("A-001", ReviewItem.MAJOR_ACCOUNTS, "총차입금은 100백만원임.", 0, 15, (), 1)
    raw = '{"status":"FAIL","severity":"CLAIM_ERROR","problem_span":"100백만원","reason_code":"FACT_CONTRADICTION","reason":"불일치","evidence_ids":["CR_00000000000000000001"],"repair_instruction":"수정"}'
    evidence = EvidenceRecord(
        "CR_00000000000000000001", ReviewItem.MAJOR_ACCOUNTS,
        SourceTier.CREDIT_REPORT_ITEM, "총차입금=120; 단위=백만원", "d", "c.xlsx"
    )
    result = LLMVerificationAgent(JsonGenerator(raw)).verify(claim, [evidence])
    assert result.status is VerificationStatus.WARN


def test_completion_detection():
    assert not ReviewGenerationOrchestrator._looks_complete("재고 증가 등에")
    assert not ReviewGenerationOrchestrator._looks_complete("차입금 증가에 따라")
    assert ReviewGenerationOrchestrator._looks_complete("총차입금 증가 추세임")
    assert ReviewGenerationOrchestrator._looks_complete("수익성 개선됨.")
