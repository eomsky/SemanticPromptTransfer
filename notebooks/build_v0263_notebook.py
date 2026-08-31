"""Build the v0.26.3 Colab launcher with an isolated Ninja toolchain."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebooks" / "SemanticPromptTransfer_v0.26.2_COLAB_LAUNCHER.ipynb"
TARGET = ROOT / "notebooks" / "SemanticPromptTransfer_v0.26.3_COLAB_LAUNCHER.ipynb"


def source_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one occurrence: {old!r}")
    return text.replace(old, new, 1)


notebook = json.loads(SOURCE.read_text(encoding="utf-8"))

setup = "".join(notebook["cells"][1]["source"])
for old, new in (
    ("v0.26.2", "v0.26.3"),
    ("0.26.2", "0.26.3"),
    ("spt_bootstrap_v0262", "spt_bootstrap_v0263"),
):
    setup = setup.replace(old, new)

runtime = "".join(notebook["cells"][5]["source"])
runtime = replace_once(
    runtime,
    '    "-U", "vllm", "sentencepiece", "protobuf",\n',
    '    "-U", "vllm", "sentencepiece", "protobuf", "ninja",\n',
)
runtime = replace_once(
    runtime,
    'vllm_executable = vllm_env / "bin" / "vllm"\n',
    'vllm_executable = vllm_env / "bin" / "vllm"\n'
    'ninja_executable = vllm_env / "bin" / "ninja"\n',
)
runtime = replace_once(
    runtime,
    '''if not vllm_executable.is_file():
    raise RuntimeError(f"isolated vLLM executable is missing: {vllm_executable}")
''',
    '''if not vllm_executable.is_file():
    raise RuntimeError(f"isolated vLLM executable is missing: {vllm_executable}")
if not ninja_executable.is_file():
    raise RuntimeError(f"isolated Ninja executable is missing: {ninja_executable}")
ninja_probe = subprocess.run(
    [str(ninja_executable), "--version"],
    capture_output=True,
    text=True,
)
if ninja_probe.returncode != 0:
    raise RuntimeError(
        "isolated Ninja validation failed before model startup:\\n"
        + (ninja_probe.stderr or ninja_probe.stdout)[-4000:]
    )
log(f"vLLM build tool ready · ninja {ninja_probe.stdout.strip()}")
''',
)
runtime = replace_once(
    runtime,
    '''vllm_log_path = stage_root / "vllm.log"
vllm_log_handle = vllm_log_path.open("w", encoding="utf-8")
vllm_process = subprocess.Popen(
''',
    '''vllm_log_path = stage_root / "vllm.log"
vllm_log_handle = vllm_log_path.open("w", encoding="utf-8")
vllm_process_env = os.environ.copy()
vllm_process_env["PATH"] = (
    str(vllm_env / "bin")
    + os.pathsep
    + vllm_process_env.get("PATH", "")
)
vllm_process = subprocess.Popen(
''',
)
runtime = replace_once(
    runtime,
    '''    stdout=vllm_log_handle,
    stderr=subprocess.STDOUT,
    text=True,
)
log(f"loading native vLLM: {MODEL_ID} · A100 BF16 · concurrent sequences=4")
''',
    '''    stdout=vllm_log_handle,
    stderr=subprocess.STDOUT,
    text=True,
    env=vllm_process_env,
)
log(f"loading native vLLM: {MODEL_ID} · A100 BF16 · concurrent sequences=4")
''',
)

notebook["cells"][0]["source"] = source_lines(
    "# SemanticPromptTransfer v0.26.3 — Gemma 4 native vLLM 심사지원 에이전트 운영\n\n"
    "위에서부터 모든 셀을 실행합니다. 로그인과 ngrok 공통 비밀번호 없이 HTML이 바로 열립니다. "
    "브라우저별 난수 ID로 파일·벡터·생성 작업을 분리합니다.\n\n"
    "첨부된 우수 심사역 FEW SHOT 1~3이 기본값으로 입력되어 있으며 수정할 수 있습니다. "
    "신용조사서는 선택사항이고, 미첨부 시 사업보고서 등 첨부자료만으로 RAG를 구성합니다.\n\n"
    "Gemma 4 26B-A4B MoE는 native vLLM 구현으로 고정하고, vLLM과 Ninja를 같은 격리환경에 "
    "설치합니다. A100 80GB BF16 설정에서 최대 4개 요청을 연속 배칭합니다. "
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
