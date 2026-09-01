from pathlib import Path

import numpy as np

from semantic_prompt_transfer.domain import EvidenceRecord, ReviewItem, SourceTier
from semantic_prompt_transfer.orchestration import ReviewGenerationOrchestrator
from semantic_prompt_transfer.poc_processing import ShardedAttachmentRetriever
from semantic_prompt_transfer.review import _sanitize_style_text
from semantic_prompt_transfer.review_docx import OpinionDocumentBuilder
from semantic_prompt_transfer.verification_flow import ClaimSegmenter, LLMVerificationAgent, VerificationStatus


def test_decimal_and_date_dots_do_not_split_claims():
    text = "매출채권은 전년 대비 58.3% 증가하였고 2026.03.23 기준 유동성은 양호함. 다음 판단임."
    claims = ClaimSegmenter().segment(ReviewItem.MAJOR_ACCOUNTS, text)
    assert len(claims) == 2
    assert "58.3%" in claims[0].text
    assert "2026.03.23" in claims[0].text


def test_style_mask_has_no_bracket_value_placeholder():
    value = _sanitize_style_text("2025년 매출액 123,456백만원, (주)ABC")
    assert "[VALUE]" not in value
    assert "[PERIOD]" not in value
    assert "수치" in value


def test_orchestrator_strips_residual_style_placeholder_lines():
    value = ReviewGenerationOrchestrator._strip_style_placeholder_leakage(
        "정상 판단임.\n총차입금 [VALUE] [VALUE]\n추가 판단임."
    )
    assert "[VALUE]" not in value
    assert "총차입금" not in value
    assert "정상 판단임" in value and "추가 판단임" in value


def test_word_picture_fit_preserves_aspect_ratio_and_bounds():
    width, height = OpinionDocumentBuilder._fit_picture_dimensions(1600, 3200, 16.0, 20.0)
    assert width <= 16.0 and height <= 20.0
    assert round(height / width, 4) == 2.0


class BatchEncoder:
    def __init__(self):
        self.calls = 0
    def encode_queries(self, texts):
        self.calls += 1
        return np.ones((len(list(texts)), 3), dtype=np.float32)


class EmptyStore:
    def search(self, embedding, top_k, filters):
        return []


def test_retriever_batches_all_queries_in_one_encoder_call():
    encoder = BatchEncoder()
    retriever = ShardedAttachmentRetriever(encoder, EmptyStore())
    rows = retriever.search_many(["A", "B", "C", "D", "E"], filters={"tenant_id": "t", "case_id": "c"})
    assert len(rows) == 5
    assert encoder.calls == 1


class BatchJsonGenerator:
    def __init__(self):
        self.calls = 0
    def generate(self, messages):
        self.calls += 1
        return '{"findings":[{"claim_id":"A-001","revision":1,"status":"PASS","severity":"MINOR","problem_span":"","reason_code":"","reason":"","evidence_ids":[],"repair_instruction":""},{"claim_id":"A-002","revision":1,"status":"PASS","severity":"MINOR","problem_span":"","reason_code":"","reason":"","evidence_ids":[],"repair_instruction":""}]}'

def test_verifier_batches_section_claims_in_one_llm_call():
    generator = BatchJsonGenerator()
    verifier = LLMVerificationAgent(generator)
    claims = ClaimSegmenter().segment(ReviewItem.MAJOR_ACCOUNTS, "첫 문장임. 둘째 문장임.")
    findings = verifier.verify_many(claims, [[], []])
    assert generator.calls == 1
    assert len(findings) == 2
    assert all(row.status is VerificationStatus.PASS for row in findings)


def test_frontend_no_longer_resplits_decimal_sentences_or_shows_embedding_wait():
    source = Path('src/semantic_prompt_transfer/examples/operational/credit_review_upload_demo.html').read_text(encoding='utf-8')
    assert "match(/[^.!?\\n]+" not in source
    assert "임베딩 대기" not in source
    assert "file.status=event.file_status" in source


def test_background_upload_and_overlap_pipeline_are_present():
    web = Path('src/semantic_prompt_transfer/web.py').read_text(encoding='utf-8')
    orchestration = Path('src/semantic_prompt_transfer/orchestration.py').read_text(encoding='utf-8')
    assert "background_tasks.add_task(process_upload" in web
    assert "ThreadPoolExecutor(max_workers=1" in orchestration
    assert '"verification_pipeline": "section_batch_overlapped"' in orchestration
