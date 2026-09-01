from .chunking import PackageChunkBuilder, build_experimental_matrix
from .config import ArtifactMode, DocumentScope, IndexWriteStrategy, PipelineConfig, RepresentationLevel
from .credit_report import CreditFieldMapping, CreditReportParseResult, CreditReportParser, CreditReportTemplate
from .credit_reasoning import CreditReasoningLayer, ReasoningPortfolio
from .domain import (
    CaseContext,
    CreditFact,
    DocumentKind,
    EvidenceRecord,
    FewShotExample,
    FileStatus,
    JobStage,
    ProgressEvent,
    ReviewItem,
    ReviewSectionDraft,
    SourceTier,
)
from .encoding import E5GpuEncoder, E5OnnxEncoder, EncoderBackend, EncoderRegistry
from .fewshot import FewShotRegistry, FewShotSelector
from .indexing import RAGIndex
from .application import OperationalApplicationService
from .colab_runtime import EphemeralColabConfig, EphemeralColabRuntime
from .llm import (
    CpuGenerationConfig,
    EvidenceTemplateGenerator,
    OpenAICompatibleHttpGenerator,
    RemoteGenerationConfig,
    TextGenerator,
    StreamingTextGenerator,
    MultimodalGenerationConfig,
    TransformersMultimodalGenerator,
    TransformersCpuGenerator,
    default_cpu_generator,
)
from .operations import DocumentLifecycleService, OfflineIndexBuilder, OnlineRAGService
from .orchestration import ReviewGenerationOrchestrator, ReviewGenerationResult, ReviewValidationError
from .pipeline import RAGPipeline
from .prompting import PromptPackage, PromptPackageBuilder
from .prompt_budget import PromptTokenBudgetManager, PromptBudgetSnapshot
from .query_profiles import QueryProfileRegistry, ReviewQueryProfile, default_query_profiles
from .registry import DocumentRecord, JobRecord, OperationalRegistry
from .review import EvidenceAssembler, ReviewPromptBuilder, ReviewPromptPackage
from .evidence_capture import EvidenceCaptureService
from .review_docx import OpinionDocumentBuilder
from .storage import ArtifactDeletionResult, DocumentArtifactStore, LocalDocumentArtifactStore
from .poc_bootstrap import ColabPocBundle, build_colab_poc, build_colab_poc_from_env
from .poc_identity import PocIdentityService
from .poc_processing import (
    ExtractedBlock,
    PocDocumentExtractor,
    PocUploadProcessor,
    ShardedAttachmentRetriever,
)
from .poc_review import EphemeralReviewJobService, PocCreditFactRepository
from .poc_session import PocSession, PocSessionGrant, PocSessionManager
from .retrieval import RetrievalEngine
from .validation import OpinionValidator, ValidationIssue, ValidationReport
from .vector_store import (
    ChromaVectorStore,
    InMemoryVectorStore,
    ShardedNpzVectorStore,
    VectorPoint,
    VectorStoreBackend,
)
from .web import ReviewJobStarter, UploadProcessor, create_fastapi_app

__all__ = [
    "PromptBudgetSnapshot",
    "PromptTokenBudgetManager",
    "ReasoningPortfolio",
    "CreditReasoningLayer",
    "ArtifactMode",
    "ArtifactDeletionResult",
    "CaseContext",
    "ChromaVectorStore",
    "CreditFact",
    "CreditFieldMapping",
    "CreditReportParseResult",
    "CreditReportParser",
    "CreditReportTemplate",
    "ColabPocBundle",
    "DocumentKind",
    "DocumentLifecycleService",
    "DocumentArtifactStore",
    "DocumentRecord",
    "EphemeralColabConfig",
    "EphemeralColabRuntime",
    "EphemeralReviewJobService",
    "E5OnnxEncoder",
    "E5GpuEncoder",
    "EncoderBackend",
    "EncoderRegistry",
    "EvidenceAssembler",
    "EvidenceCaptureService",
    "EvidenceRecord",
    "EvidenceTemplateGenerator",
    "ExtractedBlock",
    "FewShotExample",
    "FewShotRegistry",
    "FewShotSelector",
    "FileStatus",
    "InMemoryVectorStore",
    "DocumentScope",
    "IndexWriteStrategy",
    "JobRecord",
    "JobStage",
    "LocalDocumentArtifactStore",
    "PackageChunkBuilder",
    "OfflineIndexBuilder",
    "OnlineRAGService",
    "OpenAICompatibleHttpGenerator",
    "OperationalRegistry",
    "OperationalApplicationService",
    "OpinionDocumentBuilder",
    "OpinionValidator",
    "PACKAGE_VERSION",
    "PipelineConfig",
    "PromptPackage",
    "PromptPackageBuilder",
    "ProgressEvent",
    "PocCreditFactRepository",
    "PocDocumentExtractor",
    "PocIdentityService",
    "PocSession",
    "PocSessionGrant",
    "PocSessionManager",
    "PocUploadProcessor",
    "QueryProfileRegistry",
    "RAGIndex",
    "RAGPipeline",
    "ReviewGenerationOrchestrator",
    "ReviewGenerationResult",
    "ReviewItem",
    "ReviewJobStarter",
    "ReviewPromptBuilder",
    "ReviewPromptPackage",
    "ReviewQueryProfile",
    "ReviewSectionDraft",
    "ReviewValidationError",
    "RemoteGenerationConfig",
    "RepresentationLevel",
    "RetrievalEngine",
    "SourceTier",
    "ShardedAttachmentRetriever",
    "ShardedNpzVectorStore",
    "TextGenerator",
    "StreamingTextGenerator",
    "MultimodalGenerationConfig",
    "TransformersMultimodalGenerator",
    "TransformersCpuGenerator",
    "UploadProcessor",
    "CpuGenerationConfig",
    "ValidationIssue",
    "ValidationReport",
    "VectorPoint",
    "VectorStoreBackend",
    "build_experimental_matrix",
    "build_colab_poc",
    "build_colab_poc_from_env",
    "default_query_profiles",
    "default_cpu_generator",
    "create_fastapi_app",
    "__version__",
]

from .version import PACKAGE_VERSION, __version__
