from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .colab_runtime import EphemeralColabRuntime
from .domain import CaseContext, CreditFact, DocumentKind, FileStatus
from .fewshot import FewShotSelector
from .llm import TextGenerator
from .orchestration import ReviewGenerationOrchestrator, ReviewGenerationResult
from .poc_processing import ShardedAttachmentRetriever


class PocCreditFactRepository:
    def __init__(self, runtime: EphemeralColabRuntime) -> None:
        self.runtime = runtime

    def load(self, tenant_id: str, case_id: str) -> list[CreditFact]:
        candidates = [
            row
            for row in self.runtime.registry.list_documents(tenant_id, case_id)
            if row.document_kind is DocumentKind.CREDIT_REPORT and row.status is FileStatus.READY
        ]
        if len(candidates) != 1:
            raise ValueError("exactly one ready credit report is required")
        document = candidates[0]
        if not document.derived_uri:
            raise RuntimeError("credit report derived path is missing")
        path = Path(document.derived_uri) / "credit_facts.json"
        if not path.is_file():
            raise RuntimeError("credit report facts are unavailable")
        value = json.loads(path.read_text(encoding="utf-8"))
        return [CreditFact.from_dict(row) for row in value.get("facts", [])]


class EphemeralReviewJobService:
    """Create quickly, execute in a FastAPI background task, then expose a DOCX."""

    def __init__(
        self,
        runtime: EphemeralColabRuntime,
        retriever: ShardedAttachmentRetriever,
        few_shots: FewShotSelector,
        generator: TextGenerator,
        *,
        loan_type: str = "운전자금",
        industry_code: str = "*",
        company_name: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.facts = PocCreditFactRepository(runtime)
        self.generator = generator
        self.loan_type = loan_type
        self.industry_code = industry_code
        self.company_name = company_name
        self.orchestrator = ReviewGenerationOrchestrator(
            retriever,
            few_shots,
            registry=runtime.registry,
            llm=generator,
        )

    def start(self, tenant_id: str, case_id: str) -> dict[str, object]:
        documents = self.runtime.registry.list_documents(tenant_id, case_id)
        if not documents:
            raise ValueError("uploaded documents are required")
        incomplete = [row.filename for row in documents if row.status is not FileStatus.READY]
        if incomplete:
            raise ValueError("all uploads must be ready: " + ", ".join(incomplete))
        self.facts.load(tenant_id, case_id)
        return self.runtime.registry.create_job(tenant_id, case_id).to_dict()

    def run(self, job_id: str) -> ReviewGenerationResult:
        job = self.runtime.registry.get_job(job_id)
        case = CaseContext(
            job.tenant_id,
            job.case_id,
            self.loan_type,
            self.industry_code,
            self.company_name,
        )
        output = self.runtime.review_output_path(job.tenant_id, job.case_id, job_id)
        return self.orchestrator.generate(
            case,
            self.facts.load(job.tenant_id, job.case_id),
            None,
            output,
            job_id=job_id,
        )

