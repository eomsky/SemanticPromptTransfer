from __future__ import annotations

from enum import Enum


class ChatIntent(str, Enum):
    GENERAL = "GENERAL"
    CASE_QA = "CASE_QA"
    OPINION_QA = "OPINION_QA"


class ChatIntentRouter:
    """Separate ordinary conversation from case retrieval and opinion explanation."""

    _case_markers = (
        "이 회사", "해당 회사", "해당 기업", "현재 기업", "본건", "이 심사건", "현재 심사건",
        "업로드", "첨부", "신용조사서", "사업보고서", "이 자료", "우리 자료", "이 기업",
    )
    _opinion_markers = (
        "심사의견", "아까 의견", "방금 의견", "왜 그렇게", "근거가 뭐", "근거는 뭐",
        "a항목", "b항목", "c항목", "d항목", "e항목", "a 의견", "b 의견", "c 의견", "d 의견", "e 의견",
    )

    def route(self, message: str) -> ChatIntent:
        value = " ".join(str(message or "").lower().split())
        if any(marker in value for marker in self._opinion_markers):
            return ChatIntent.OPINION_QA
        if any(marker in value for marker in self._case_markers):
            return ChatIntent.CASE_QA
        return ChatIntent.GENERAL
