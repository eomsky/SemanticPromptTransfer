import json
from pathlib import Path

path = Path('notebooks/SemanticPromptTransfer_v0.26.10_COLAB_LAUNCHER.ipynb')
nb = json.loads(path.read_text(encoding='utf-8'))
for cell in nb.get('cells', []):
    if cell.get('cell_type') != 'code':
        continue
    lines = list(cell.get('source', []))
    if not any('build_colab_poc(' in line for line in lines):
        continue
    seen_verification_mode = False
    cleaned = []
    for line in lines:
        if 'verification_mode="ENFORCE"' in line:
            if seen_verification_mode:
                continue
            seen_verification_mode = True
        cleaned.append(line)
    cell['source'] = cleaned
path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding='utf-8')
print('v0.26.10 notebook duplicate verification_mode removed')
