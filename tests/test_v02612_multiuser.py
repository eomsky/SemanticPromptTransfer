from __future__ import annotations

import threading
import time

from semantic_prompt_transfer.domain import CaseContext, EvidenceRecord, ReviewItem, SourceTier
from semantic_prompt_transfer.fewshot import FewShotRegistry, FewShotSelector
from semantic_prompt_transfer.orchestration import ReviewGenerationOrchestrator
from semantic_prompt_transfer.poc_scheduler import FairShareReviewScheduler
from semantic_prompt_transfer.review import ReviewPromptBuilder


class EmptyRetriever:
    def search(self, query, **kwargs):
        return {"query": query, "hits": []}


def test_fair_share_rotates_waiting_jobs():
    scheduler = FairShareReviewScheduler(parallel_quanta=1, idle_timeout_seconds=10)
    scheduler.register("j1")
    scheduler.register("j2")
    order = []
    first_ready = threading.Event()

    def first():
        with scheduler.quantum("j1", "generation"):
            order.append("j1")
            first_ready.set()
            time.sleep(0.08)

    def second():
        first_ready.wait(1)
        with scheduler.quantum("j2", "generation"):
            order.append("j2")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start(); t2.start(); t1.join(2); t2.join(2)
    assert order == ["j1", "j2"]


def test_idle_job_suspends_and_touch_resumes():
    now = [0.0]
    scheduler = FairShareReviewScheduler(parallel_quanta=1, idle_timeout_seconds=30, clock=lambda: now[0])
    scheduler.register("j")
    now[0] = 31.0
    assert scheduler.state("j").status == "SUSPENDED"
    assert scheduler.touch("j").status == "RUNNABLE"


def test_final_opinion_strips_accidental_evidence_table():
    raw = """신용조사서 근거 요약 · 재무제표 주요계정\n| 구분 | 2024-12 | 2025-12 | 2026-03 |\n|---|---:|---:|---:|\n| 재고자산 | 1,000 | 1,200 | - |\n\n재고자산은 전년 대비 증가하여 운전자금 점유 부담을 점검할 필요가 있음."""
    cleaned = ReviewGenerationOrchestrator._strip_non_opinion_scaffolding(raw)
    assert "근거 요약" not in cleaned
    assert "|" not in cleaned
    assert "운전자금" in cleaned


def test_prompt_forbids_tables_and_dash_fillers():
    builder = ReviewPromptBuilder()
    case = CaseContext("t", "c", "운전자금", "*")
    evidence = [EvidenceRecord("CR_" + "a" * 20, ReviewItem.MAJOR_ACCOUNTS, SourceTier.CREDIT_REPORT_ITEM, "재고자산 1,000백만원", "d")]
    prompt = builder.build(case, ReviewItem.MAJOR_ACCOUNTS, "재고", evidence, [])
    system = prompt.messages[0]["content"]
    assert "표·마크다운 표" in system
    assert "대시(-)" in system
