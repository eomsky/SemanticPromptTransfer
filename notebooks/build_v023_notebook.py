"""Build the editable v0.23 Colab launcher from the verified v0.22 bootstrap."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebooks" / "SemanticPromptTransfer_v0.22_COLAB_LAUNCHER.ipynb"
TARGET = ROOT / "notebooks" / "SemanticPromptTransfer_v0.23_COLAB_LAUNCHER.ipynb"


def source_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def few_shot_cell(number: int) -> str:
    return f'''# FEW SHOT {number} — 실제 우수 심사역 답안 입력
# 아래 사례는 모든 여신유형·업종에 일괄 적용되며, 사실 근거가 아니라 문체·분석 구조로만 사용됩니다.
FEW_SHOT_{number} = {{
    # 사례 입력자료 요약. 회사명·고객식별정보는 제거하고 재무·영업 상황만 기입합니다.
    "case_summary": """
""".strip(),
    "answers": {{
        "A": """
""".strip(),  # 가. 재무제표 주요계정(현황 및 향후전망)
        "B": """
""".strip(),  # 나. 수익성(현황 및 향후전망)
        "C": """
""".strip(),  # 다. 재무안정성 및 자산의 질(현황 및 향후전망)
        "D": """
""".strip(),  # 라. 현금흐름 및 채무상환능력(현황 및 향후전망)
        "E": """
""".strip(),  # 마. 주요 매출처 및 매출비중 변동 추이
    }},
}}
'''


notebook = json.loads(SOURCE.read_text(encoding="utf-8"))
original = "".join(notebook["cells"][1]["source"])
original = original.replace("v0.22", "v0.23").replace("0.22.0", "0.23.0")
original = original.replace("spt_bootstrap_v022", "spt_bootstrap_v023")
split_marker = 'ngrok_token = secret("NGROK_AUTHTOKEN", prompt="ngrok Authtoken: ")'
setup, runtime = original.split(split_marker, 1)
runtime = 'ngrok_token = secret("NGROK_AUTHTOKEN")' + runtime
runtime = runtime.replace(
    'llm_base_url = secret("SPT_LLM_BASE_URL") or None\n'
    'llm_model = secret("SPT_LLM_MODEL", default="local-credit-review-model")\n'
    'llm_api_key = secret("SPT_LLM_API_KEY") or None\n',
    'hf_token = secret("HF_TOKEN")\n'
    'if not hf_token:\n'
    '    raise RuntimeError("Colab Secrets의 HF_TOKEN이 필요합니다")\n',
)
runtime = runtime.replace(
    'from semantic_prompt_transfer import __version__, build_colab_poc',
    'from semantic_prompt_transfer import (\n'
    '    TransformersMultimodalGenerator,\n'
    '    TwoPassReviewGenerator,\n'
    '    __version__,\n'
    '    build_colab_poc,\n'
    ')',
)
runtime = runtime.replace(
    'bundle = None\nserver = None',
    '''# 세 입력 셀을 심사항목별 3-shot(JSON 15개 레코드)으로 변환합니다.
few_shot_rows = []
item_titles = {
    "A": "재무제표 주요계정(현황 및 향후전망)",
    "B": "수익성(현황 및 향후전망)",
    "C": "재무안정성 및 자산의 질(현황 및 향후전망)",
    "D": "현금흐름 및 채무상환능력(현황 및 향후전망)",
    "E": "주요 매출처 및 매출비중 변동 추이",
}
for shot_number, shot in enumerate((FEW_SHOT_1, FEW_SHOT_2, FEW_SHOT_3), start=1):
    summary = str(shot.get("case_summary") or "").strip()
    for item_code, title in item_titles.items():
        answer = str((shot.get("answers") or {}).get(item_code) or "").strip()
        if not answer:
            continue
        few_shot_rows.append({
            "example_id": f"COLAB-FS{shot_number}-{item_code}",
            "review_item_code": item_code,
            "input_summary": summary,
            "output_example": answer,
            "example_version": "1",
            "approval_status": "APPROVED",
            "loan_types": [],
            "industry_codes": [],
            "situation_tags": [],
            "style_tags": ["expert-reviewer", "global-application", title],
        })
few_shot_path = stage_root / "few_shots_runtime.json"
few_shot_path.write_text(
    json.dumps({"examples": few_shot_rows}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
log(f"few-shot loaded: {len(few_shot_rows)} item examples (3 cases × A-E)")

log("installing Gemma GPU runtime")
subprocess.run([
    sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
    "transformers==5.15.1", "accelerate==1.14.0", "bitsandbytes==0.50.1",
    "sentencepiece", "protobuf",
], check=True)

import torch
from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig

MODEL_ID = "google/gemma-4-31b-it"
if not torch.cuda.is_available():
    raise RuntimeError("Gemma 4 31B 운영에는 Colab GPU 런타임이 필요합니다")
compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
quantization = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=compute_dtype,
    bnb_4bit_use_double_quant=True,
)
log(f"loading LLM: {MODEL_ID} / GPU={torch.cuda.get_device_name(0)}")
processor = AutoProcessor.from_pretrained(MODEL_ID, token=hf_token)
model = AutoModelForMultimodalLM.from_pretrained(
    MODEL_ID,
    token=hf_token,
    quantization_config=quantization,
    dtype=compute_dtype,
    device_map="auto",
)
model.eval()
local_generator = TwoPassReviewGenerator(
    TransformersMultimodalGenerator(processor, model)
)
log("Gemma 모델 로드 완료")

bundle = None
server = None''',
)
runtime = runtime.replace(
    '        llm_base_url=llm_base_url,\n'
    '        llm_model=llm_model,\n'
    '        llm_api_key=llm_api_key,\n',
    '        few_shot_path=few_shot_path,\n'
    '        generator=local_generator,\n',
)
runtime = runtime.replace(
    '    log(f"ready: package={__version__}, model=CPU INT8, storage=/content only")',
    '    log(f"ready: package={__version__}, llm={MODEL_ID} 4-bit, embedding=CPU INT8, storage=/content only")',
)
runtime = runtime.replace(
    '    if llm_base_url:\n'
    '        log("generator: remote OpenAI-compatible endpoint with CPU fallback")\n'
    '    else:\n'
    '        log("generator: immediate CPU evidence-template fallback")',
    '    log("generator: in-process Gemma two-pass review with grounded fallback")',
)

notebook["cells"] = [
    {
        "cell_type": "markdown",
        "metadata": {},
        "source": source_lines(
            "# SemanticPromptTransfer v0.23 — Gemma 운영 개시\n\n"
            "위에서부터 모든 셀을 실행합니다. 먼저 FEW SHOT 1~3 셀에 실제 우수 심사역의 "
            "A~E 답안을 입력하세요. 세 사례는 유형 구분 없이 모든 건에 문체·분석 구조로 적용됩니다.\n\n"
            "Colab Secrets 필수값: `NGROK_AUTHTOKEN`, `HF_TOKEN`, `SPT_GATE_PASSWORD` "
            "(선택: `SPT_GATE_USER`). 토큰은 입력창이나 로그에 출력되지 않습니다.\n"
        ),
    },
    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source_lines(setup)},
    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source_lines(few_shot_cell(1))},
    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source_lines(few_shot_cell(2))},
    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source_lines(few_shot_cell(3))},
    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source_lines(runtime)},
]
notebook.setdefault("metadata", {})["colab"] = {
    "name": TARGET.name,
    "provenance": [],
    "gpuType": "L4",
}
notebook["metadata"]["accelerator"] = "GPU"
TARGET.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(TARGET)
