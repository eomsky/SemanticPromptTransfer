from pathlib import Path

root = Path('.')

# Restore compatibility manifest flags used by existing POC contract tests.
path = root / 'src/semantic_prompt_transfer/review.py'
text = path.read_text(encoding='utf-8')
old = '                "available_evidence_count": len(evidence),\n                "direct_conflicts": conflicts,\n'
new = '                "available_evidence_count": len(evidence),\n                "credit_report_available": any(row.source_class == "credit_report" for row in kept_evidence),\n                "attachment_evidence_available": any(row.source_class == "attachment" for row in kept_evidence),\n                "direct_conflicts": conflicts,\n'
if old not in text:
    raise RuntimeError('review manifest compatibility target missing')
path.write_text(text.replace(old, new, 1), encoding='utf-8')

# The focused budget test should exercise the manager above its production safety floor.
path = root / 'tests/test_v02610_credit_reasoning.py'
text = path.read_text(encoding='utf-8').replace('max_model_tokens=2400,', 'max_model_tokens=5000,', 1)
path.write_text(text, encoding='utf-8')

# v0.26.10 permits an explicitly supplied verifier client while retaining primary as fallback.
path = root / 'tests/test_v0268_direct_generation.py'
text = path.read_text(encoding='utf-8')
text = text.replace(
    '    assert "verification_generator=primary_generator" in source\n',
    '    assert "verification_generator=verification_generator or primary_generator" in source\n',
    1,
)
path.write_text(text, encoding='utf-8')

print('v0.26.10 regression compatibility fixes applied')
