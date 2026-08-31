from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import DocumentScope, PipelineConfig
from .credit_report import CreditReportParser, CreditReportTemplate
from .domain import ReviewItem
from .fewshot import FewShotRegistry, FewShotSelector
from .indexing import RAGIndex
from .pipeline import RAGPipeline


def _write(value: dict[str, Any], output: str | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _filters(args: argparse.Namespace) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "tenant_id": getattr(args, "tenant_id", None),
            "case_id": getattr(args, "case_id", None),
            "document_id": getattr(args, "document_id", None),
            "financial_scope": getattr(args, "financial_scope", None),
        }.items()
        if value
    }


def _add_serving_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--representation-level", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--document-id")
    parser.add_argument("--financial-scope")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spt-rag", description="SemanticPromptTransfer Cells 4-7")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="offline MASTER -> persistent RAG index")
    index.add_argument("--master", required=True)
    index.add_argument("--index", required=True)
    index.add_argument("--model-dir", required=True)
    index.add_argument("--tenant-id", required=True)
    index.add_argument("--case-id", required=True)
    index.add_argument("--document-id", required=True)
    index.add_argument("--financial-scope", default="unspecified")
    index.add_argument("--source-version")
    index.add_argument("--source-filename")
    index.add_argument("--document-kind", choices=("credit_report", "attachment"), default="attachment")
    index.add_argument("--loan-type")
    index.add_argument("--industry-code")
    index.add_argument("--representation-level", type=int, choices=(0, 1, 2), default=0)
    index.add_argument("--write-strategy", choices=("REPLACE", "UPSERT"), default="REPLACE")
    index.add_argument("--output")

    retrieve = sub.add_parser("retrieve", help="online query against a loaded index")
    _add_serving_arguments(retrieve)
    retrieve.add_argument("--query", required=True)

    prompt = sub.add_parser("prompt", help="retrieve and build an evidence prompt")
    _add_serving_arguments(prompt)
    prompt.add_argument("--query-id", required=True)
    prompt.add_argument("--query", required=True)

    inspect = sub.add_parser("inspect", help="inspect index metadata without loading a model")
    inspect.add_argument("--index", required=True)
    inspect.add_argument("--output")

    delete = sub.add_parser("delete", help="delete one document scope from a persistent NPZ index")
    delete.add_argument("--index", required=True)
    delete.add_argument("--model-dir", required=True)
    delete.add_argument("--tenant-id", required=True)
    delete.add_argument("--case-id", required=True)
    delete.add_argument("--document-id", required=True)
    delete.add_argument("--representation-level", type=int, choices=(0, 1, 2), default=0)
    delete.add_argument("--output")

    parse_credit = sub.add_parser("credit-report-parse", help="extract canonical facts from a mapped credit-report Excel")
    parse_credit.add_argument("--workbook", required=True)
    parse_credit.add_argument("--template", required=True)
    parse_credit.add_argument("--tenant-id", required=True)
    parse_credit.add_argument("--case-id", required=True)
    parse_credit.add_argument("--document-id", required=True)
    parse_credit.add_argument("--source-filename")
    parse_credit.add_argument("--output")

    select_fewshot = sub.add_parser("fewshot-select", help="select approved style-only examples")
    select_fewshot.add_argument("--registry", required=True)
    select_fewshot.add_argument("--review-item", required=True, choices=tuple(item.value for item in ReviewItem))
    select_fewshot.add_argument("--loan-type", required=True)
    select_fewshot.add_argument("--industry-code", required=True)
    select_fewshot.add_argument("--situation-tag", action="append", default=[])
    select_fewshot.add_argument("--max-examples", type=int, default=3)
    select_fewshot.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect":
        index = RAGIndex.load(args.index)
        _write({"metadata": index.metadata, "stats": index.stats()}, args.output)
        return 0

    if args.command == "fewshot-select":
        selector = FewShotSelector(FewShotRegistry.from_json(args.registry), args.max_examples)
        selected = selector.select(
            ReviewItem(args.review_item),
            loan_type=args.loan_type,
            industry_code=args.industry_code,
            situation_tags=args.situation_tag,
        )
        _write({"examples": [row.to_dict() for row in selected], "style_only": True}, args.output)
        return 0

    if args.command == "credit-report-parse":
        scope = DocumentScope(
            tenant_id=args.tenant_id,
            case_id=args.case_id,
            document_id=args.document_id,
            source_filename=args.source_filename or Path(args.workbook).name,
            document_kind="credit_report",
        )
        parsed = CreditReportParser().parse(
            args.workbook,
            CreditReportTemplate.from_json(args.template),
            scope,
        )
        _write(parsed.to_dict(), args.output)
        return 0

    if args.command == "delete":
        config = PipelineConfig.for_index_build(
            model_dir=args.model_dir,
            index_path=args.index,
            representation_level=args.representation_level,
            index_write_strategy="UPSERT",
        )
        pipeline = RAGPipeline(config)
        result = pipeline.delete_document(
            DocumentScope(args.tenant_id, args.case_id, args.document_id)
        )
        _write(result, args.output)
        return 0

    if args.command == "index":
        config = PipelineConfig.for_index_build(
            model_dir=args.model_dir,
            index_path=args.index,
            representation_level=args.representation_level,
            index_write_strategy=args.write_strategy,
        )
        pipeline = RAGPipeline(config)
        scope = DocumentScope(
            tenant_id=args.tenant_id,
            case_id=args.case_id,
            document_id=args.document_id,
            financial_scope=args.financial_scope,
            source_version=args.source_version,
            source_filename=args.source_filename or Path(args.master).name,
            document_kind=args.document_kind,
            loan_type=args.loan_type,
            industry_code=args.industry_code,
        )
        index = pipeline.prepare(args.master, scope)
        _write({"index": str(config.index_path), "metadata": index.metadata, "stats": index.stats()}, args.output)
        return 0

    config = PipelineConfig.for_serving(
        model_dir=args.model_dir,
        index_path=args.index,
        representation_level=args.representation_level,
        top_k=args.top_k,
    )
    pipeline = RAGPipeline(config)
    pipeline.prepare()
    filters = _filters(args)
    if args.command == "retrieve":
        _write(pipeline.retrieve(args.query, filters=filters), args.output)
    else:
        _write(pipeline.build_prompt(args.query_id, args.query, filters=filters).to_dict(), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
