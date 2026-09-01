from pathlib import Path

path = Path('tests/test_package.py')
text = path.read_text(encoding='utf-8')
text = text.replace('self.assertEqual(__version__, "0.26.11")', 'self.assertEqual(__version__, "0.26.12")')
path.write_text(text, encoding='utf-8')
print('v0.26.12 legacy version expectation updated')
