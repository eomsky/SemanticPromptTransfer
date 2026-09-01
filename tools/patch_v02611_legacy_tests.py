from pathlib import Path

path = Path('tests/test_package.py')
text = path.read_text(encoding='utf-8')
text = text.replace('self.assertEqual(__version__, "0.26.10")', 'self.assertEqual(__version__, "0.26.11")')
path.write_text(text, encoding='utf-8')

path = Path('tests/test_v0265_demo.py')
text = path.read_text(encoding='utf-8')
if 'import time\n' not in text:
    text = text.replace('import tempfile\n', 'import tempfile\nimport time\n', 1)
text = text.replace(
    'def test_demo_files_seed_once_are_downloadable_and_remain_unprocessed():',
    'def test_demo_files_seed_once_are_downloadable_and_begin_background_processing():',
)
text = text.replace(
    '        assert all(row["status"] == "UPLOADED" for row in rows)\n',
    '        assert all(row["status"] in {"VALIDATING", "PARSING", "INDEXING", "READY", "EXCLUDED"} for row in rows)\n',
)
text = text.replace(
    '        assert all(row["progress_percent"] == 0 for row in rows)\n',
    '        assert all(0 <= int(row["progress_percent"]) <= 100 for row in rows)\n',
)
text = text.replace(
    '        assert processor.calls == 0\n        assert runtime.vectors.count() == 0\n',
    '        for _ in range(50):\n            if processor.calls >= 2:\n                break\n            time.sleep(0.01)\n        assert processor.calls == 2\n        assert runtime.vectors.count() == 0\n',
    1,
)
text = text.replace(
    '        assert processor.calls == 0\n        assert runtime.vectors.count() == 0\n        runtime.close(purge=True)\n',
    '        assert processor.calls == 2\n        assert runtime.vectors.count() == 0\n        runtime.close(purge=True)\n',
    1,
)
path.write_text(text, encoding='utf-8')
print('v0.26.11 legacy expectations updated')
