from __future__ import annotations

import tempfile, zipfile
from pathlib import Path

from semantic_prompt_transfer.chat_routing import ChatIntent, ChatIntentRouter
from semantic_prompt_transfer.domain import CaseContext, EvidenceRecord, ReviewItem, ReviewSectionDraft, SourceTier
from semantic_prompt_transfer.evidence_trace import EvidenceTraceLedger
from semantic_prompt_transfer.review_docx import OpinionDocumentBuilder
from semantic_prompt_transfer.verification_flow import VerificationMode


def test_chat_router_separates_general_case_and_opinion():
    router = ChatIntentRouter()
    assert router.route("안녕") is ChatIntent.GENERAL
    assert router.route("심사에 주의할점은") is ChatIntent.GENERAL
    assert router.route("이 회사 심사에서 주의할 점은?") is ChatIntent.CASE_QA
    assert router.route("아까 C항목 의견은 왜 그렇게 판단했어?") is ChatIntent.OPINION_QA

def test_same_visual_chunks_collapse_to_one_reference():
    item = ReviewItem.FINANCIAL_STABILITY
    rows = [EvidenceRecord(evidence_id=f"ATT_{i:020x}", review_item=item, source_tier=SourceTier.ATTACHMENT, content=f"chunk {i}", document_id="doc-1", source_filename="report.pdf", page=104, metadata={"logical_table_id":"cashflow-table","bbox":[100,100+i*10,500,140+i*10]}) for i in range(1,4)]
    refs = EvidenceTraceLedger().register(item, rows, [row.evidence_id for row in rows])
    assert len(refs) == 1 and len(refs[0].member_ids) == 3 and len(refs[0].highlight_bboxes) == 3

def test_docx_header_keeps_case_only():
    item = ReviewItem.MAJOR_ACCOUNTS; eid = "CR_00000000000000000001"
    ref = {"ref_no":1,"representative_id":eid,"member_ids":[eid],"source_class":"credit_report","source_filename":"credit.xlsx","page":None,"location":"Sheet1 · A1:B2","review_items":["A"],"highlight_bboxes":[],"highlight_cell_ranges":["A1:B2"]}
    sections = [ReviewSectionDraft(item, f"테스트 심사의견 [{eid}]", (eid,), {"verification_mode":VerificationMode.OFF.value}, (ref,))]
    sections += [ReviewSectionDraft(other, "테스트", (), {}, ()) for other in list(ReviewItem.ordered())[1:]]
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)/"opinion.docx"; OpinionDocumentBuilder().build(CaseContext("t","case-1","운전자금","*"), sections, target)
        with zipfile.ZipFile(target) as z: xml = z.read("word/document.xml").decode("utf-8")
        assert "심사건" in xml and "여신유형" not in xml and "산업분류" not in xml and "대상기업" not in xml and "근거 1" in xml
