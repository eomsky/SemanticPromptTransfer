from __future__ import annotations

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
