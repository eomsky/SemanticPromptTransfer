from .chunking import PackageChunkBuilder, build_experimental_matrix
from .config import ArtifactMode, DocumentScope, IndexWriteStrategy, PipelineConfig, RepresentationLevel
from .credit_report import CreditFieldMapping, CreditReportParseResult, CreditReportParser, CreditReportTemplate
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
from .encoding import E5OnnxEncoder, EncoderBackend, EncoderRegistry
from .fewshot import FewShotRegistry, FewShotSelector
from .indexing import RAGIndex
from .operations import DocumentLifecycleService, OfflineIndexBuilder, OnlineRAGService
from .orchestration import ReviewGenerationOrchestrator, ReviewGenerationResult, ReviewValidationError
from .pipeline import RAGPipeline
from .prompting import PromptPackage, PromptPackageBuilder
from .query_profiles import QueryProfileRegistry, ReviewQueryProfile, default_query_profiles
from .registry import DocumentRecord, JobRecord, OperationalRegistry
from .review import EvidenceAssembler, ReviewPromptBuilder, ReviewPromptPackage
from .review_docx import OpinionDocumentBuilder
from .retrieval import RetrievalEngine
from .validation import OpinionValidator, ValidationIssue, ValidationReport
from .vector_store import ChromaVectorStore, InMemoryVectorStore, VectorPoint, VectorStoreBackend

__all__ = [
    "ArtifactMode",
    "CaseContext",
    "ChromaVectorStore",
    "CreditFact",
    "CreditFieldMapping",
    "CreditReportParseResult",
    "CreditReportParser",
    "CreditReportTemplate",
    "DocumentKind",
    "DocumentLifecycleService",
    "DocumentRecord",
    "E5OnnxEncoder",
    "EncoderBackend",
    "EncoderRegistry",
    "EvidenceAssembler",
    "EvidenceRecord",
    "FewShotExample",
    "FewShotRegistry",
    "FewShotSelector",
    "FileStatus",
    "InMemoryVectorStore",
    "DocumentScope",
    "IndexWriteStrategy",
    "JobRecord",
    "JobStage",
    "PackageChunkBuilder",
    "OfflineIndexBuilder",
    "OnlineRAGService",
    "OperationalRegistry",
    "OpinionDocumentBuilder",
    "OpinionValidator",
    "PipelineConfig",
    "PromptPackage",
    "PromptPackageBuilder",
    "ProgressEvent",
    "QueryProfileRegistry",
    "RAGIndex",
    "RAGPipeline",
    "ReviewGenerationOrchestrator",
    "ReviewGenerationResult",
    "ReviewItem",
    "ReviewPromptBuilder",
    "ReviewPromptPackage",
    "ReviewQueryProfile",
    "ReviewSectionDraft",
    "ReviewValidationError",
    "RepresentationLevel",
    "RetrievalEngine",
    "SourceTier",
    "ValidationIssue",
    "ValidationReport",
    "VectorPoint",
    "VectorStoreBackend",
    "build_experimental_matrix",
    "default_query_profiles",
]

__version__ = "0.20.0"
