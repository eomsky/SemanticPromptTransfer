from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path('.')


def replace_exact(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    if old not in text:
        raise RuntimeError(f'missing patch target in {path}: {old[:120]!r}')
    target.write_text(text.replace(old, new, count), encoding='utf-8')


# ---------------------------------------------------------------------------
# 1. Prompt token budget manager.
# ---------------------------------------------------------------------------
(ROOT / 'src/semantic_prompt_transfer/prompt_budget.py').write_text(r'''from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class PromptBudgetSnapshot:
    max_model_tokens: int
    generation_reserve_tokens: int
    completion_reserve_tokens: int
    safety_margin_tokens: int
    input_budget_tokens: int
    prompt_tokens: int
    remaining_input_tokens: int
    counter_mode: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "max_model_tokens": self.max_model_tokens,
            "generation_reserve_tokens": self.generation_reserve_tokens,
            "completion_reserve_tokens": self.completion_reserve_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "prompt_tokens": self.prompt_tokens,
            "remaining_input_tokens": self.remaining_input_tokens,
            "counter_mode": self.counter_mode,
        }


class PromptTokenBudgetManager:
    """Allocate Gemma context before a generation request is sent.

    Colab supplies the actual Gemma tokenizer.  Unit tests and non-Colab callers use a
    deliberately conservative Korean-friendly estimate so the code remains portable.
    """

    def __init__(
        self,
        *,
        max_model_tokens: int = 28672,
        generation_reserve_tokens: int = 3600,
        completion_reserve_tokens: int = 700,
        safety_margin_tokens: int = 1200,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.max_model_tokens = int(max_model_tokens)
        self.generation_reserve_tokens = int(generation_reserve_tokens)
        self.completion_reserve_tokens = int(completion_reserve_tokens)
        self.safety_margin_tokens = int(safety_margin_tokens)
        self.token_counter = token_counter
        if self.max_model_tokens < 4096:
            raise ValueError("max_model_tokens is too small")
        if min(self.generation_reserve_tokens, self.completion_reserve_tokens, self.safety_margin_tokens) < 0:
            raise ValueError("token reserves cannot be negative")
        if self.input_budget_tokens < 1500:
            raise ValueError("reserved output/completion tokens leave too little input context")

    @property
    def input_budget_tokens(self) -> int:
        return (
            self.max_model_tokens
            - self.generation_reserve_tokens
            - self.completion_reserve_tokens
            - self.safety_margin_tokens
        )

    @property
    def counter_mode(self) -> str:
        return "model_tokenizer" if self.token_counter is not None else "conservative_estimate"

    def count_text(self, text: str) -> int:
        value = str(text or "")
        if not value:
            return 0
        if self.token_counter is not None:
            try:
                return max(0, int(self.token_counter(value)))
            except Exception:
                pass
        # Korean financial text can tokenize close to character granularity.  This is
        # intentionally more conservative than the usual English 4-char heuristic.
        return max(1, int(math.ceil(len(value) / 1.7)))

    def count_messages(self, messages: Iterable[dict[str, str]]) -> int:
        total = 0
        for row in messages:
            total += self.count_text(str(row.get("content") or "")) + 8
        return total + 8

    def fits(self, messages: Iterable[dict[str, str]]) -> bool:
        return self.count_messages(messages) <= self.input_budget_tokens

    def snapshot(self, messages: Iterable[dict[str, str]]) -> PromptBudgetSnapshot:
        prompt_tokens = self.count_messages(messages)
        return PromptBudgetSnapshot(
            max_model_tokens=self.max_model_tokens,
            generation_reserve_tokens=self.generation_reserve_tokens,
            completion_reserve_tokens=self.completion_reserve_tokens,
            safety_margin_tokens=self.safety_margin_tokens,
            input_budget_tokens=self.input_budget_tokens,
            prompt_tokens=prompt_tokens,
            remaining_input_tokens=max(0, self.input_budget_tokens - prompt_tokens),
            counter_mode=self.counter_mode,
        )
''', encoding='utf-8')


# ---------------------------------------------------------------------------
# 2. Credit reasoning layer: materiality + risk/mitigant + cross-item signals.
# ---------------------------------------------------------------------------
(ROOT / 'src/semantic_prompt_transfer/credit_reasoning.py').write_text(r'''from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .domain import CaseContext, EvidenceRecord, ReviewItem
from .llm import TextGenerator

_NUMBER = re.compile(r"(?<![A-Za-z0-9_])[+-]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_])")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

_ITEM_WEIGHTS: dict[ReviewItem, dict[str, float]] = {
    ReviewItem.MAJOR_ACCOUNTS: {
        "매출채권": 1.5, "재고": 1.5, "차입금": 1.6, "운전자금": 1.4,
        "유형자산": 0.8, "자본": 1.0, "선수금": 0.9, "수주": 0.8,
    },
    ReviewItem.PROFITABILITY: {
        "매출": 1.2, "영업이익": 1.7, "당기순이익": 1.4, "원가": 1.2,
        "이익률": 1.4, "금융비용": 1.2, "손실": 1.5,
    },
    ReviewItem.FINANCIAL_STABILITY: {
        "부채비율": 1.8, "차입금의존도": 1.8, "유동비율": 1.5, "차입금": 1.5,
        "자본": 1.3, "유동성": 1.4, "우발": 1.3, "담보": 1.0,
    },
    ReviewItem.CASH_FLOW: {
        "영업활동현금흐름": 2.0, "현금흐름": 1.5, "상환": 1.8, "이자": 1.3,
        "만기": 1.4, "차입금": 1.4, "현금": 1.2, "투자활동": 0.8,
    },
    ReviewItem.MAJOR_CUSTOMERS: {
        "매출처": 1.7, "매출비중": 1.7, "거래처": 1.5, "수주잔고": 1.3,
        "계약": 1.1, "집중": 1.4, "발주": 1.2,
    },
}

_ITEM_JUDGMENT: dict[ReviewItem, str] = {
    ReviewItem.MAJOR_ACCOUNTS: "운전자금 점유, 자산 회전, 차입수요 및 계정 변동의 지속가능성",
    ReviewItem.PROFITABILITY: "외형 성장의 질, 원가/마진, 이익 지속성 및 현금창출과의 연결",
    ReviewItem.FINANCIAL_STABILITY: "자본완충력, 차입부담, 단기유동성 및 자산의 현금전환 가능성",
    ReviewItem.CASH_FLOW: "영업현금 창출, 투자 후 잉여현금, 차입금 만기와 실질 상환재원",
    ReviewItem.MAJOR_CUSTOMERS: "거래처 집중도, 발주 변동성, 수주/계약의 매출 전환 및 매출기반 안정성",
}


def _score(item: ReviewItem, row: EvidenceRecord) -> float:
    content = str(row.content or "")
    score = 0.0
    for term, weight in _ITEM_WEIGHTS[item].items():
        if term in content:
            score += weight
    score += min(0.7, len(_NUMBER.findall(content)) * 0.08)
    if len(set(_YEAR.findall(content))) >= 2:
        score += 0.55
    if row.metadata.get("conflicts_with_credit_ids") or row.metadata.get("conflicting_attachment_ids"):
        score += 0.75
    try:
        vector = float(row.metadata.get("score")) if row.metadata.get("score") is not None else 0.0
    except (TypeError, ValueError):
        vector = 0.0
    score += max(0.0, min(1.0, vector)) * 0.35
    return round(score, 4)


def _materiality(score: float) -> str:
    if score >= 2.4:
        return "HIGH"
    if score >= 1.25:
        return "MEDIUM"
    return "LOW"


@dataclass(frozen=True)
class ReasoningPortfolio:
    items: dict[str, dict[str, Any]]
    cross_item_signals: tuple[dict[str, Any], ...]
    planner: str

    def item_blueprint(self, item: ReviewItem) -> dict[str, Any]:
        value = dict(self.items.get(item.value) or {})
        value["cross_item_signals"] = [dict(row) for row in self.cross_item_signals]
        value["planner"] = self.planner
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "cross_item_signals": [dict(row) for row in self.cross_item_signals],
            "planner": self.planner,
        }


class CreditReasoningLayer:
    """Prepare a compact, inspectable credit-judgment blueprint before prose generation."""

    def __init__(self, generator: TextGenerator | None = None, *, max_evidence_per_item: int = 10) -> None:
        self.generator = generator
        self.max_evidence_per_item = max(3, int(max_evidence_per_item))

    def _assess(self, evidence_by_item: dict[ReviewItem, list[EvidenceRecord]]) -> dict[ReviewItem, list[dict[str, Any]]]:
        result: dict[ReviewItem, list[dict[str, Any]]] = {}
        for item, rows in evidence_by_item.items():
            assessed = []
            for row in rows:
                score = _score(item, row)
                assessed.append({
                    "evidence_id": row.evidence_id,
                    "source_class": row.source_class,
                    "materiality_score": score,
                    "materiality": _materiality(score),
                    "content": str(row.content or "")[:900],
                })
            assessed.sort(key=lambda x: (-float(x["materiality_score"]), x["evidence_id"]))
            result[item] = assessed
        return result

    @staticmethod
    def _fallback_cross(evidence_by_item: dict[ReviewItem, list[EvidenceRecord]]) -> tuple[dict[str, Any], ...]:
        corpus = {item: "\n".join(row.content for row in rows) for item, rows in evidence_by_item.items()}
        signals: list[dict[str, Any]] = []
        if any(term in corpus.get(ReviewItem.PROFITABILITY, "") for term in ("영업이익", "당기순이익")) and "현금흐름" in corpus.get(ReviewItem.CASH_FLOW, ""):
            signals.append({"type": "PROFIT_CASHFLOW_ALIGNMENT", "result": "CHECK", "why": "손익 개선이 영업현금으로 전환되는지 함께 판단"})
        if any(term in corpus.get(ReviewItem.MAJOR_ACCOUNTS, "") for term in ("재고", "매출채권")) and "현금흐름" in corpus.get(ReviewItem.CASH_FLOW, ""):
            signals.append({"type": "WORKING_CAPITAL_PRESSURE", "result": "CHECK", "why": "영업자산 증가가 현금흐름 및 추가 차입수요에 미치는 영향 점검"})
        if "차입금" in (corpus.get(ReviewItem.MAJOR_ACCOUNTS, "") + corpus.get(ReviewItem.FINANCIAL_STABILITY, "")) and "자본" in corpus.get(ReviewItem.FINANCIAL_STABILITY, ""):
            signals.append({"type": "DEBT_CAPITAL_BALANCE", "result": "CHECK", "why": "차입 증가와 자본완충력의 동시 변화를 판단"})
        return tuple(signals[:6])

    def _fallback(self, assessed: dict[ReviewItem, list[dict[str, Any]]], evidence_by_item: dict[ReviewItem, list[EvidenceRecord]]) -> ReasoningPortfolio:
        items: dict[str, dict[str, Any]] = {}
        for item in ReviewItem.ordered():
            candidates = [row for row in assessed.get(item, []) if row["materiality"] != "LOW"]
            if not candidates:
                candidates = assessed.get(item, [])[:3]
            candidates = candidates[:5]
            items[item.value] = {
                "judgment_focus": _ITEM_JUDGMENT[item],
                "priority_evidence_ids": [row["evidence_id"] for row in candidates],
                "priority_issues": [
                    {
                        "title": row["content"][:80],
                        "materiality": row["materiality"],
                        "direction": "MIXED",
                        "evidence_ids": [row["evidence_id"]],
                        "why_it_matters": _ITEM_JUDGMENT[item],
                        "risks": [],
                        "mitigants": [],
                        "repayment_impact": "CHECK",
                        "trend": "CHECK",
                        "forward_triggers": [],
                    }
                    for row in candidates
                ],
            }
        return ReasoningPortfolio(items, self._fallback_cross(evidence_by_item), "deterministic_materiality")

    def plan(self, case: CaseContext, evidence_by_item: dict[ReviewItem, list[EvidenceRecord]]) -> ReasoningPortfolio:
        assessed = self._assess(evidence_by_item)
        fallback = self._fallback(assessed, evidence_by_item)
        if self.generator is None:
            return fallback

        compact: dict[str, list[dict[str, Any]]] = {}
        allowed_ids: dict[str, set[str]] = {}
        for item in ReviewItem.ordered():
            rows = assessed.get(item, [])[: self.max_evidence_per_item]
            compact[item.value] = rows
            allowed_ids[item.value] = {row["evidence_id"] for row in rows}

        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 기업여신 심사 판단 설계자다. 심사의견 문장을 작성하지 말고 구조화된 JSON만 반환한다. "
                    "검색된 사실을 중요도 순으로 선별하고, 단순 수치 나열보다 상환능력과 신용위험에 중요한 연결을 우선한다. "
                    "각 A-E 항목에서 HIGH/MEDIUM 중요 이슈 3~5개를 선택하여 관찰사실, 왜 중요한지, 위험요인, 완화요인, "
                    "상환능력 영향, 추세 지속성, 향후 관찰변수를 정리한다. 자료에 없는 원인은 만들지 않는다. "
                    "A-E를 연결해 이익-현금흐름 일치, 운전자본-차입부담, 차입-자본완충, 매출처-외형 안정성 같은 cross_item_signals도 만든다. "
                    "evidence_ids는 제공된 ID만 사용한다. 내부 추론과 설명문은 출력하지 않는다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"case={case.case_id}\n"
                    "다음 evidence 후보를 바탕으로 JSON을 작성하라.\n"
                    + json.dumps({k: v for k, v in compact.items()}, ensure_ascii=False, separators=(",", ":"))
                    + "\nJSON schema: {items:{A:{judgment_focus,priority_evidence_ids,priority_issues:[{title,materiality,direction,evidence_ids,observation,why_it_matters,risks,mitigants,repayment_impact,trend,forward_triggers}]},B:{...},C:{...},D:{...},E:{...}},cross_item_signals:[{type,result,why,evidence_ids}]}"
                ),
            },
        ]
        try:
            raw = str(self.generator.generate(messages) or "").strip()
            match = re.search(r"\{.*\}", raw, flags=re.S)
            if not match:
                return fallback
            value = json.loads(match.group(0))
        except Exception:
            return fallback

        result_items: dict[str, dict[str, Any]] = {}
        raw_items = value.get("items") if isinstance(value, dict) else None
        for item in ReviewItem.ordered():
            base = dict(fallback.items[item.value])
            candidate = dict((raw_items or {}).get(item.value) or {}) if isinstance(raw_items, dict) else {}
            issues = []
            for issue in candidate.get("priority_issues") or []:
                if not isinstance(issue, dict):
                    continue
                ids = [str(eid) for eid in issue.get("evidence_ids") or [] if str(eid) in allowed_ids[item.value]]
                if not ids:
                    continue
                materiality = str(issue.get("materiality") or "MEDIUM").upper()
                if materiality not in {"HIGH", "MEDIUM"}:
                    continue
                cleaned = dict(issue)
                cleaned["evidence_ids"] = ids
                cleaned["materiality"] = materiality
                issues.append(cleaned)
                if len(issues) >= 5:
                    break
            if issues:
                base["priority_issues"] = issues
                priority_ids = []
                for issue in issues:
                    for eid in issue["evidence_ids"]:
                        if eid not in priority_ids:
                            priority_ids.append(eid)
                base["priority_evidence_ids"] = priority_ids
                if candidate.get("judgment_focus"):
                    base["judgment_focus"] = str(candidate["judgment_focus"])
            result_items[item.value] = base

        allowed_all = set().union(*allowed_ids.values()) if allowed_ids else set()
        cross = []
        for row in value.get("cross_item_signals") or [] if isinstance(value, dict) else []:
            if not isinstance(row, dict):
                continue
            ids = [str(eid) for eid in row.get("evidence_ids") or [] if str(eid) in allowed_all]
            cleaned = dict(row)
            cleaned["evidence_ids"] = ids
            cross.append(cleaned)
            if len(cross) >= 6:
                break
        if not cross:
            cross = [dict(row) for row in fallback.cross_item_signals]
        return ReasoningPortfolio(result_items, tuple(cross), "gemma_structured_reasoning")
''', encoding='utf-8')


# ---------------------------------------------------------------------------
# 3. Replace ReviewPromptBuilder with token-budgeted compact evidence packing.
# ---------------------------------------------------------------------------
review_path = ROOT / 'src/semantic_prompt_transfer/review.py'
review = review_path.read_text(encoding='utf-8')
if 'import json\n' not in review.split('\n', 10)[:10]:
    review = review.replace('from __future__ import annotations\n\n', 'from __future__ import annotations\n\nimport json\n', 1)
if 'from .prompt_budget import PromptTokenBudgetManager' not in review:
    review = review.replace('from .identity import evidence_id\n', 'from .identity import evidence_id\nfrom .prompt_budget import PromptTokenBudgetManager\n', 1)
start = review.index('class ReviewPromptBuilder:')
new_builder = r'''class ReviewPromptBuilder:
    """Evidence-maximizing prompt builder with explicit credit-reasoning guidance."""

    _OUTPUT_DEPTH = {
        ReviewItem.MAJOR_ACCOUNTS: "핵심 이슈 4~6개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.PROFITABILITY: "핵심 이슈 3~5개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.FINANCIAL_STABILITY: "핵심 이슈 4~6개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.CASH_FLOW: "핵심 이슈 4~6개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
        ReviewItem.MAJOR_CUSTOMERS: "핵심 이슈 3~5개를 선별하고 각 이슈를 2~4문장으로 충분히 분석",
    }

    def __init__(
        self,
        max_context_chars: int = 18000,
        *,
        credit_report_min_share: float = 0.50,
        token_budget_manager: PromptTokenBudgetManager | None = None,
    ) -> None:
        self.max_context_chars = int(max_context_chars)
        self.credit_report_min_share = float(credit_report_min_share)
        self.token_budget = token_budget_manager
        if self.max_context_chars < 1000:
            raise ValueError("max_context_chars must be at least 1000")
        if not 0.0 <= self.credit_report_min_share <= 1.0:
            raise ValueError("credit_report_min_share must be between 0 and 1")

    @staticmethod
    def _legacy_block(row: EvidenceRecord) -> str:
        label = "CREDIT_REPORT" if row.source_class == "credit_report" else "ATTACHMENT"
        conflicts = ",".join(row.metadata.get("conflicts_with_credit_ids") or [])
        return (
            f"[{label} EVIDENCE]\nsource_class={row.source_class}\n"
            f"evidence_id={row.evidence_id}\ndocument_id={row.document_id}\n"
            f"source_filename={row.source_filename}\npage={row.page}\n"
            f"direct_conflict_credit_ids={conflicts}\ncontent={row.content}"
        )

    @staticmethod
    def _compact_entry(row: EvidenceRecord) -> str:
        conflicts = ",".join(row.metadata.get("conflicts_with_credit_ids") or [])
        suffix = f" | direct_conflict_credit_ids={conflicts}" if conflicts else ""
        return f"id={row.evidence_id}{suffix} | {row.content}"

    @staticmethod
    def _group_header(row: EvidenceRecord) -> str:
        metadata = dict(row.metadata or {})
        if row.source_class == "credit_report":
            loc = f"{metadata.get('sheet_name') or '시트'}:{metadata.get('cell_range') or '범위'}"
            return f"[CREDIT_REPORT_GROUP file={row.source_filename or 'credit.xlsx'} location={loc}]"
        loc = metadata.get("logical_table_id") or metadata.get("source_location") or "region"
        return f"[ATTACHMENT_GROUP file={row.source_filename or 'attachment'} page={row.page or '-'} region={loc}]"

    def _render_grouped(self, rows: list[EvidenceRecord]) -> list[str]:
        groups: dict[tuple[Any, ...], list[EvidenceRecord]] = {}
        order: list[tuple[Any, ...]] = []
        for row in rows:
            key = visual_group_key(row)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        blocks = []
        for key in order:
            members = groups[key]
            blocks.append(self._group_header(members[0]) + "\n" + "\n".join(self._compact_entry(row) for row in members))
        return blocks

    def _ranked(
        self,
        query: str,
        evidence: list[EvidenceRecord],
        reasoning_blueprint: dict[str, Any] | None = None,
    ) -> list[tuple[float, EvidenceRecord]]:
        blueprint = reasoning_blueprint or {}
        priority = {str(v) for v in blueprint.get("priority_evidence_ids") or []}
        supporting = set()
        for issue in blueprint.get("priority_issues") or []:
            if isinstance(issue, dict):
                supporting.update(str(v) for v in issue.get("evidence_ids") or [])
        ranked: list[tuple[float, EvidenceRecord]] = []
        for row in evidence:
            lexical = _similarity(query, row.content)
            vector_score = row.metadata.get("score") if row.source_class == "attachment" else None
            try:
                vector = max(-1.0, min(1.0, float(vector_score))) if vector_score is not None else 0.0
            except (TypeError, ValueError):
                vector = 0.0
            score = lexical + max(0.0, vector) * 0.35
            if row.evidence_id in priority:
                score += 1.5
            elif row.evidence_id in supporting:
                score += 0.75
            if row.metadata.get("conflicts_with_credit_ids") or row.metadata.get("conflicting_attachment_ids"):
                score += 0.25
            metadata = dict(row.metadata)
            metadata["selection_score"] = round(score, 6)
            ranked.append((score, replace(row, metadata=metadata)))
        ranked.sort(key=lambda value: (-value[0], value[1].evidence_id))
        return ranked

    @staticmethod
    def _novelty_order(entries, initially_seen=None):
        seen = set(initially_seen or ())
        unique, repeats = [], []
        for entry in entries:
            key = visual_group_key(entry[1])
            if key in seen:
                repeats.append(entry)
            else:
                unique.append(entry)
                seen.add(key)
        return unique + repeats

    def _select_legacy(self, query: str, evidence: list[EvidenceRecord], reasoning_blueprint=None):
        ranked = [(s, row, self._legacy_block(row)) for s, row in self._ranked(query, evidence, reasoning_blueprint)]
        credit = [entry for entry in ranked if entry[1].source_class == "credit_report"]
        target_credit = min(
            sum(len(entry[2]) for entry in credit),
            int(self.max_context_chars * self.credit_report_min_share),
        ) if credit else 0
        selected, chosen, seen_groups = [], set(), set()
        used = credit_used = 0
        for entry in self._novelty_order(credit):
            if credit_used >= target_credit:
                break
            size = len(entry[2])
            if used + size > self.max_context_chars:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id)
            seen_groups.add(visual_group_key(entry[1])); used += size; credit_used += size
        remaining = [entry for entry in ranked if entry[1].evidence_id not in chosen]
        for entry in self._novelty_order(remaining, seen_groups):
            size = len(entry[2])
            if used + size > self.max_context_chars:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id); used += size
            if entry[1].source_class == "credit_report":
                credit_used += size
        selected.sort(key=lambda value: (-value[0], value[1].evidence_id))
        rows = [entry[1] for entry in selected]
        return rows, {
            "selection_mode": "legacy_char_budget",
            "context_characters": used,
            "credit_report_target_characters": target_credit,
            "credit_report_floor_satisfied": credit_used >= target_credit,
            "unique_visual_evidence_groups": len({visual_group_key(row) for row in rows}),
        }

    def _select_tokens(
        self,
        query: str,
        evidence: list[EvidenceRecord],
        evidence_budget_tokens: int,
        reasoning_blueprint: dict[str, Any] | None,
    ):
        assert self.token_budget is not None
        ranked = self._ranked(query, evidence, reasoning_blueprint)
        entries = [(score, row, self.token_budget.count_text(self._compact_entry(row)) + 10) for score, row in ranked]
        credit = [entry for entry in entries if entry[1].source_class == "credit_report"]
        available_credit = sum(entry[2] for entry in credit)
        target_credit = min(available_credit, int(evidence_budget_tokens * self.credit_report_min_share)) if credit else 0
        selected, chosen, seen_groups = [], set(), set()
        used = credit_used = 0
        for entry in self._novelty_order(credit):
            if credit_used >= target_credit:
                break
            if used + entry[2] > evidence_budget_tokens:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id)
            seen_groups.add(visual_group_key(entry[1])); used += entry[2]; credit_used += entry[2]
        remaining = [entry for entry in entries if entry[1].evidence_id not in chosen]
        for entry in self._novelty_order(remaining, seen_groups):
            if used + entry[2] > evidence_budget_tokens:
                continue
            selected.append(entry); chosen.add(entry[1].evidence_id); used += entry[2]
            if entry[1].source_class == "credit_report":
                credit_used += entry[2]
        selected.sort(key=lambda value: (-value[0], value[1].evidence_id))
        rows = [entry[1] for entry in selected]
        return rows, {
            "selection_mode": "token_budget_materiality_diversity",
            "evidence_budget_tokens": evidence_budget_tokens,
            "evidence_tokens_used": used,
            "credit_report_target_tokens": target_credit,
            "credit_report_tokens_used": credit_used,
            "credit_report_floor_satisfied": credit_used >= target_credit,
            "unique_visual_evidence_groups": len({visual_group_key(row) for row in rows}),
        }

    @staticmethod
    def _blueprint_text(value: dict[str, Any] | None) -> str:
        if not value:
            return "구조화 판단정보 없음"
        compact = {
            "judgment_focus": value.get("judgment_focus"),
            "priority_issues": value.get("priority_issues") or [],
            "cross_item_signals": value.get("cross_item_signals") or [],
        }
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

    def build(
        self,
        case: CaseContext,
        review_item: ReviewItem,
        query: str,
        evidence: list[EvidenceRecord],
        few_shots: list[FewShotExample],
        reasoning_blueprint: dict[str, Any] | None = None,
    ) -> ReviewPromptPackage:
        system = (
            "현재 심사건에서 제공된 근거만 사실로 사용한다. 신용조사서와 기타 첨부자료는 출처 종류만으로 상시 우선순위를 부여하지 않고 독립 근거로 검토한다. "
            "동일 사실·동일 기간·동일 단위가 직접 충돌할 때에만 신용조사서 내용을 채택한다. FEW SHOT은 사실이 아니라 심사역의 표현·분석 구조만 참고한다. "
            "CREDIT_REASONING_BLUEPRINT의 중요도와 항목 간 연계를 우선 반영한다. 단순 사실 나열에 그치지 말고 각 핵심 이슈에서 사실→의미→위험→완화요인→상환능력 영향→향후 관찰변수를 연결한다. "
            "자료에 없는 원인은 만들지 않는다. 금액은 CURRENT_CASE_EVIDENCE의 정규화 단위를 사용하고 한 비교축에서 단위를 혼용하지 않는다. "
            "각 핵심 주장에는 제공된 evidence_id를 붙이며 존재하지 않는 ID를 만들지 않는다."
        )
        blueprint_text = self._blueprint_text(reasoning_blueprint)
        depth = self._OUTPUT_DEPTH[review_item]
        fixed_prefix = (
            f"심사건: case={case.case_id}\n심사항목: {review_item.value}. {review_item.title}\n\n"
            f"[QUERY_PROFILE]\n{query}\n\n[CREDIT_REASONING_BLUEPRINT]\n{blueprint_text}\n\n"
        )
        writing = (
            "\n\n[작성요청]\n심사역 관점에서 중요도가 높은 내용을 우선한다. " + depth + ". "
            "LOW 중요도 사실은 핵심 판단을 설명하는 데 필요할 때만 사용한다. 위험요인만 나열하지 말고 확인 가능한 완화요인과 상환재원 영향을 함께 비교한다. "
            "손익과 현금흐름, 운전자본과 차입금, 차입금과 자본완충, 매출처와 외형 안정성처럼 관련 항목을 연결한다. "
            "현재보다 충분히 상세하게 작성하되 반복은 피하고 최종 심사의견만 출력한다. 마지막 문장은 반드시 완결한다."
        )
        style_candidates = []
        for example in few_shots:
            summary = _sanitize_style_text(example.input_summary, example.forbidden_tokens)
            output = _sanitize_style_text(example.output_example, example.forbidden_tokens)
            if self.token_budget is not None:
                summary = summary[:480]
            style_candidates.append(
                f"[STYLE_ONLY_FEW_SHOT {example.example_id}]\n입력맥락: {summary}\n작성예시: {output}\n주의: 사실은 전이하지 않는다."
            )

        selection: dict[str, Any]
        if self.token_budget is None:
            kept_style = style_candidates
            kept_evidence, selection = self._select_legacy(query, evidence, reasoning_blueprint)
            evidence_blocks = [self._legacy_block(row) for row in kept_evidence]
            user = fixed_prefix + "[FEW_SHOT_STYLE_ONLY]\n" + ("\n\n".join(kept_style) if kept_style else "선택된 예시 없음") + "\n\n[CURRENT_CASE_EVIDENCE]\n" + ("\n\n".join(evidence_blocks) if evidence_blocks else "현재 근거 없음") + writing
            budget_info = {"token_budget_enabled": False}
        else:
            input_budget = self.token_budget.input_budget_tokens
            style_cap = min(4200, max(900, int(input_budget * 0.22)))
            kept_style, style_tokens = [], 0
            for block in style_candidates:
                cost = self.token_budget.count_text(block) + 8
                if kept_style and style_tokens + cost > style_cap:
                    continue
                if style_tokens + cost > style_cap and kept_style:
                    continue
                kept_style.append(block); style_tokens += cost
            if not kept_style and style_candidates:
                kept_style = [style_candidates[0]]
                style_tokens = self.token_budget.count_text(style_candidates[0]) + 8
            fixed_tokens = self.token_budget.count_text(system + fixed_prefix + writing) + style_tokens + 80
            evidence_budget = max(700, input_budget - fixed_tokens)
            kept_evidence, selection = self._select_tokens(query, evidence, evidence_budget, reasoning_blueprint)
            while True:
                evidence_blocks = self._render_grouped(kept_evidence)
                user = fixed_prefix + "[FEW_SHOT_STYLE_ONLY]\n" + ("\n\n".join(kept_style) if kept_style else "선택된 예시 없음") + "\n\n[CURRENT_CASE_EVIDENCE]\n" + ("\n\n".join(evidence_blocks) if evidence_blocks else "현재 근거 없음") + writing
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                if self.token_budget.fits(messages) or len(kept_evidence) <= 1:
                    break
                kept_evidence.pop()
            snap = self.token_budget.snapshot(messages)
            budget_info = {"token_budget_enabled": True, **snap.to_dict(), "style_tokens": style_tokens}

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        conflicts = {
            row.evidence_id: list(row.metadata.get("conflicts_with_credit_ids") or [])
            for row in kept_evidence if row.metadata.get("conflicts_with_credit_ids")
        }
        priority_ids = list((reasoning_blueprint or {}).get("priority_evidence_ids") or [])
        return ReviewPromptPackage(
            schema_version="review-prompt-2.0",
            review_item=review_item,
            query=query,
            messages=messages,
            evidence=[row.to_dict() for row in kept_evidence],
            few_shots=[
                {
                    "example_id": row.example_id,
                    "review_item": row.review_item.value,
                    "version": row.version,
                    "style_only": True,
                    "sanitized": True,
                    "loan_types": list(row.loan_types),
                    "industry_codes": list(row.industry_codes),
                    "situation_tags": list(row.situation_tags),
                }
                for row in few_shots[: len(kept_style)]
            ],
            manifest={
                "tenant_id": case.tenant_id,
                "case_id": case.case_id,
                "evidence_policy": "source_neutral_with_credit_report_direct_conflict_resolution",
                "conflict_resolution": "credit_report_on_direct_conflict",
                "few_shot_is_evidence": False,
                "reasoning_layer": "credit_reasoning_materiality_cross_item",
                "priority_evidence_ids": priority_ids,
                "selected_evidence_count": len(kept_evidence),
                "available_evidence_count": len(evidence),
                "direct_conflicts": conflicts,
                **selection,
                **budget_info,
            },
        )
'''
review_path.write_text(review[:start] + new_builder, encoding='utf-8')


# ---------------------------------------------------------------------------
# 4. Bootstrap and service wiring for separate logical Gemma roles.
# ---------------------------------------------------------------------------
pb = ROOT / 'src/semantic_prompt_transfer/poc_bootstrap.py'
text = pb.read_text(encoding='utf-8')
text = text.replace('from pathlib import Path\n', 'from pathlib import Path\nfrom typing import Callable\n', 1)
if 'from .prompt_budget import PromptTokenBudgetManager' not in text:
    text = text.replace('from .poc_review import EphemeralReviewJobService\n', 'from .poc_review import EphemeralReviewJobService\nfrom .prompt_budget import PromptTokenBudgetManager\nfrom .review import ReviewPromptBuilder\n', 1)
text = text.replace(
    '    verification_mode: str = "OFF",\n) -> ColabPocBundle:',
    '    verification_mode: str = "OFF",\n    verification_generator: TextGenerator | None = None,\n    reasoning_generator: TextGenerator | None = None,\n    completion_generator: TextGenerator | None = None,\n    prompt_token_counter: Callable[[str], int] | None = None,\n    model_context_tokens: int = 28672,\n    generation_reserve_tokens: int = 3600,\n    completion_reserve_tokens: int = 700,\n) -> ColabPocBundle:',
    1,
)
needle = '''        upload_processor = PocUploadProcessor(
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
'''
replacement = '''        upload_processor = PocUploadProcessor(
            embedding_encoder,
            runtime.vectors,
            runtime.artifacts,
            credit_template=template,
        )
        retriever = ShardedAttachmentRetriever(embedding_encoder, runtime.vectors)
        budget = PromptTokenBudgetManager(
            max_model_tokens=model_context_tokens,
            generation_reserve_tokens=generation_reserve_tokens,
            completion_reserve_tokens=completion_reserve_tokens,
            safety_margin_tokens=1200,
            token_counter=prompt_token_counter,
        )
        prompt_builder = ReviewPromptBuilder(token_budget_manager=budget)
        review_jobs = EphemeralReviewJobService(
            runtime,
            retriever,
            few_shots,
            text_generator,
            upload_processor,
            verification_mode=verification_mode,
            verification_generator=verification_generator or primary_generator,
            reasoning_generator=reasoning_generator or primary_generator,
            completion_generator=completion_generator or primary_generator,
            prompt_builder=prompt_builder,
        )
'''
if needle not in text:
    raise RuntimeError('poc_bootstrap service block target missing')
text = text.replace(needle, replacement, 1)
pb.write_text(text, encoding='utf-8')

pr = ROOT / 'src/semantic_prompt_transfer/poc_review.py'
text = pr.read_text(encoding='utf-8')
if 'from .credit_reasoning import CreditReasoningLayer' not in text:
    text = text.replace('from .chat_routing import ChatIntent, ChatIntentRouter\n', 'from .chat_routing import ChatIntent, ChatIntentRouter\nfrom .credit_reasoning import CreditReasoningLayer\n', 1)
if 'from .review import ReviewPromptBuilder' not in text:
    text = text.replace('from .review_docx import OpinionDocumentBuilder\n', 'from .review import ReviewPromptBuilder\nfrom .review_docx import OpinionDocumentBuilder\n', 1)
text = text.replace(
    '        verification_generator: TextGenerator | None = None,\n    ) -> None:',
    '        verification_generator: TextGenerator | None = None,\n        reasoning_generator: TextGenerator | None = None,\n        completion_generator: TextGenerator | None = None,\n        prompt_builder: ReviewPromptBuilder | None = None,\n    ) -> None:',
    1,
)
old = '''        mode = VerificationMode(str(getattr(verification_mode, "value", verification_mode)).upper())
        verifier = LLMVerificationAgent(verification_generator or generator) if mode is not VerificationMode.OFF else None
        self.orchestrator = ReviewGenerationOrchestrator(
            retriever,
            few_shots,
            registry=runtime.registry,
            llm=generator,
            document_builder=OpinionDocumentBuilder(capture_service=self.capture_service),
            verification_mode=mode,
            verifier=verifier,
        )
'''
new = '''        mode = VerificationMode(str(getattr(verification_mode, "value", verification_mode)).upper())
        verifier = LLMVerificationAgent(verification_generator or generator) if mode is not VerificationMode.OFF else None
        reasoner = CreditReasoningLayer(reasoning_generator or generator)
        self.orchestrator = ReviewGenerationOrchestrator(
            retriever,
            few_shots,
            registry=runtime.registry,
            llm=generator,
            prompt_builder=prompt_builder,
            document_builder=OpinionDocumentBuilder(capture_service=self.capture_service),
            verification_mode=mode,
            verifier=verifier,
            reasoner=reasoner,
            completion_generator=completion_generator or generator,
        )
'''
if old not in text:
    raise RuntimeError('poc_review orchestrator target missing')
pr.write_text(text.replace(old, new, 1), encoding='utf-8')


# ---------------------------------------------------------------------------
# 5. Orchestrator: pre-plan A-E, completion rollback, evidence rebinding.
# ---------------------------------------------------------------------------
op = ROOT / 'src/semantic_prompt_transfer/orchestration.py'
text = op.read_text(encoding='utf-8')
if 'from .credit_reasoning import CreditReasoningLayer' not in text:
    text = text.replace('from .domain import CaseContext, CreditFact, EvidenceRecord, JobStage, ProgressEvent, ReviewItem, ReviewSectionDraft\n', 'from .domain import CaseContext, CreditFact, EvidenceRecord, JobStage, ProgressEvent, ReviewItem, ReviewSectionDraft\nfrom .credit_reasoning import CreditReasoningLayer\n', 1)
text = text.replace(
    '        max_completion_attempts: int = 2,\n    ) -> None:',
    '        max_completion_attempts: int = 2,\n        reasoner: CreditReasoningLayer | None = None,\n        completion_generator: TextGenerator | None = None,\n    ) -> None:',
    1,
)
text = text.replace(
    '        self.max_completion_attempts = max(0, int(max_completion_attempts))\n',
    '        self.max_completion_attempts = max(0, int(max_completion_attempts))\n        self.reasoner = reasoner or CreditReasoningLayer(None)\n        self.completion_generator = completion_generator\n',
    1,
)
# Use the smaller completion-role generator.
text = text.replace(
    '                continuation = str(generator.generate(messages) or "").strip()\n',
    '                completion = self.completion_generator or generator\n                continuation = str(completion.generate(messages) or "").strip()\n',
    1,
)
# Add fail-closed rollback for incomplete tails.
insert_at = text.index('    @staticmethod\n    def _cited_ids')
rollback = r'''    @classmethod
    def _rollback_incomplete_tail(cls, text: str) -> str:
        value = str(text or "").rstrip()
        if not value or cls._looks_complete(value):
            return value
        # Preserve a citation immediately following the last completed sentence.
        last = max(value.rfind("."), value.rfind("!"), value.rfind("?"), value.rfind("。"))
        if last >= 0:
            prefix = value[: last + 1]
            rest = value[last + 1 :]
            cite = re.match(r"\s*(?:\[(?:CR|ATT)_[a-f0-9]{20}\]\s*)*", rest, flags=re.I)
            return (prefix + (cite.group(0) if cite else "")).rstrip()
        lines = [line.rstrip() for line in value.splitlines() if line.strip()]
        kept = []
        for line in lines:
            if cls._looks_complete(line):
                kept.append(line)
            else:
                break
        return "\n".join(kept).strip() or value

'''
text = text[:insert_at] + rollback + text[insert_at:]
text = text.replace(
    '        return current.strip()\n\n    @classmethod\n    def _rollback_incomplete_tail',
    '        current = current.strip()\n        return current if self._looks_complete(current) else self._rollback_incomplete_tail(current)\n\n    @classmethod\n    def _rollback_incomplete_tail',
    1,
)
# Replace generate() to prepare all evidence before the reasoning pass.
generate_start = text.index('    def generate(\n')
new_generate = r'''    def generate(
        self,
        case: CaseContext,
        credit_facts: list[CreditFact],
        llm: LLMClient | None,
        output_path: str | Path,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
        token_callback: Callable[[ReviewItem, str], None] | None = None,
        section_callback: Callable[[ReviewItem, str, list[dict[str, Any]], dict[str, Any]], None] | None = None,
        job_id: str | None = None,
        claim_callback: Callable[[ReviewItem, dict[str, Any]], None] | None = None,
        patch_callback: Callable[[ReviewItem, dict[str, Any]], None] | None = None,
    ) -> ReviewGenerationResult:
        generator = llm or self.llm
        if generator is None:
            raise ValueError("a text generator must be provided to the orchestrator or generate()")
        if self.registry and job_id is not None:
            job = self.registry.get_job(job_id)
            if (job.tenant_id, job.case_id) != (case.tenant_id, case.case_id):
                raise ValueError("review job scope does not match the case")
        else:
            job = self.registry.create_job(case.tenant_id, case.case_id) if self.registry else None
        job_id = job_id or (job.job_id if job else uuid.uuid4().hex)
        events: list[ProgressEvent] = []
        prompts: list[ReviewPromptPackage] = []
        recovered_any = False

        def emit(stage: JobStage, progress: int, message: str, item: ReviewItem | None = None) -> None:
            event = ProgressEvent(stage, progress, message, item)
            events.append(event)
            if self.registry:
                try:
                    self.registry.update_job(job_id, stage, progress, message)
                except Exception:
                    pass
            if progress_callback:
                progress_callback(event)

        try:
            emit(JobStage.PRECHECK, 5, "입력자료와 심사범위를 확인했습니다.")
            emit(JobStage.CREDIT_REPORT_LOAD, 18, "신용조사서와 첨부자료를 심사 근거로 로드했습니다.")
            emit(JobStage.ATTACHMENT_RETRIEVAL, 26, "A~E 전체 관련 근거를 검색하고 중요도를 평가합니다.")

            prepared: dict[ReviewItem, tuple[str, list[EvidenceRecord], list[Any]]] = {}
            evidence_by_item: dict[ReviewItem, list[EvidenceRecord]] = {}
            for index, item in enumerate(ReviewItem.ordered()):
                query = self.query_profiles.get(item).build(case)
                try:
                    retrieval = self.attachment_retriever.search(
                        query,
                        filters={"tenant_id": case.tenant_id, "case_id": case.case_id},
                    )
                except Exception as exc:
                    recovered_any = True
                    retrieval = {"query": query, "hits": [], "recovered": True}
                    self._audit(case, job_id, "RETRIEVAL_RECOVERED", {"item": item.value, "error_type": type(exc).__name__, "message": str(exc)[:1000]})
                try:
                    evidence = self.evidence_assembler.assemble(item, credit_facts, retrieval)
                except Exception as exc:
                    recovered_any = True
                    evidence = self.evidence_assembler.assemble(item, credit_facts, {"hits": []}) if credit_facts else []
                    self._audit(case, job_id, "EVIDENCE_ASSEMBLY_RECOVERED", {"item": item.value, "error_type": type(exc).__name__, "message": str(exc)[:1000]})
                try:
                    examples = list(self.few_shot_selector.select(
                        item,
                        loan_type=case.loan_type,
                        industry_code=case.industry_code,
                        situation_tags=case.situation_tags,
                    ))
                except Exception as exc:
                    recovered_any = True
                    examples = []
                    self._audit(case, job_id, "FEW_SHOT_SELECTION_RECOVERED", {"item": item.value, "message": str(exc)[:1000]})
                prepared[item] = (query, evidence, examples)
                evidence_by_item[item] = evidence
                emit(JobStage.ATTACHMENT_RETRIEVAL, 27 + index * 2, f"{item.value}. 관련 근거 검색 완료", item)

            emit(JobStage.ATTACHMENT_RETRIEVAL, 38, "중요 이슈·위험·완화요인·상환영향 및 A~E 연계를 설계합니다.")
            try:
                portfolio = self.reasoner.plan(case, evidence_by_item)
            except Exception as exc:
                recovered_any = True
                portfolio = CreditReasoningLayer(None).plan(case, evidence_by_item)
                self._audit(case, job_id, "CREDIT_REASONING_RECOVERED", {"error_type": type(exc).__name__, "message": str(exc)[:1000]})

            sections: list[ReviewSectionDraft] = []
            trace = EvidenceTraceLedger()
            evidence_catalog: dict[str, dict[str, Any]] = {}

            for index, item in enumerate(ReviewItem.ordered()):
                base = 40 + index * 11
                emit(JobStage.ITEM_GENERATION, base, f"{item.value}. {item.title} 생성 중 ({index + 1}/5)", item)
                query, evidence, examples = prepared[item]
                blueprint = portfolio.item_blueprint(item)
                prompt = self.prompt_builder.build(case, item, query, evidence, examples, reasoning_blueprint=blueprint)
                prompts.append(prompt)
                prompt_evidence = self._prompt_evidence(prompt, evidence)
                for row in prompt_evidence:
                    evidence_catalog[row.evidence_id] = row.to_dict()

                text, recovered = self._technical_generate(
                    case=case,
                    job_id=job_id,
                    item=item,
                    prompt=prompt,
                    generator=generator,
                    token_callback=token_callback,
                )
                recovered_any = recovered_any or recovered

                text, verification, repaired = self._verify_and_patch(
                    item=item,
                    text=text,
                    evidence=prompt_evidence,
                    generator=generator,
                    claim_callback=claim_callback,
                    patch_callback=patch_callback,
                )
                recovered_any = recovered_any or repaired
                cited_ids = self._cited_ids(text, prompt_evidence)
                refs = trace.register(item, prompt_evidence, cited_ids)
                final_claims = self.segmenter.segment(item, text, [row.evidence_id for row in prompt_evidence])
                claim_evidence_map = []
                for claim in final_claims:
                    ref_nos = []
                    for eid in claim.evidence_ids:
                        ref_no = trace.ref_no_for(eid)
                        if ref_no is not None and ref_no not in ref_nos:
                            ref_nos.append(ref_no)
                    claim_evidence_map.append({"claim_id": claim.claim_id, "evidence_ids": list(claim.evidence_ids), "ref_nos": ref_nos})
                meta = {
                    "valid": True,
                    "verification_mode": self.verification_mode.value,
                    "verification": verification,
                    "recovered": recovered,
                    "repaired": repaired,
                    "evidence_rebound_after_patch": bool(repaired),
                    "cited_evidence_ids": list(cited_ids),
                    "evidence_refs": [ref.to_dict() for ref in refs],
                    "claim_evidence_map": claim_evidence_map,
                    "credit_reasoning": blueprint,
                    "prompt_budget": dict(prompt.manifest),
                }
                section = ReviewSectionDraft(
                    review_item=item,
                    text=text,
                    evidence_ids=cited_ids,
                    validation=meta,
                    evidence_refs=tuple(ref.to_dict() for ref in refs),
                )
                sections.append(section)
                if section_callback:
                    section_callback(item, text, prompt.evidence, meta)
                emit(JobStage.ITEM_GENERATION, min(95, base + 9), f"{item.value}. {item.title} 생성 완료 ({index + 1}/5)", item)

            emit(JobStage.DOCX_RENDER, 97, "심사의견과 근거 부록 Word 파일을 생성합니다.")
            try:
                target = self.document_builder.build(case, sections, output_path, evidence_catalog=evidence_catalog)
            except Exception as exc:
                recovered_any = True
                self._audit(case, job_id, "DOCX_RENDER_RECOVERED", {"error_type": type(exc).__name__, "message": str(exc)[:1000]})
                target = self.document_builder.build_minimal(case, sections, output_path)

            final_stage = JobStage.COMPLETE_WITH_WARNINGS if recovered_any else JobStage.COMPLETE
            if self.verification_mode is VerificationMode.OFF:
                final_message = "심사의견 생성이 완료되었습니다."
            elif self.verification_mode is VerificationMode.SHADOW:
                final_message = "심사의견 생성 및 비개입 검증이 완료되었습니다."
            else:
                final_message = "심사의견 생성 및 최소범위 검증 반영이 완료되었습니다."
            if self.registry:
                try:
                    self.registry.update_job(job_id, final_stage, 100, final_message, str(target))
                except Exception:
                    pass
            complete = ProgressEvent(final_stage, 100, final_message)
            events.append(complete)
            if progress_callback:
                progress_callback(complete)
            return ReviewGenerationResult(job_id, tuple(sections), tuple(prompts), tuple(events), str(target))
        except Exception as exc:
            return self._emergency_result(case, job_id, output_path, prompts, events, progress_callback, exc)
'''
op.write_text(text[:generate_start] + new_generate, encoding='utf-8')


# ---------------------------------------------------------------------------
# 6. UI: evidence mapping is permanent; verification highlight is only transient.
# ---------------------------------------------------------------------------
html = ROOT / 'src/semantic_prompt_transfer/examples/operational/credit_review_upload_demo.html'
text = html.read_text(encoding='utf-8')
text = text.replace(
    '.section-text { margin:0; color:#29313a; line-height:1.75; white-space:pre-wrap; word-break:keep-all; } .section-text.patching{background:#fff8ce;transition:background .25s ease;}',
    '.section-text { margin:0; color:#29313a; line-height:1.75; white-space:pre-wrap; word-break:keep-all; } .section-text.patching{background:#f4f8fc;transition:background .25s ease;}',
    1,
)
text = text.replace(
    '.claim { min-width:0; height:auto; padding:2px 4px; border:0; border-bottom:1px dashed var(--navy); border-radius:0; background:#fff8ce; color:#1f4167; font:inherit; line-height:1.75; text-align:left; }\n    .claim:hover { background:#ffef98; }',
    '.claim { min-width:0; height:auto; padding:1px 2px; border:0; border-bottom:1px dotted #9aaabd; border-radius:0; background:transparent; color:inherit; font:inherit; line-height:1.75; text-align:left; }\n    .claim:hover { background:#f1f6fb; color:#1f4167; }',
    1,
)
html.write_text(text, encoding='utf-8')


# ---------------------------------------------------------------------------
# 7. Public exports and versions.
# ---------------------------------------------------------------------------
init = ROOT / 'src/semantic_prompt_transfer/__init__.py'
text = init.read_text(encoding='utf-8')
if 'from .credit_reasoning import CreditReasoningLayer, ReasoningPortfolio' not in text:
    text = text.replace('from .credit_report import CreditFieldMapping, CreditReportParseResult, CreditReportParser, CreditReportTemplate\n', 'from .credit_report import CreditFieldMapping, CreditReportParseResult, CreditReportParser, CreditReportTemplate\nfrom .credit_reasoning import CreditReasoningLayer, ReasoningPortfolio\n', 1)
if 'from .prompt_budget import PromptTokenBudgetManager, PromptBudgetSnapshot' not in text:
    text = text.replace('from .prompting import PromptPackage, PromptPackageBuilder\n', 'from .prompting import PromptPackage, PromptPackageBuilder\nfrom .prompt_budget import PromptTokenBudgetManager, PromptBudgetSnapshot\n', 1)
for name in ('CreditReasoningLayer', 'ReasoningPortfolio', 'PromptTokenBudgetManager', 'PromptBudgetSnapshot'):
    if f'    "{name}",' not in text:
        pos = text.index('__all__ = [') + len('__all__ = [')
        text = text[:pos] + f'\n    "{name}",' + text[pos:]
init.write_text(text, encoding='utf-8')
replace_exact('src/semantic_prompt_transfer/version.py', 'PACKAGE_VERSION = "0.26.9"', 'PACKAGE_VERSION = "0.26.10"')
replace_exact('pyproject.toml', 'version = "0.26.9"', 'version = "0.26.10"')

# Runtime version test.
package_test = ROOT / 'tests/test_package.py'
text = package_test.read_text(encoding='utf-8').replace('self.assertEqual(__version__, "0.26.9")', 'self.assertEqual(__version__, "0.26.10")')
package_test.write_text(text, encoding='utf-8')


# ---------------------------------------------------------------------------
# 8. Build v0.26.10 Colab launcher from the validated v0.26.9 notebook.
# ---------------------------------------------------------------------------
source_nb = ROOT / 'notebooks/SemanticPromptTransfer_v0.26.9_COLAB_LAUNCHER.ipynb'
target_nb = ROOT / 'notebooks/SemanticPromptTransfer_v0.26.10_COLAB_LAUNCHER.ipynb'
nb = json.loads(source_nb.read_text(encoding='utf-8'))
for cell in nb.get('cells', []):
    if cell.get('cell_type') != 'code':
        continue
    code = ''.join(cell.get('source', []))
    code = code.replace('v0.26.9', 'v0.26.10').replace('0.26.9', '0.26.10').replace('v0269', 'v02610')
    code = code.replace('"--max-model-len", "16384"', '"--max-model-len", str(MODEL_CONTEXT_TOKENS)')
    code = code.replace('"--max-num-seqs", "4"', '"--max-num-seqs", "2"')
    code = code.replace('max_new_tokens=1800', 'max_new_tokens=3600')
    code = code.replace('streaming generation · 1800 tokens', 'streaming generation · 3600 tokens')
    if 'MODEL_ID = "google/gemma-4-26B-A4B-it"' in code and 'MODEL_CONTEXT_TOKENS = 28672' not in code:
        code = code.replace('MODEL_ID = "google/gemma-4-26B-A4B-it"\n', 'MODEL_ID = "google/gemma-4-26B-A4B-it"\nMODEL_CONTEXT_TOKENS = 28672\n')
    if 'local_generator = OpenAICompatibleHttpGenerator(' in code:
        start_local = code.index('local_generator = OpenAICompatibleHttpGenerator(')
        end_local = code.index('\n\nbundle = None', start_local)
        role_block = '''from transformers import AutoTokenizer\nprompt_tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token, use_fast=True)\ndef count_prompt_tokens(text):\n    return len(prompt_tokenizer.encode(str(text or ""), add_special_tokens=False))\n\ngeneration_generator = OpenAICompatibleHttpGenerator(\n    RemoteGenerationConfig(\n        base_url=f"http://127.0.0.1:{VLLM_PORT}/v1", model=MODEL_ID, api_key=vllm_api_key,\n        timeout_seconds=300, max_new_tokens=3600, max_continuations=0, temperature=0.0, allow_insecure_http=True,\n    )\n)\nreasoning_generator = OpenAICompatibleHttpGenerator(\n    RemoteGenerationConfig(\n        base_url=f"http://127.0.0.1:{VLLM_PORT}/v1", model=MODEL_ID, api_key=vllm_api_key,\n        timeout_seconds=300, max_new_tokens=1800, max_continuations=0, temperature=0.0, allow_insecure_http=True,\n    )\n)\nverification_generator = OpenAICompatibleHttpGenerator(\n    RemoteGenerationConfig(\n        base_url=f"http://127.0.0.1:{VLLM_PORT}/v1", model=MODEL_ID, api_key=vllm_api_key,\n        timeout_seconds=180, max_new_tokens=700, max_continuations=0, temperature=0.0, allow_insecure_http=True,\n    )\n)\ncompletion_generator = OpenAICompatibleHttpGenerator(\n    RemoteGenerationConfig(\n        base_url=f"http://127.0.0.1:{VLLM_PORT}/v1", model=MODEL_ID, api_key=vllm_api_key,\n        timeout_seconds=180, max_new_tokens=500, max_continuations=0, temperature=0.0, allow_insecure_http=True,\n    )\n)'''
        code = code[:start_local] + role_block + code[end_local:]
        code = code.replace('        generator=local_generator,\n', '        generator=generation_generator,\n        verification_generator=verification_generator,\n        reasoning_generator=reasoning_generator,\n        completion_generator=completion_generator,\n        prompt_token_counter=count_prompt_tokens,\n        model_context_tokens=MODEL_CONTEXT_TOKENS,\n        generation_reserve_tokens=3600,\n        completion_reserve_tokens=700,\n        verification_mode="ENFORCE",\n')
        code = code.replace('llm=vLLM/{MODEL_ID}', 'llm=vLLM/{MODEL_ID} · roles=Generation/Reasoning/Verifier/Completion')
    cell['source'] = code.splitlines(keepends=True)
target_nb.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding='utf-8')


# ---------------------------------------------------------------------------
# 9. Focused regression tests.
# ---------------------------------------------------------------------------
(ROOT / 'tests/test_v02610_credit_reasoning.py').write_text(r'''import json
from pathlib import Path

from semantic_prompt_transfer.credit_reasoning import CreditReasoningLayer
from semantic_prompt_transfer.domain import CaseContext, EvidenceRecord, FewShotExample, ReviewItem, SourceTier
from semantic_prompt_transfer.prompt_budget import PromptTokenBudgetManager
from semantic_prompt_transfer.review import ReviewPromptBuilder


def ev(item, eid, text, tier=SourceTier.ATTACHMENT, score=0.5, **meta):
    return EvidenceRecord(eid, item, tier, text, "doc", "report.pdf", 1, {"score": score, **meta})


def test_token_budget_caps_prompt_and_keeps_priority_evidence():
    manager = PromptTokenBudgetManager(
        max_model_tokens=2400,
        generation_reserve_tokens=500,
        completion_reserve_tokens=150,
        safety_margin_tokens=150,
        token_counter=lambda text: max(1, len(text) // 4),
    )
    builder = ReviewPromptBuilder(token_budget_manager=manager)
    item = ReviewItem.MAJOR_ACCOUNTS
    rows = [ev(item, f"ATT_{i:020x}", ("재고자산 차입금 운전자금 " if i == 0 else "기타 참고 ") + ("자료" * 140), score=0.9-i*0.02) for i in range(12)]
    blueprint = {"judgment_focus": "상환영향", "priority_evidence_ids": [rows[0].evidence_id], "priority_issues": [{"materiality":"HIGH","evidence_ids":[rows[0].evidence_id]}]}
    prompt = builder.build(CaseContext("t","c","운전자금","*"), item, "재고 차입금", rows, [], blueprint)
    assert prompt.manifest["prompt_tokens"] <= prompt.manifest["input_budget_tokens"]
    assert rows[0].evidence_id in {r["evidence_id"] for r in prompt.evidence}
    assert prompt.manifest["selection_mode"] == "token_budget_materiality_diversity"


def test_reasoning_fallback_prioritizes_material_credit_topics():
    item = ReviewItem.CASH_FLOW
    rows = [
        ev(item, "ATT_00000000000000000001", "2024년 영업활동현금흐름 100백만원, 2025년 영업활동현금흐름 500백만원, 차입금 상환재원 개선"),
        ev(item, "ATT_00000000000000000002", "기타 주석 참고"),
    ]
    portfolio = CreditReasoningLayer(None).plan(CaseContext("t","c","운전자금","*"), {r: (rows if r is item else []) for r in ReviewItem.ordered()})
    bp = portfolio.item_blueprint(item)
    assert "ATT_00000000000000000001" in bp["priority_evidence_ids"]
    assert bp["priority_issues"]


def test_prompt_contains_credit_reasoning_and_depth_instruction():
    item = ReviewItem.PROFITABILITY
    row = ev(item, "ATT_00000000000000000003", "2025년 영업이익 개선 및 원가율 하락")
    prompt = ReviewPromptBuilder().build(
        CaseContext("t","c","운전자금","*"), item, "영업이익", [row], [],
        {"judgment_focus":"수익성 지속성","priority_evidence_ids":[row.evidence_id],"priority_issues":[]},
    )
    combined = "\n".join(m["content"] for m in prompt.messages)
    assert "CREDIT_REASONING_BLUEPRINT" in combined
    assert "사실→의미→위험→완화요인→상환능력 영향" in combined
    assert "현재보다 충분히 상세하게" in combined


def test_v02610_notebook_has_expanded_context_and_role_generators():
    path = Path(__file__).resolve().parents[1] / "notebooks/SemanticPromptTransfer_v0.26.10_COLAB_LAUNCHER.ipynb"
    nb = json.loads(path.read_text(encoding="utf-8"))
    code = "\n".join("".join(c.get("source", [])) for c in nb["cells"] if c.get("cell_type") == "code")
    assert "MODEL_CONTEXT_TOKENS = 28672" in code
    assert '"--max-num-seqs", "2"' in code
    assert "max_new_tokens=3600" in code
    assert "reasoning_generator" in code and "verification_generator" in code and "completion_generator" in code
    assert "count_prompt_tokens" in code
    assert 'verification_mode="ENFORCE"' in code
    assert "136,281" in code
    assert "500,000, 2024년 575,000" not in code


def test_ui_does_not_keep_permanent_yellow_claim_background():
    html = (Path(__file__).resolve().parents[1] / "src/semantic_prompt_transfer/examples/operational/credit_review_upload_demo.html").read_text(encoding="utf-8")
    assert ".claim {" in html
    claim_css = html.split(".claim {", 1)[1].split("}", 1)[0]
    assert "background:transparent" in claim_css
    assert "#fff8ce" not in claim_css
''', encoding='utf-8')


# Changelog.
changelog = ROOT / 'CHANGELOG.md'
current = changelog.read_text(encoding='utf-8')
entry = '''## 0.26.10 - 2026-09-01\n\n- Added a structured Credit Reasoning Layer for materiality, risk/mitigant, repayment impact, trend, forward triggers, and cross-item A-E signals.\n- Added model-tokenizer-aware prompt budgeting and compact evidence grouping so evidence fills the available input window without context overflow.\n- Expanded the operating Gemma context target to 28,672 tokens with two concurrent sequences and doubled the generation ceiling to 3,600 tokens.\n- Split one loaded Gemma/vLLM server into logical Generation, Reasoning, Verifier, and Completion clients with role-specific output limits.\n- Made verifier patch highlighting transient and rebound final claims to evidence references after a patch; permanent yellow claim backgrounds were removed.\n- Added fail-closed incomplete-tail rollback so a completed section cannot end with a dangling clause.\n\n'''
if '## 0.26.10 - 2026-09-01' not in current:
    changelog.write_text(entry + current, encoding='utf-8')

print('v0.26.10 credit reasoning patch complete')
