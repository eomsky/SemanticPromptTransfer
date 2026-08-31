"""Build the v0.24 single-LLM, streaming-evidence Colab launcher."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebooks" / "SemanticPromptTransfer_v0.23_COLAB_LAUNCHER.ipynb"
TARGET = ROOT / "notebooks" / "SemanticPromptTransfer_v0.24_COLAB_LAUNCHER.ipynb"


def source_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


notebook = json.loads(SOURCE.read_text(encoding="utf-8"))
setup = "".join(notebook["cells"][1]["source"])
setup = setup.replace("v0.23", "v0.24").replace("0.23.0", "0.24.0")
setup = setup.replace("spt_bootstrap_v023", "spt_bootstrap_v024")

runtime = "".join(notebook["cells"][5]["source"])
runtime = runtime.replace(
    "    TransformersMultimodalGenerator,\n    TwoPassReviewGenerator,",
    "    MultimodalGenerationConfig,\n    TransformersMultimodalGenerator,",
)
runtime = runtime.replace(
    "if __version__ != PACKAGE_VERSION:\n"
    "    raise RuntimeError(f\"installed package version mismatch: {__version__}\")\n",
    "if __version__ != PACKAGE_VERSION:\n"
    "    raise RuntimeError(f\"installed package version mismatch: {__version__}\")\n\n"
    "# 모델 다운로드 전에 ngrok 엔드포인트를 예약해 중복 세션 오류를 즉시 확인합니다.\n"
    "public_url = None\n"
    "ngrok.set_auth_token(ngrok_token)\n"
    "try:\n"
    "    ngrok.kill()\n"
    "except Exception:\n"
    "    pass\n"
    "try:\n"
    "    tunnel = ngrok.connect(\n"
    "        addr=PORT,\n"
    "        proto=\"http\",\n"
    "        bind_tls=True,\n"
    "        auth=f\"{gate_user}:{gate_password}\",\n"
    "    )\n"
    "    public_url = tunnel.public_url\n"
    "    log(f\"ngrok endpoint reserved: {public_url}\")\n"
    "except Exception as exc:\n"
    "    raise RuntimeError(\n"
    "        \"ngrok 엔드포인트가 이미 사용 중입니다. 기존 Colab 런타임의 실행 셀을 \"\n"
    "        \"중지하거나 런타임을 종료한 뒤 이 셀을 다시 실행하세요. 모델 다운로드 전 확인에서 중단했습니다.\"\n"
    "    ) from exc\n",
)
runtime = runtime.replace(
    'log(f"loading LLM: {MODEL_ID} / GPU={torch.cuda.get_device_name(0)}")\n'
    'processor = AutoProcessor.from_pretrained(MODEL_ID, token=hf_token)\n'
    'model = AutoModelForMultimodalLM.from_pretrained(\n'
    '    MODEL_ID,\n'
    '    token=hf_token,\n'
    '    quantization_config=quantization,\n'
    '    dtype=compute_dtype,\n'
    '    device_map="auto",\n'
    ')\n'
    'model.eval()\n'
    'local_generator = TwoPassReviewGenerator(\n'
    '    TransformersMultimodalGenerator(processor, model)\n'
    ')\n'
    'log("Gemma 모델 로드 완료")\n',
    'try:\n'
    '    log(f"loading LLM: {MODEL_ID} / GPU={torch.cuda.get_device_name(0)}")\n'
    '    cached_processor = globals().get("_SPT_GEMMA_PROCESSOR")\n'
    '    cached_model = globals().get("_SPT_GEMMA_MODEL")\n'
    '    if cached_processor is not None and cached_model is not None:\n'
    '        processor, model = cached_processor, cached_model\n'
    '        log("reusing the loaded Gemma model")\n'
    '    else:\n'
    '        processor = AutoProcessor.from_pretrained(MODEL_ID, token=hf_token)\n'
    '        model = AutoModelForMultimodalLM.from_pretrained(\n'
    '            MODEL_ID,\n'
    '            token=hf_token,\n'
    '            quantization_config=quantization,\n'
    '            dtype=compute_dtype,\n'
    '            device_map="auto",\n'
    '        )\n'
    '        model.eval()\n'
    '        globals()["_SPT_GEMMA_PROCESSOR"] = processor\n'
    '        globals()["_SPT_GEMMA_MODEL"] = model\n'
    '    local_generator = TransformersMultimodalGenerator(\n'
    '        processor,\n'
    '        model,\n'
    '        MultimodalGenerationConfig(\n'
    '            max_new_tokens=1200,\n'
    '            repetition_penalty=1.05,\n'
    '            enable_thinking=False,\n'
    '        ),\n'
    '    )\n'
    '    log("Gemma 단일 생성 모델 로드 완료 · 항목당 최대 1,200 tokens")\n'
    'except Exception:\n'
    '    if public_url:\n'
    '        try:\n'
    '            ngrok.disconnect(public_url)\n'
    '        except Exception:\n'
    '            pass\n'
    '    raise\n',
)
runtime = runtime.replace(
    "bundle = None\nserver = None\nserver_thread = None\npublic_url = None",
    "bundle = None\nserver = None\nserver_thread = None",
)
runtime = runtime.replace(
    '''    ngrok.set_auth_token(ngrok_token)
    tunnel = ngrok.connect(
        addr=PORT,
        proto="http",
        bind_tls=True,
        auth=f"{gate_user}:{gate_password}",
    )
    public_url = tunnel.public_url
''',
    "",
)
runtime = runtime.replace(
    '    log("generator: in-process Gemma two-pass review with grounded fallback")',
    '    log("generator: one in-process Gemma; A→B→C→D→E sequential streaming")',
)

for old, new in (("v0.23", "v0.24"), ("0.23.0", "0.24.0"), ("spt_bootstrap_v023", "spt_bootstrap_v024")):
    runtime = runtime.replace(old, new)

notebook["cells"][0]["source"] = source_lines(
    "# SemanticPromptTransfer v0.24 — 단일 Gemma 운영 개시\n\n"
    "위에서부터 모든 셀을 실행합니다. FEW SHOT 1~3 셀에는 우수 심사역의 A~E 답안을 입력합니다. "
    "세 사례는 유형 구분 없이 문체와 분석 구조에만 일괄 적용됩니다.\n\n"
    "생성 버튼을 누를 때 자료 분석과 벡터 임베딩이 시작되며, Gemma가 A→E를 한 항목씩 생성합니다. "
    "화면에는 생성 내용이 실시간 표시되고, 근거 연결 문구를 누르면 원문 캡처가 열립니다.\n\n"
    "Colab Secrets 필수값: `NGROK_AUTHTOKEN`, `HF_TOKEN`, `SPT_GATE_PASSWORD` "
    "(선택: `SPT_GATE_USER`). 토큰은 별도로 입력하거나 로그에 출력하지 않습니다.\n"
)
notebook["cells"][1]["source"] = source_lines(setup)
notebook["cells"][5]["source"] = source_lines(runtime)
notebook.setdefault("metadata", {}).setdefault("colab", {})["name"] = TARGET.name
TARGET.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(TARGET)
