from pathlib import Path

path = Path('tests/test_v02611_perf_render.py')
text = path.read_text(encoding='utf-8')
text = text.replace(
    '    assert "verification_pipeline": "section_batch_overlapped"\n',
    '    assert \'"verification_pipeline": "section_batch_overlapped"\' in orchestration\n',
)
path.write_text(text, encoding='utf-8')
print('v0.26.11 test syntax fixed')
