from semantic_prompt_transfer.domain import EvidenceRecord, ReviewItem, SourceTier
from semantic_prompt_transfer.verification_flow import (
    Claim, LLMVerificationAgent, RepairCoordinator, VerificationStatus
)


class JsonGenerator:
    def __init__(self, text): self.text = text
    def generate(self, messages): return self.text


def evidence():
    return EvidenceRecord(
        evidence_id="CR_00000000000000000001", review_item=ReviewItem.MAJOR_ACCOUNTS,
        source_tier=SourceTier.CREDIT_REPORT_ITEM, content="2025년 총차입금은 314,398백만원임",
        document_id="d", source_filename="credit.xlsx", metadata={}
    )


def claim():
    text = "2025년 총차입금은 300,000백만원으로 증가함."
    return Claim("A-001", ReviewItem.MAJOR_ACCOUNTS, text, 0, len(text),
                 ("CR_00000000000000000001",), 1)


def test_clear_bound_error_can_fail():
    raw = '{"claim_id":"A-001","revision":1,"status":"FAIL","severity":"MINOR","problem_span":"300,000백만원","reason_code":"UNSUPPORTED_NUMERIC","reason":"근거 수치와 불일치","evidence_ids":["CR_00000000000000000001"],"repair_instruction":"근거 수치로 교체"}'
    result = LLMVerificationAgent(JsonGenerator(raw)).verify(claim(), [evidence()])
    assert result.status is VerificationStatus.FAIL


def test_fail_without_exact_span_is_downgraded():
    raw = '{"status":"FAIL","severity":"MINOR","problem_span":"없는문구","reason_code":"UNSUPPORTED_NUMERIC","reason":"불일치","evidence_ids":["CR_00000000000000000001"],"repair_instruction":"수정"}'
    result = LLMVerificationAgent(JsonGenerator(raw)).verify(claim(), [evidence()])
    assert result.status is VerificationStatus.WARN


def test_fail_without_bound_evidence_never_mutates():
    raw = '{"status":"FAIL","severity":"MINOR","problem_span":"300,000백만원","reason_code":"UNSUPPORTED_NUMERIC","reason":"불일치","evidence_ids":[],"repair_instruction":"수정"}'
    result = LLMVerificationAgent(JsonGenerator(raw)).verify(claim(), [evidence()])
    assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
