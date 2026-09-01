from pathlib import Path

path = Path('tests/test_package.py')
text = path.read_text(encoding='utf-8')
text = text.replace('self.assertEqual(__version__, "0.26.10")', 'self.assertEqual(__version__, "0.26.11")')
path.write_text(text, encoding='utf-8')

path = Path('tests/test_v0265_demo.py')
text = path.read_text(encoding='utf-8')
text = text.replace(
    'def test_demo_files_seed_once_are_downloadable_and_remain_unprocessed():',
    'def test_demo_files_seed_once_are_downloadable_and_begin_background_processing():',
)
text = text.replace(
    '        assert all(row["status"] == "UPLOADED" for row in rows)\n',
    '        assert all(row["status"] in {"VALIDATING", "PARSING", "INDEXING", "READY", "EXCLUDED"} for row in rows)\n',
)
path.write_text(text, encoding='utf-8')
print('v0.26.11 legacy expectations updated')
