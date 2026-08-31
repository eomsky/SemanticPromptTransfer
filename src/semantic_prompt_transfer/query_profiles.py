from __future__ import annotations

from dataclasses import dataclass

from .domain import CaseContext, ReviewItem


@dataclass(frozen=True)
class ReviewQueryProfile:
    review_item: ReviewItem
    terms: tuple[str, ...]

    def build(self, case: CaseContext) -> str:
        context = [
            self.review_item.title,
            f"여신유형 {case.loan_type}",
            f"산업분류 {case.industry_code}",
        ]
        if case.company_name:
            context.append(f"대상기업 {case.company_name}")
        context.append("검색개념 " + ", ".join(self.terms))
        return "\n".join(context)


class QueryProfileRegistry:
    def __init__(self, profiles: tuple[ReviewQueryProfile, ...] | None = None) -> None:
        values = profiles or default_query_profiles()
        self._profiles = {profile.review_item: profile for profile in values}
        missing = set(ReviewItem) - set(self._profiles)
        if missing:
            raise ValueError(f"query profiles missing: {sorted(item.value for item in missing)}")

    def get(self, review_item: ReviewItem) -> ReviewQueryProfile:
        return self._profiles[review_item]


def default_query_profiles() -> tuple[ReviewQueryProfile, ...]:
    return (
        ReviewQueryProfile(
            ReviewItem.MAJOR_ACCOUNTS,
            (
                "매출채권", "재고자산", "유형자산", "차입금", "운전자본",
                "계정 증감", "회수가능성", "평가손실", "향후 전망",
            ),
        ),
        ReviewQueryProfile(
            ReviewItem.PROFITABILITY,
            (
                "매출액", "매출총이익", "영업이익", "당기순이익", "이익률",
                "원가율", "판관비", "일회성 손익", "수익성 전망",
            ),
        ),
        ReviewQueryProfile(
            ReviewItem.FINANCIAL_STABILITY,
            (
                "부채비율", "차입금의존도", "유동비율", "순차입금", "담보",
                "자산건전성", "매출채권 연령", "재고 노후화", "우발채무",
            ),
        ),
        ReviewQueryProfile(
            ReviewItem.CASH_FLOW,
            (
                "영업활동현금흐름", "잉여현금흐름", "EBITDA", "원리금상환",
                "이자보상", "차입금 만기", "유동성", "상환재원", "현금흐름 전망",
            ),
        ),
        ReviewQueryProfile(
            ReviewItem.MAJOR_CUSTOMERS,
            (
                "주요 매출처", "매출비중", "거래처 집중도", "상위 고객",
                "매출처 변동", "계약기간", "수주잔고", "산업 수요", "매출 전망",
            ),
        ),
    )
