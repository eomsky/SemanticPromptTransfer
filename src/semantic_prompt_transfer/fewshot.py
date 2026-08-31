from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .domain import FewShotExample, ReviewItem


class FewShotRegistry:
    def __init__(self, examples: Iterable[FewShotExample]) -> None:
        rows = tuple(examples)
        ids = [row.example_id for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("few-shot example_id values must be unique")
        self.examples = rows

    @classmethod
    def from_json(cls, path: str | Path) -> "FewShotRegistry":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = value.get("examples", value) if isinstance(value, dict) else value
        return cls(FewShotExample.from_dict(row) for row in rows)


class FewShotSelector:
    """Select style-only examples by item, loan type, industry and situation."""

    def __init__(self, registry: FewShotRegistry, max_examples: int = 3) -> None:
        if max_examples < 1:
            raise ValueError("max_examples must be positive")
        self.registry = registry
        self.max_examples = int(max_examples)

    @staticmethod
    def _industry_score(candidates: tuple[str, ...], industry_code: str) -> int | None:
        if not candidates:
            return 0
        best: int | None = None
        for code in candidates:
            if code == "*":
                best = max(best or 0, 1)
            elif code == industry_code:
                best = max(best or 0, 12)
            elif industry_code.startswith(code) or code.startswith(industry_code):
                best = max(best or 0, 4 + min(len(code), len(industry_code)))
        return best

    def select(
        self,
        review_item: ReviewItem,
        *,
        loan_type: str,
        industry_code: str,
        situation_tags: Iterable[str] = (),
    ) -> list[FewShotExample]:
        wanted_tags = set(str(tag) for tag in situation_tags)
        ranked: list[tuple[int, str, FewShotExample]] = []
        for example in self.registry.examples:
            if example.approval_status != "APPROVED" or example.review_item is not review_item:
                continue
            if example.loan_types:
                if loan_type in example.loan_types:
                    loan_score = 10
                elif "*" in example.loan_types:
                    loan_score = 1
                else:
                    continue
            else:
                loan_score = 0
            industry_score = self._industry_score(example.industry_codes, industry_code)
            if industry_score is None:
                continue
            tag_score = 2 * len(wanted_tags.intersection(example.situation_tags))
            score = loan_score + industry_score + tag_score
            ranked.append((score, example.example_id, example))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return [row[2] for row in ranked[: self.max_examples]]
