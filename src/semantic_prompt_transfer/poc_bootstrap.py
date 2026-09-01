from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from .colab_runtime import EphemeralColabConfig, EphemeralColabRuntime
from .credit_report import CreditReportTemplate
from .encoding import E5OnnxEncoder, EncoderBackend
from .fewshot import FewShotRegistry, FewShotSelector
from .llm import (
    EvidenceTemplateGenerator,
    OpenAICompatibleHttpGenerator,
    RemoteGenerationConfig,
    TextGenerator,
)
from .poc_processing import PocUploadProcessor, ShardedAttachmentRetriever
from .poc_identity import PocIdentityService
from .poc_review import EphemeralReviewJobService
from .web import create_fastapi_app


@dataclass
class ColabPocBundle:
    runtime: EphemeralColabRuntime
    sessions: PocIdentityService | None
    upload_processor: PocUploadProcessor
    review_jobs: EphemeralReviewJobService
    generator: TextGenerator
    app: object
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> dict[str, object]:
        if not self._closed:
            if self.sessions is not None:
                self.sessions.close()
            self._closed = True
        return self.runtime.close(purge=True)


def _example_path(filename: str) -> Path:
    return Path(str(files("semantic_prompt_transfer.examples.operational").joinpath(filename)))


def build_colab_poc(
    *,
    model_dir: str | Path,
    access_code: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str = "local-credit-review-model",
    llm_api_key: str | None = None,
    root: str | Path = "/content/spt_poc_runtime",
    credit_template_path: str | Path | None = None,
    few_shot_path: str | Path | None = None,
    demo_credit_report_path: str | Path | None = None,
    demo_attachment_paths: tuple[str | Path, ...] = (),
    allowed_origins: tuple[str, ...] = (),
    session_ttl_seconds: int = 4 * 60 * 60,
    runtime_lifetime_seconds: int = 12 * 60 * 60,
    encoder: EncoderBackend | None = None,
    generator: TextGenerator | None = None,
    require_content_root: bool = True,
    anonymous_access: bool = False,
    verification_mode: str = "OFF",
) -> ColabPocBundle:
    runtime = EphemeralColabRuntime(
        EphemeralColabConfig(
            root=root,
            max_lifetime_seconds=runtime_lifetime_seconds,
            require_content_root=require_content_root,
            clean_start=True,
        )
    )
    sessions: PocIdentityService | None = None
    try:
        embedding_encoder = encoder or E5OnnxEncoder(model_dir)
        template = CreditReportTemplate.from_json(
            credit_template_path or _example_path("credit_report_template.json")
        )
        few_shots = FewShotSelector(
            FewShotRegistry.from_json(few_shot_path or _example_path("few_shots.json"))
        )
        primary_generator: TextGenerator | None = None
        if generator is not None:
            # Review generation streams directly from the primary LLM. Semantic/factual checking
            # belongs exclusively to the post-generation VerificationAgent.
            primary_generator = generator
            text_generator: TextGenerator = generator
        elif llm_base_url:
            primary_generator = OpenAICompatibleHttpGenerator(
                RemoteGenerationConfig(
                    base_url=llm_base_url,
                    model=llm_model,
                    api_key=llm_api_key,
                )
            )
            text_generator = primary_generator
        else:
            # Offline/no-LLM compatibility only. The operating Colab always supplies a primary LLM.
            text_generator = EvidenceTemplateGenerator()
        upload_processor = PocUploadProcessor(
            embedding_encoder,
            runtime.vectors,
            runtime.artifacts,
            credit_template=template,
        )
        retriever = ShardedAttachmentRetriever(embedding_encoder, runtime.vectors)
        review_jobs = EphemeralReviewJobService(
            runtime,
            retriever,
            few_shots,
            text_generator,
            upload_processor,
            verification_mode=verification_mode,
            verification_generator=primary_generator,
        )
        sessions = None if anonymous_access else PocIdentityService(
            runtime.root / "metadata" / "identity.sqlite",
            ttl_seconds=session_ttl_seconds,
        )
        app = create_fastapi_app(
            runtime.application,
            runtime.artifacts,
            upload_processor,
            review_jobs=review_jobs,
            session_manager=sessions,
            runtime_health=runtime.health,
            purge_case=runtime.purge_case,
            allowed_origins=allowed_origins,
            download_root=runtime.root,
            credit_template_download=_example_path("credit_report_sample_template.xlsx"),
            demo_credit_report_path=demo_credit_report_path,
            demo_attachment_paths=demo_attachment_paths,
        )
        return ColabPocBundle(
            runtime,
            sessions,
            upload_processor,
            review_jobs,
            text_generator,
            app,
        )
    except Exception:
        if sessions is not None:
            sessions.close()
        runtime.close(purge=True)
        raise


def build_colab_poc_from_env() -> ColabPocBundle:
    required = [name for name in ("SPT_MODEL_DIR",) if not os.environ.get(name)]
    if required:
        raise RuntimeError("missing required environment variables: " + ", ".join(required))
    origins = tuple(
        value.strip()
        for value in os.environ.get("SPT_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    )
    demo_attachments = tuple(
        value.strip()
        for value in os.environ.get("SPT_DEMO_ATTACHMENTS", "").split(",")
        if value.strip()
    )
    return build_colab_poc(
        model_dir=os.environ["SPT_MODEL_DIR"],
        llm_base_url=os.environ.get("SPT_LLM_BASE_URL"),
        llm_model=os.environ.get("SPT_LLM_MODEL", "local-credit-review-model"),
        root=os.environ.get("SPT_POC_ROOT", "/content/spt_poc_runtime"),
        credit_template_path=os.environ.get("SPT_CREDIT_TEMPLATE"),
        few_shot_path=os.environ.get("SPT_FEW_SHOTS"),
        demo_credit_report_path=os.environ.get("SPT_DEMO_CREDIT_REPORT"),
        demo_attachment_paths=demo_attachments,
        allowed_origins=origins,
        verification_mode=os.environ.get("SPT_VERIFICATION_MODE", "ENFORCE"),
        session_ttl_seconds=int(os.environ.get("SPT_SESSION_TTL_SECONDS", str(4 * 60 * 60))),
        runtime_lifetime_seconds=int(
            os.environ.get("SPT_RUNTIME_LIFETIME_SECONDS", str(12 * 60 * 60))
        ),
    )
