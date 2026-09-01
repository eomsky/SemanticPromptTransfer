from pathlib import Path

root = Path(__file__).resolve().parents[1]


def replace_exact(path: str, old: str, new: str, count: int = 1) -> None:
    target = root / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"missing target in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, count), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Delete FallbackGenerator entirely.
# ---------------------------------------------------------------------------
path = root / "src/semantic_prompt_transfer/llm.py"
text = path.read_text(encoding="utf-8")
start = text.index("\n\nclass FallbackGenerator:")
end = text.index("\n\ndef default_cpu_generator", start)
text = text[:start] + text[end:]
text = text.replace(
    "def default_cpu_generator(\n"
    "    config: CpuGenerationConfig | None = None,\n"
    ") -> FallbackGenerator:\n"
    "    return FallbackGenerator(TransformersCpuGenerator(config), EvidenceTemplateGenerator())",
    "def default_cpu_generator(\n"
    "    config: CpuGenerationConfig | None = None,\n"
    ") -> TransformersCpuGenerator:\n"
    "    return TransformersCpuGenerator(config)",
)
path.write_text(text, encoding="utf-8")

replace_exact("src/semantic_prompt_transfer/__init__.py", "    FallbackGenerator,\n", "")
replace_exact("src/semantic_prompt_transfer/__init__.py", '    "FallbackGenerator",\n', "")

path = root / "tests/test_package.py"
text = path.read_text(encoding="utf-8").replace("    FallbackGenerator,\n", "")
start = text.index("    def test_cpu_generator_adapter_and_grounded_fallback(self):")
middle = text.index("        class FakeTokenizer:", start)
text = text[:start] + '    def test_cpu_generator_adapter(self):\n        messages = [{"role": "user", "content": "작성"}]\n\n' + text[middle:]
text = text.replace('self.assertEqual(__version__, "0.26.8")', 'self.assertEqual(__version__, "0.26.9")')
path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. Normalize monetary evidence to 백만원 before prompting.
# ---------------------------------------------------------------------------
path = root / "src/semantic_prompt_transfer/review.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "import re\nfrom dataclasses import dataclass, replace\n",
    "import re\nfrom decimal import Decimal, InvalidOperation, ROUND_HALF_UP\nfrom dataclasses import dataclass, replace\n",
    1,
)
anchor = '''_STOPWORDS = {
    "현재", "자료", "근거", "기준", "관련", "항목", "현황", "향후", "전망", "확인",
    "신용조사서", "첨부자료", "기타", "해당", "대한", "그리고", "또한", "으로", "에서",
}
'''
if anchor not in text:
    raise RuntimeError("review helper anchor missing")
helpers = anchor + '''

_MONEY_TO_MILLION = {
    "원": Decimal("0.000001"),
    "천원": Decimal("0.001"),
    "만원": Decimal("0.01"),
    "백만원": Decimal("1"),
    "억원": Decimal("100"),
}
_EXPLICIT_MONEY = re.compile(
    r"(?P<value>\\(?[+-]?\\d[\\d,]*(?:\\.\\d+)?\\)?)\\s*(?P<unit>백만원|억원|만원|천원|원)(?![가-힣])"
)


def _money_decimal(value: Any) -> Decimal | None:
    raw = str(value if value is not None else "").strip()
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1].strip()
    raw = raw.replace(",", "")
    try:
        result = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return -result if negative else result


def _format_million(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return f"{int(rounded):,}"
    return f"{rounded:,.1f}"


def _normalized_money(value: Any, unit: str | None) -> tuple[str, str | None, bool]:
    source_unit = str(unit or "").strip()
    factor = _MONEY_TO_MILLION.get(source_unit)
    number = _money_decimal(value)
    if factor is None or number is None:
        return str(value), unit, False
    million = number * factor
    return _format_million(million), "백만원", source_unit != "백만원"


def _normalize_explicit_money_text(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        number, unit, _ = _normalized_money(match.group("value"), match.group("unit"))
        return f"{number}{unit or match.group('unit')}"
    return _EXPLICIT_MONEY.sub(repl, str(value or ""))
'''
text = text.replace(anchor, helpers, 1)

old = '''            rendered = f"{fact.field_name}={fact.value}"
            if fact.unit:
                rendered += f"; 단위={fact.unit}"
            if fact.period:
                rendered += f"; 기간={fact.period}"
'''
new = '''            display_value, display_unit, unit_normalized = _normalized_money(fact.value, fact.unit)
            rendered = f"{fact.field_name}={display_value}"
            if display_unit:
                rendered += f"; 단위={display_unit}"
            if fact.period:
                rendered += f"; 기간={fact.period}"
'''
if old not in text:
    raise RuntimeError("credit fact render target missing")
text = text.replace(old, new, 1)
text = text.replace(
    '''                        "source_hash": fact.source_hash,
                        "source_class": "credit_report",
''',
    '''                        "source_hash": fact.source_hash,
                        "source_class": "credit_report",
                        "raw_value": fact.value,
                        "raw_unit": fact.unit,
                        "display_unit": display_unit,
                        "unit_normalized": unit_normalized,
''',
    1,
)
text = text.replace(
    '                    content=str(hit.get("document") or hit.get("embedding_text") or ""),',
    '                    content=_normalize_explicit_money_text(str(hit.get("document") or hit.get("embedding_text") or "")),',
    1,
)
old_policy = '''            "문체와 분석 구조만 참고한다. 수치·부호·단위·기간을 임의 변환하거나 계산하지 않는다. "
            "각 핵심 주장 문장 끝에는 제공된 [evidence_id]를 붙이고 존재하지 않는 근거 ID를 만들지 않는다."
'''
new_policy = '''            "문체와 분석 구조만 참고한다. 금액은 CURRENT_CASE_EVIDENCE에 표시된 정규화 단위를 그대로 사용한다. "
            "심사의견 금액 단위는 백만원을 기본으로 하며 원·천원·만원으로 다시 바꾸거나 한 항목 안에서 혼용하지 않는다. "
            "수치·부호·기간을 임의 계산하지 않는다. 각 핵심 주장 문장 끝에는 제공된 [evidence_id]를 붙이고 "
            "존재하지 않는 근거 ID를 만들지 않는다."
'''
if old_policy not in text:
    raise RuntimeError("prompt policy target missing")
text = text.replace(old_policy, new_policy, 1)
path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 3. Conservative verifier: only hard, exact MINOR span corrections mutate.
# ---------------------------------------------------------------------------
path = root / "src/semantic_prompt_transfer/verification_flow.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    '''    _FAIL_REASON_CODES = {
        "FACT_CONTRADICTION",
        "UNSUPPORTED_NUMERIC",
        "UNSUPPORTED_FACT",
        "UNSUPPORTED_CAUSAL",
        "PERIOD_MISMATCH",
        "UNIT_MISMATCH",
        "SOURCE_CONFLICT",
    }''',
    '''    _FAIL_REASON_CODES = {
        "FACT_CONTRADICTION",
        "PERIOD_MISMATCH",
        "UNIT_MISMATCH",
        "SOURCE_CONFLICT",
    }''',
    1,
)
text = text.replace(
    '''                    "분석 강도의 차이도 WARN 이하로만 판정한다. FAIL은 근거에 의해 명백히 확인되는 수치, "
                    "기간, 단위, 사실, 인과관계의 직접 오류가 있을 때만 허용한다. 동일 사실·동일 기간·동일 "
''',
    '''                    "분석 강도의 차이, 근거 미제시, 인과 추론의 강도 차이도 WARN 이하로만 판정한다. FAIL은 "
                    "근거와 직접 대조해 값·기간·단위·동일 사실이 명백히 충돌하는 경우에만 허용한다. 동일 사실·동일 기간·동일 "
''',
    1,
)
text = text.replace(
    '''                    "reason_code는 FACT_CONTRADICTION, UNSUPPORTED_NUMERIC, UNSUPPORTED_FACT, "
                    "UNSUPPORTED_CAUSAL, PERIOD_MISMATCH, UNIT_MISMATCH, SOURCE_CONFLICT 중 하나만 사용한다."
''',
    '''                    "reason_code는 FACT_CONTRADICTION, PERIOD_MISMATCH, UNIT_MISMATCH, SOURCE_CONFLICT 중 하나만 사용한다. "
                    "자동 수정이 필요한 경우에도 severity는 MINOR만 사용한다."
''',
    1,
)
marker = '''            if reason_code not in self._FAIL_REASON_CODES:
                return self._warn(claim, "UNSAFE_FAIL_REASON", reason or "FAIL 사유가 허용 범위를 벗어남")
'''
if marker not in text:
    raise RuntimeError("verifier fail marker missing")
text = text.replace(
    marker,
    marker + '''            if severity is not RepairSeverity.MINOR:
                return self._warn(claim, "AUTO_PATCH_SCOPE_TOO_WIDE", "문장/문단 재작성은 자동 반영하지 않음")
''',
    1,
)
start = text.index("class PatchGuard:")
end = text.index("\n\nclass RepairCoordinator:", start)
patch_guard = '''class PatchGuard:
    """Only an exact verifier-selected MINOR span may be changed automatically."""

    @staticmethod
    def apply(claim: Claim, finding: VerificationFinding, replacement: str) -> str | None:
        replacement = str(replacement or "").strip()
        if not replacement or finding.severity is not RepairSeverity.MINOR:
            return None
        span = str(finding.problem_span or "")
        if not span or span not in claim.text:
            return None
        if len(replacement) > max(120, len(span) * 2):
            return None
        return claim.text.replace(span, replacement, 1)
'''
text = text[:start] + patch_guard + text[end:]
old = '''        for _ in range(self.max_attempts):
            if finding.severity is RepairSeverity.MINOR:
                output_rule = "문장 전체가 아니라 problem_span을 대체할 문자열만 출력한다."
            else:
                output_rule = "해당 claim 하나만 다시 작성한다. 다른 문장이나 설명을 출력하지 않는다."
            messages = [
'''
new = '''        if finding.severity is not RepairSeverity.MINOR:
            return None
        for _ in range(self.max_attempts):
            output_rule = "문장 전체가 아니라 problem_span을 대체할 문자열만 출력한다."
            messages = [
'''
if old not in text:
    raise RuntimeError("repair coordinator target missing")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Completion guard and at most one automatic patch per section.
# ---------------------------------------------------------------------------
path = root / "src/semantic_prompt_transfer/orchestration.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    '_CITATION = re.compile(r"(?:CR|ATT)_[a-f0-9]{20}", re.IGNORECASE)\n',
    '_CITATION = re.compile(r"(?:CR|ATT)_[a-f0-9]{20}", re.IGNORECASE)\n'
    '_COMPLETE_SUFFIXES = ("함", "임", "됨", "음", "필요함", "양호함", "판단됨", "예상됨", "전망됨", "확인됨")\n'
    '_INCOMPLETE_SUFFIXES = ("등에", "으로", "하며", "하고", "및", "따라", "대해", "대한", "에서", "위해", "통해", "반면", "이나", "지만", "경우", "때문에")\n',
    1,
)
text = text.replace(
    '        verifier: VerificationAgent | None = None,\n    ) -> None:',
    '        verifier: VerificationAgent | None = None,\n        max_auto_patches_per_section: int = 1,\n        max_completion_attempts: int = 2,\n    ) -> None:',
    1,
)
text = text.replace(
    '        self.repair = RepairCoordinator(max_repair_attempts)\n',
    '        self.repair = RepairCoordinator(max_repair_attempts)\n'
    '        self.max_auto_patches_per_section = max(0, int(max_auto_patches_per_section))\n'
    '        self.max_completion_attempts = max(0, int(max_completion_attempts))\n',
    1,
)
insertion_point = text.index("    @staticmethod\n    def _cited_ids")
methods = '''    @classmethod
    def _looks_complete(cls, text: str) -> bool:
        visible = _CITATION.sub("", str(text or "")).strip()
        visible = re.sub(r"\\s+", " ", visible).rstrip()
        if not visible:
            return False
        if visible.endswith((".", "!", "?", "。")):
            return True
        if any(visible.endswith(suffix) for suffix in _COMPLETE_SUFFIXES):
            return True
        if visible.endswith((",", ";", ":", "·", "-")):
            return False
        if any(visible.endswith(suffix) for suffix in _INCOMPLETE_SUFFIXES):
            return False
        return False

    @staticmethod
    def _novel_continuation(base: str, continuation: str) -> str:
        value = str(continuation or "").strip()
        if not value:
            return ""
        if value.startswith(base):
            return value[len(base):].lstrip()
        maximum = min(240, len(base), len(value))
        for size in range(maximum, 12, -1):
            if base[-size:] == value[:size]:
                return value[size:].lstrip()
        return value

    def _ensure_complete(
        self,
        *,
        case: CaseContext,
        job_id: str,
        item: ReviewItem,
        prompt: ReviewPromptPackage,
        generator: TextGenerator,
        text: str,
        token_callback: Callable[[ReviewItem, str], None] | None,
    ) -> str:
        current = str(text or "").strip()
        if self._looks_complete(current):
            return current
        for attempt in range(1, self.max_completion_attempts + 1):
            messages = [
                *[dict(row) for row in prompt.messages],
                {"role": "assistant", "content": current},
                {
                    "role": "user",
                    "content": (
                        "직전 심사의견의 마지막 문장이 중간에서 끊겼다. 이미 작성한 내용을 반복하거나 "
                        "새 분석 포인트를 추가하지 말고, 끊긴 마지막 문장만 자연스럽게 이어서 완결하라. "
                        "출력은 이어질 문자열만 작성한다."
                    ),
                },
            ]
            try:
                continuation = str(generator.generate(messages) or "").strip()
            except Exception as exc:
                self._audit(case, job_id, "GENERATION_COMPLETION_ERROR", {
                    "item": item.value,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                })
                break
            addition = self._novel_continuation(current, continuation)
            if not addition:
                break
            current = current.rstrip() + ("" if current.endswith((" ", "\\n")) else " ") + addition.lstrip()
            if token_callback:
                token_callback(item, addition)
            self._audit(case, job_id, "GENERATION_COMPLETION_CONTINUED", {
                "item": item.value,
                "attempt": attempt,
            })
            if self._looks_complete(current):
                break
        return current.strip()

'''
text = text[:insertion_point] + methods + text[insertion_point:]
text = text.replace(
    '''            if text:
                return text, False
            raise RuntimeError("empty generation")
''',
    '''            if text:
                text = self._ensure_complete(
                    case=case,
                    job_id=job_id,
                    item=item,
                    prompt=prompt,
                    generator=generator,
                    text=text,
                    token_callback=token_callback,
                )
                return text, False
            raise RuntimeError("empty generation")
''',
    1,
)
text = text.replace(
    '''                text = self._generate_once(generator, prompt.messages)
                if text:
                    return text, True
''',
    '''                text = self._generate_once(generator, prompt.messages)
                if text:
                    text = self._ensure_complete(
                        case=case,
                        job_id=job_id,
                        item=item,
                        prompt=prompt,
                        generator=generator,
                        text=text,
                        token_callback=None,
                    )
                    return text, True
''',
    1,
)
old_loop = '''        repaired_any = False
        current = text
        # Reverse order preserves the original offsets. The current slice is the only
        # mutable scope, so another claim can never be rewritten by this repair.
        for claim, finding in sorted(findings, key=lambda pair: pair[0].start, reverse=True):
            if finding.status is not VerificationStatus.FAIL:
                continue
'''
new_loop = '''        repaired_any = False
        current = text
        patch_count = 0
        for claim, finding in sorted(findings, key=lambda pair: pair[0].start):
            if finding.status is not VerificationStatus.FAIL:
                continue
            if patch_count >= self.max_auto_patches_per_section:
                break
'''
if old_loop not in text:
    raise RuntimeError("verification patch loop target missing")
text = text.replace(old_loop, new_loop, 1)
text = text.replace(
    '            repaired_any = True\n            if patch_callback:\n',
    '            repaired_any = True\n            patch_count += 1\n            if patch_callback:\n',
    1,
)
text = text.replace(
    '    The reusable core defaults to OFF. The v0.26.7 Colab POC activates an LLM verifier\n',
    '    The reusable core defaults to OFF. The operating Colab activates a conservative LLM verifier\n',
    1,
)
path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. Version, notebook output budget, and focused tests.
# ---------------------------------------------------------------------------
replace_exact("src/semantic_prompt_transfer/version.py", 'PACKAGE_VERSION = "0.26.8"', 'PACKAGE_VERSION = "0.26.9"')
replace_exact("pyproject.toml", 'version = "0.26.8"', 'version = "0.26.9"')

path = root / "tests/test_colab_poc.py"
if path.exists():
    text = path.read_text(encoding="utf-8").replace("max_new_tokens=1400", "max_new_tokens=1800")
    path.write_text(text, encoding="utf-8")

source = root / "notebooks/SemanticPromptTransfer_v0.26.8_COLAB_LAUNCHER.ipynb"
target = root / "notebooks/SemanticPromptTransfer_v0.26.9_COLAB_LAUNCHER.ipynb"
notebook = source.read_text(encoding="utf-8")
notebook = notebook.replace("v0.26.8", "v0.26.9").replace("0.26.8", "0.26.9").replace("v0268", "v0269")
notebook = notebook.replace("max_new_tokens=1400", "max_new_tokens=1800")
target.write_text(notebook, encoding="utf-8")

(root / "tests/test_v0269_quality_guard.py").write_text(
    '''from semantic_prompt_transfer.domain import CreditFact, EvidenceRecord, ReviewItem, SourceTier
from semantic_prompt_transfer.review import EvidenceAssembler
from semantic_prompt_transfer.verification_flow import Claim, LLMVerificationAgent, VerificationStatus
from semantic_prompt_transfer.orchestration import ReviewGenerationOrchestrator


class JsonGenerator:
    def __init__(self, value):
        self.value = value
    def generate(self, messages):
        return self.value


def test_credit_won_is_normalized_to_million():
    fact = CreditFact(
        "f", "x", "총차입금", 164134131441, "원", "2025",
        (ReviewItem.MAJOR_ACCOUNTS,), False, "d", "c.xlsx", "S", "A1"
    )
    rows = EvidenceAssembler().assemble(ReviewItem.MAJOR_ACCOUNTS, [fact], {"hits": []})
    assert "164,134.1" in rows[0].content
    assert "단위=백만원" in rows[0].content
    assert "164134131441" not in rows[0].content


def test_unsupported_causal_is_not_auto_fail():
    claim = Claim("A-001", ReviewItem.MAJOR_ACCOUNTS, "원가 부담이 확대됨.", 0, 11, (), 1)
    raw = '{"status":"FAIL","severity":"MINOR","problem_span":"원가 부담","reason_code":"UNSUPPORTED_CAUSAL","reason":"근거 부족","evidence_ids":["CR_00000000000000000001"],"repair_instruction":"삭제"}'
    evidence = EvidenceRecord(
        "CR_00000000000000000001", ReviewItem.MAJOR_ACCOUNTS,
        SourceTier.CREDIT_REPORT_ITEM, "매출액=100; 단위=백만원", "d", "c.xlsx"
    )
    result = LLMVerificationAgent(JsonGenerator(raw)).verify(claim, [evidence])
    assert result.status is VerificationStatus.WARN


def test_claim_rewrite_scope_is_downgraded():
    claim = Claim("A-001", ReviewItem.MAJOR_ACCOUNTS, "총차입금은 100백만원임.", 0, 15, (), 1)
    raw = '{"status":"FAIL","severity":"CLAIM_ERROR","problem_span":"100백만원","reason_code":"FACT_CONTRADICTION","reason":"불일치","evidence_ids":["CR_00000000000000000001"],"repair_instruction":"수정"}'
    evidence = EvidenceRecord(
        "CR_00000000000000000001", ReviewItem.MAJOR_ACCOUNTS,
        SourceTier.CREDIT_REPORT_ITEM, "총차입금=120; 단위=백만원", "d", "c.xlsx"
    )
    result = LLMVerificationAgent(JsonGenerator(raw)).verify(claim, [evidence])
    assert result.status is VerificationStatus.WARN


def test_completion_detection():
    assert not ReviewGenerationOrchestrator._looks_complete("재고 증가 등에")
    assert not ReviewGenerationOrchestrator._looks_complete("차입금 증가에 따라")
    assert ReviewGenerationOrchestrator._looks_complete("총차입금 증가 추세임")
    assert ReviewGenerationOrchestrator._looks_complete("수익성 개선됨.")
''',
    encoding="utf-8",
)


# Changelog.
changelog = root / "CHANGELOG.md"
current = changelog.read_text(encoding="utf-8")
entry = '''## 0.26.9 - 2026-09-01

- Deleted FallbackGenerator from runtime, public exports, and tests.
- Restricted automatic verifier mutation to exact MINOR spans for hard evidence conflicts, at most one patch per review section.
- Normalized structured monetary evidence to 백만원 and instructed generation not to mix 원/천원/만원 units.
- Added a completion guard that continues a prematurely ended Korean review sentence without introducing new analysis.
- Increased the Colab per-call output budget from 1400 to 1800 tokens while retaining continuation support.

'''
if "## 0.26.9 - 2026-09-01" not in current:
    changelog.write_text(entry + current, encoding="utf-8")

print("v0.26.9 patch complete")
