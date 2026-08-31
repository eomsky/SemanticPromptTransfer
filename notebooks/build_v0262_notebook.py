"""Build the v0.26.2 native-vLLM Gemma 4 Colab launcher."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebooks" / "SemanticPromptTransfer_v0.26.1_COLAB_LAUNCHER.ipynb"
TARGET = ROOT / "notebooks" / "SemanticPromptTransfer_v0.26.2_COLAB_LAUNCHER.ipynb"


def source_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one occurrence: {old!r}")
    return text.replace(old, new, 1)


notebook = json.loads(SOURCE.read_text(encoding="utf-8"))

setup = "".join(notebook["cells"][1]["source"])
for old, new in (
    ("v0.26.1", "v0.26.2"),
    ("0.26.1", "0.26.2"),
    ("spt_bootstrap_v0261", "spt_bootstrap_v0262"),
):
    setup = setup.replace(old, new)

runtime = "".join(notebook["cells"][5]["source"])
runtime = replace_once(
    runtime,
    '        "--dtype", "bfloat16",\n',
    '        "--dtype", "bfloat16",\n'
    '        "--model-impl", "vllm",\n',
)
runtime = replace_once(
    runtime,
    '        "--gpu-memory-utilization", "0.88",\n',
    '        "--gpu-memory-utilization", "0.90",\n',
)
runtime = replace_once(
    runtime,
    'log(f"loading vLLM: {MODEL_ID} · concurrent sequences=4")\n',
    'log(f"loading native vLLM: {MODEL_ID} · A100 BF16 · concurrent sequences=4")\n',
)

old_failure = '''    if vllm_process.poll() is not None:
        vllm_log_handle.flush()
        tail = vllm_log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        raise RuntimeError("vLLM startup failed:\\n" + tail)
'''
new_failure = '''    if vllm_process.poll() is not None:
        vllm_log_handle.flush()
        full_log = vllm_log_path.read_text(encoding="utf-8", errors="replace")
        marker = full_log.find("EngineCore failed to start")
        if marker >= 0:
            start = max(0, marker - 2000)
            excerpt = full_log[start : start + 30000]
            if start + 30000 < len(full_log):
                excerpt += "\\n... [middle omitted] ...\\n" + full_log[-4000:]
        else:
            excerpt = full_log[-30000:]
        raise RuntimeError(
            f"vLLM startup failed. Full log: {vllm_log_path}\\n" + excerpt
        )
'''
runtime = replace_once(runtime, old_failure, new_failure)

notebook["cells"][0]["source"] = source_lines(
    "# SemanticPromptTransfer v0.26.2 — Gemma 4 native vLLM 심사지원 에이전트 운영\n\n"
    "위에서부터 모든 셀을 실행합니다. 로그인과 ngrok 공통 비밀번호 없이 HTML이 바로 열립니다. "
    "브라우저별 난수 ID로 파일·벡터·생성 작업을 분리합니다.\n\n"
    "첨부된 우수 심사역 FEW SHOT 1~3이 기본값으로 입력되어 있으며 수정할 수 있습니다. "
    "신용조사서는 선택사항이고, 미첨부 시 사업보고서 등 첨부자료만으로 RAG를 구성합니다.\n\n"
    "Gemma 4 26B-A4B MoE는 Transformers 대체 백엔드가 아니라 native vLLM 구현으로 고정합니다. "
    "A100 80GB 공식 BF16 설정을 사용하고 최대 4개 요청을 연속 배칭합니다. "
    "Colab Secrets 필수값은 `NGROK_AUTHTOKEN`, `HF_TOKEN` 두 개뿐입니다.\n"
)
notebook["cells"][1]["source"] = source_lines(setup)
notebook["cells"][5]["source"] = source_lines(runtime)
notebook.setdefault("metadata", {}).setdefault("colab", {})["name"] = TARGET.name
TARGET.write_text(
    json.dumps(notebook, ensure_ascii=False, indent=1) + "\n",
    encoding="utf-8",
)
print(TARGET)
