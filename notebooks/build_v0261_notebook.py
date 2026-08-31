"""Build the v0.26.1 isolated-vLLM Colab launcher."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebooks" / "SemanticPromptTransfer_v0.26_COLAB_LAUNCHER.ipynb"
TARGET = ROOT / "notebooks" / "SemanticPromptTransfer_v0.26.1_COLAB_LAUNCHER.ipynb"
FEW_SHOTS = ROOT / "notebooks" / "few_shot_defaults_v1.json"


def source_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def few_shot_cell(number: int, value: dict) -> str:
    summary = str(value.get("case_summary") or "")
    answers = dict(value.get("answers") or {})
    fields = {
        "A": "가. 재무제표 주요계정(현황 및 향후전망)",
        "B": "나. 수익성(현황 및 향후전망)",
        "C": "다. 재무안정성 및 자산의 질(현황 및 향후전망)",
        "D": "라. 현금흐름 및 채무상환능력(현황 및 향후전망)",
        "E": "마. 주요 매출처 및 매출비중 변동 추이",
    }
    values = [summary, *(str(answers.get(key) or "") for key in fields)]
    if any('"""' in text for text in values):
        raise ValueError("few-shot defaults cannot contain triple double quotes")
    lines = [
        f"# FEW SHOT {number} — 실제 우수 심사역 답안 기본값",
        "# 모든 여신유형·업종에 일괄 적용하며, 사실이 아닌 문체·분석 구조로만 사용합니다.",
        f"FEW_SHOT_{number} = {{",
        '    "case_summary": """',
        summary,
        '""".strip(),',
        '    "answers": {',
    ]
    for key, title in fields.items():
        lines.extend(
            [
                f'        "{key}": """',
                str(answers.get(key) or ""),
                f'""".strip(),  # {title}',
            ]
        )
    lines.extend(["    },", "}", ""])
    return "\n".join(lines)


notebook = json.loads(SOURCE.read_text(encoding="utf-8"))
defaults = json.loads(FEW_SHOTS.read_text(encoding="utf-8"))["few_shots"]

setup = "".join(notebook["cells"][1]["source"])
for old, new in (
    ("v0.26", "v0.26.1"),
    ("0.26.0", "0.26.1"),
    ("spt_bootstrap_v026", "spt_bootstrap_v0261"),
):
    setup = setup.replace(old, new)

runtime = r'''ngrok_token = secret("NGROK_AUTHTOKEN")
if not ngrok_token:
    raise RuntimeError("Colab Secrets의 NGROK_AUTHTOKEN이 필요합니다")
hf_token = secret("HF_TOKEN")
if not hf_token:
    raise RuntimeError("Colab Secrets의 HF_TOKEN이 필요합니다")

import os
import secrets
from fastapi.responses import FileResponse
from importlib.resources import files
from IPython.display import HTML, display
from pyngrok import ngrok
from urllib.request import Request
import uvicorn

if shutil.which("nvidia-smi") is None:
    raise RuntimeError("Gemma 4 MoE 운영에는 Colab GPU 런타임이 필요합니다")

# 모델 다운로드 전에 ngrok 엔드포인트 중복 여부를 확인합니다. Basic Auth는 사용하지 않습니다.
public_url = None
ngrok.set_auth_token(ngrok_token)
try:
    ngrok.kill()
except Exception:
    pass
try:
    tunnel = ngrok.connect(addr=PORT, proto="http", bind_tls=True)
    public_url = tunnel.public_url
    log(f"ngrok endpoint reserved: {public_url}")
except Exception as exc:
    raise RuntimeError(
        "ngrok 엔드포인트가 이미 사용 중입니다. 기존 Colab 런타임을 종료한 뒤 다시 실행하세요."
    ) from exc

# 세 입력 셀을 심사항목별 3-shot(JSON 15개 레코드)으로 변환합니다.
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
        if answer:
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

log("installing isolated vLLM GPU runtime")
subprocess.run([
    sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
    "uv",
], check=True)
uv_command = shutil.which("uv") or "uv"
vllm_env = stage_root / "vllm-env"
subprocess.run([
    uv_command, "venv", "--python", sys.executable, "--seed", str(vllm_env),
], check=True)
vllm_python = vllm_env / "bin" / "python"
vllm_executable = vllm_env / "bin" / "vllm"
subprocess.run([
    uv_command, "pip", "install", "--python", str(vllm_python),
    "-U", "vllm", "sentencepiece", "protobuf",
    "--pre",
    "--extra-index-url", "https://wheels.vllm.ai/nightly/cu129",
    "--extra-index-url", "https://download.pytorch.org/whl/cu129",
    "--index-strategy", "unsafe-best-match",
], check=True)
if not vllm_executable.is_file():
    raise RuntimeError(f"isolated vLLM executable is missing: {vllm_executable}")

# vLLM 환경은 별도 프로세스에 격리한다. E5가 사용할 현재 커널의 NumPy,
# SciPy, torch, transformers는 변경하지 않으며 모델 다운로드 전에 검사한다.
dependency_probe = subprocess.run(
    [
        sys.executable,
        "-c",
        (
            "import numpy, torch; "
            "from transformers import AutoModel, AutoTokenizer; "
            "print(numpy.__version__, torch.__version__)"
        ),
    ],
    capture_output=True,
    text=True,
)
if dependency_probe.returncode != 0:
    raise RuntimeError(
        "E5 dependency validation failed before model download:\n"
        + (dependency_probe.stderr or dependency_probe.stdout)[-4000:]
    )
log("E5 dependency stack ready · vLLM environment isolated")

MODEL_ID = "google/gemma-4-26B-A4B-it"
VLLM_PORT = 8001
vllm_api_key = secrets.token_urlsafe(24)
os.environ["HF_TOKEN"] = hf_token
os.environ["HUGGING_FACE_HUB_TOKEN"] = hf_token
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
vllm_log_path = stage_root / "vllm.log"
vllm_log_handle = vllm_log_path.open("w", encoding="utf-8")
vllm_process = subprocess.Popen(
    [
        str(vllm_executable),
        "serve", MODEL_ID,
        "--host", "127.0.0.1",
        "--port", str(VLLM_PORT),
        "--served-model-name", MODEL_ID,
        "--api-key", vllm_api_key,
        "--dtype", "bfloat16",
        "--gpu-memory-utilization", "0.88",
        "--max-model-len", "16384",
        "--max-num-seqs", "4",
        "--max-num-batched-tokens", "8192",
        "--enable-prefix-caching",
        "--async-scheduling",
        "--limit-mm-per-prompt", json.dumps({"image": 0, "audio": 0}, separators=(",", ":")),
    ],
    stdout=vllm_log_handle,
    stderr=subprocess.STDOUT,
    text=True,
)
log(f"loading vLLM: {MODEL_ID} · concurrent sequences=4")
vllm_ready = False
for _ in range(1800):
    if vllm_process.poll() is not None:
        vllm_log_handle.flush()
        tail = vllm_log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        raise RuntimeError("vLLM startup failed:\n" + tail)
    try:
        call = Request(
            f"http://127.0.0.1:{VLLM_PORT}/v1/models",
            headers={"Authorization": f"Bearer {vllm_api_key}"},
        )
        with urlopen(call, timeout=2) as response:
            if response.status == 200:
                vllm_ready = True
                break
    except (URLError, TimeoutError, OSError):
        pass
    time.sleep(1)
if not vllm_ready:
    raise RuntimeError("vLLM did not become healthy within 30 minutes")
log("vLLM ready · streaming generation · 1400 tokens + automatic continuation")

# vLLM 설치가 끝난 뒤 현재 커널에서 애플리케이션을 처음 import합니다.
from semantic_prompt_transfer import (
    E5GpuEncoder,
    OpenAICompatibleHttpGenerator,
    RemoteGenerationConfig,
    __version__,
    build_colab_poc,
)

if __version__ != PACKAGE_VERSION:
    raise RuntimeError(f"installed package version mismatch: {__version__}")

# A100의 남은 메모리에서 작은 E5를 상주시켜 임베딩을 GPU 배치 처리합니다.
embedding_encoder = E5GpuEncoder(
    token=hf_token,
    batch_size=128,
    max_length=384,
    stride=32,
)
local_generator = OpenAICompatibleHttpGenerator(
    RemoteGenerationConfig(
        base_url=f"http://127.0.0.1:{VLLM_PORT}/v1",
        model=MODEL_ID,
        api_key=vllm_api_key,
        timeout_seconds=300,
        max_new_tokens=1400,
        max_continuations=2,
        temperature=0.0,
        allow_insecure_http=True,
    )
)

bundle = None
server = None
server_thread = None
try:
    bundle = build_colab_poc(
        model_dir=stage_root,
        root=runtime_root,
        few_shot_path=few_shot_path,
        encoder=embedding_encoder,
        generator=local_generator,
        anonymous_access=True,
    )
    app = bundle.app
    html_path = files("semantic_prompt_transfer.examples.operational").joinpath(
        "credit_review_upload_demo.html"
    )

    @app.get("/", include_in_schema=False)
    def poc_screen():
        return FileResponse(str(html_path), media_type="text/html")

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    health = None
    for _ in range(60):
        try:
            with urlopen(f"http://127.0.0.1:{PORT}/api/v1/runtime/health", timeout=2) as response:
                if response.status == 200:
                    health = json.loads(response.read().decode("utf-8"))
                    break
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    if health is None:
        raise RuntimeError("POC API did not become healthy within 60 seconds")

    query = urlencode({"mode": "api", "api_base": public_url})
    launch_url = f"{public_url}/?{query}"

    def cleanup() -> None:
        global bundle, server, server_thread, public_url, vllm_process, vllm_log_handle
        if public_url:
            try:
                ngrok.disconnect(public_url)
            except Exception:
                pass
            public_url = None
        if server is not None:
            server.should_exit = True
        if server_thread is not None and server_thread.is_alive():
            server_thread.join(timeout=10)
        if bundle is not None:
            try:
                bundle.close()
            finally:
                bundle = None
        if vllm_process is not None and vllm_process.poll() is None:
            vllm_process.terminate()
            try:
                vllm_process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                vllm_process.kill()
        if not vllm_log_handle.closed:
            vllm_log_handle.close()

    globals()["_SPT_COLAB_CLEANUP"] = cleanup
    atexit.register(cleanup)
    log(f"ready: package={__version__}, llm=vLLM/{MODEL_ID}, embedding=GPU E5, anonymous POC")
    log("multi-user routing: random browser scope + vLLM continuous batching (max 4)")
    display(
        HTML(
            "<h3>SemanticPromptTransfer POC가 준비되었습니다.</h3>"
            f"<p><a href='{launch_url}' target='_blank' rel='noopener'>심사 화면 바로 열기</a></p>"
            "<p>별도 로그인·공통 비밀번호가 없습니다. URL을 아는 사람은 접속할 수 있으며, "
            "Colab 종료 시 업로드·벡터·임시 ID가 삭제됩니다.</p>"
        )
    )
except Exception:
    if public_url:
        try:
            ngrok.disconnect(public_url)
        except Exception:
            pass
    if server is not None:
        server.should_exit = True
    if server_thread is not None and server_thread.is_alive():
        server_thread.join(timeout=10)
    if bundle is not None:
        bundle.close()
    if vllm_process is not None and vllm_process.poll() is None:
        vllm_process.terminate()
    if not vllm_log_handle.closed:
        vllm_log_handle.close()
    raise
'''

notebook["cells"][0]["source"] = source_lines(
    "# SemanticPromptTransfer v0.26.1 — 격리형 vLLM 심사지원 에이전트 운영\n\n"
    "위에서부터 모든 셀을 실행합니다. 로그인과 ngrok 공통 비밀번호 없이 HTML이 바로 열립니다. "
    "브라우저별 난수 ID로 파일·벡터·생성 작업을 분리합니다.\n\n"
    "첨부된 우수 심사역 FEW SHOT 1~3이 기본값으로 입력되어 있으며 수정할 수 있습니다. "
    "신용조사서는 선택사항이고, 미첨부 시 사업보고서 등 첨부자료만으로 RAG를 구성합니다.\n\n"
    "Gemma 4 26B-A4B MoE를 vLLM으로 실행해 최대 4개 요청을 연속 배칭하고, "
    "A→E는 각 사용자 작업 안에서 순차 생성합니다. "
    "Colab Secrets 필수값은 `NGROK_AUTHTOKEN`, `HF_TOKEN` 두 개뿐입니다.\n"
)
notebook["cells"][1]["source"] = source_lines(setup)
for number in (1, 2, 3):
    notebook["cells"][number + 1]["source"] = source_lines(
        few_shot_cell(number, defaults[f"FEW_SHOT_{number}"])
    )
notebook["cells"][5]["source"] = source_lines(runtime)
notebook.setdefault("metadata", {}).setdefault("colab", {})["name"] = TARGET.name
TARGET.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(TARGET)

