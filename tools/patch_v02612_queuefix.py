from pathlib import Path

path = Path('src/semantic_prompt_transfer/poc_scheduler.py')
text = path.read_text(encoding='utf-8')
old = '''        def to_dict(self) -> dict[str, object]:\n            return asdict(self)\n'''
new = '''        def to_dict(self) -> dict[str, object]:\n            value = asdict(self)\n            value.pop("job_id", None)\n            return value\n'''
if old not in text:
    raise RuntimeError('QueueState.to_dict patch target missing')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('v0.26.12 queue payload fixed')
