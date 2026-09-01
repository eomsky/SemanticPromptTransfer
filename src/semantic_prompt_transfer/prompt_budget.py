from __future__ import annotations

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
