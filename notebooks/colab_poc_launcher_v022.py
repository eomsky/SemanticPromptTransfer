"""Single-cell Google Colab launcher for SemanticPromptTransfer v0.22.

The notebook generated from this source mounts the owner's Google Drive,
verifies and stages the approved package/model assets under /content, starts
the FastAPI application and packaged HTML on one port, and exposes one ngrok
URL protected by HTTP Basic Auth. User uploads and vectors never write back to
Google Drive.
"""

import atexit
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from getpass import getpass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


RELEASE = "v0.22"
PACKAGE_VERSION = "0.22.0"
DRIVE_MOUNT = Path("/content/drive")
DRIVE_ROOT = DRIVE_MOUNT / "MyDrive" / "SemanticPromptTransfer"
ASSET_MANIFEST = (
    DRIVE_ROOT
    / "runtime-assets"
    / RELEASE
    / f"SemanticPromptTransfer_{RELEASE}_COLAB_ASSETS.json"
)
PORT = 8000


def log(message: str) -> None:
    print(f"[SemanticPromptTransfer] {message}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_reset(path: Path, expected: str) -> None:
    resolved = path.resolve()
    if resolved.parent != Path("/content") or resolved.name != expected:
        raise RuntimeError(f"unsafe Colab cleanup target: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=False)


def secret(name: str, *, prompt: str | None = None, default: str = "") -> str:
    value = ""
    try:
        from google.colab import userdata

        value = str(userdata.get(name) or "").strip()
    except Exception:
        value = ""
    if not value and prompt:
        value = getpass(prompt).strip()
    return value or default


def stage_assets(manifest: dict, stage_root: Path) -> dict[str, Path]:
    staged: dict[str, Path] = {}
    for item in manifest["assets"]:
        source = (DRIVE_ROOT / item["source"]).resolve()
        target = (stage_root / item["target"]).resolve()
        if not source.is_relative_to(DRIVE_ROOT.resolve()):
            raise RuntimeError(f"asset escaped Drive root: {source}")
        if not target.is_relative_to(stage_root.resolve()):
            raise RuntimeError(f"asset escaped stage root: {target}")
        if not source.is_file():
            raise FileNotFoundError(f"Drive asset is missing: {source}")
        if source.stat().st_size != int(item["size"]):
            raise RuntimeError(f"Drive asset size mismatch: {source.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if target.stat().st_size != int(item["size"]):
            raise RuntimeError(f"staged asset size mismatch: {target.name}")
        if sha256(target) != item["sha256"]:
            raise RuntimeError(f"staged asset SHA-256 mismatch: {target.name}")
        staged[item["role"]] = target
        log(f"verified: {item['role']} ({target.stat().st_size:,} bytes)")
    return staged


def materialize_model(
    manifest: dict, staged: dict[str, Path], stage_root: Path
) -> Path:
    archive = staged["model_gzip"]
    specification = manifest["model_output"]
    target = (stage_root / specification["target"]).resolve()
    if not target.is_relative_to(stage_root.resolve()):
        raise RuntimeError(f"model output escaped stage root: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "rb") as source, target.open("wb") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    if target.stat().st_size != int(specification["size"]):
        raise RuntimeError("decompressed model size mismatch")
    if sha256(target) != specification["sha256"]:
        raise RuntimeError("decompressed model SHA-256 mismatch")
    staged["model"] = target
    log(f"verified: model output ({target.stat().st_size:,} bytes)")
    return target


def install_runtime(wheel: Path) -> None:
    requirement = f"semantic-prompt-transfer[poc] @ {wheel.resolve().as_uri()}"
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--disable-pip-version-check",
        requirement,
        "pyngrok==8.1.2",
    ]
    log("installing package and Colab web dependencies")
    subprocess.run(command, check=True)


previous_cleanup = globals().get("_SPT_COLAB_CLEANUP")
if callable(previous_cleanup):
    log("closing the previous POC process before restart")
    previous_cleanup()

from google.colab import drive

log("mounting Google Drive")
drive.mount(str(DRIVE_MOUNT), force_remount=False)

if not ASSET_MANIFEST.is_file():
    raise FileNotFoundError(
        "Colab asset manifest is missing. Expected: " + str(ASSET_MANIFEST)
    )
manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
if manifest.get("package_version") != PACKAGE_VERSION:
    raise RuntimeError("asset manifest package version mismatch")

stage_root = Path(manifest["stage_root"])
runtime_root = Path(manifest["runtime_root"])
safe_reset(stage_root, "spt_bootstrap_v022")
staged = stage_assets(manifest, stage_root)
materialize_model(manifest, staged, stage_root)
install_runtime(staged["wheel"])

ngrok_token = secret("NGROK_AUTHTOKEN", prompt="ngrok Authtoken: ")
if not ngrok_token:
    raise RuntimeError("NGROK_AUTHTOKEN is required")
gate_user = secret("SPT_GATE_USER", default="spt-poc")
gate_password = secret(
    "SPT_GATE_PASSWORD",
    prompt="POC URL 공통 접속 비밀번호(8자 이상): ",
)
if len(gate_password) < 8:
    raise RuntimeError("SPT_GATE_PASSWORD must contain at least 8 characters")

llm_base_url = secret("SPT_LLM_BASE_URL") or None
llm_model = secret("SPT_LLM_MODEL", default="local-credit-review-model")
llm_api_key = secret("SPT_LLM_API_KEY") or None

from fastapi.responses import FileResponse
from importlib.resources import files
from IPython.display import HTML, display
from pyngrok import ngrok
import uvicorn

from semantic_prompt_transfer import __version__, build_colab_poc

if __version__ != PACKAGE_VERSION:
    raise RuntimeError(f"installed package version mismatch: {__version__}")

bundle = None
server = None
server_thread = None
public_url = None

try:
    bundle = build_colab_poc(
        model_dir=staged["model"].parent.parent,
        root=runtime_root,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
    )
    app = bundle.app
    html_path = files("semantic_prompt_transfer.examples.operational").joinpath(
        "credit_review_upload_demo.html"
    )

    @app.get("/", include_in_schema=False)
    def poc_screen():
        return FileResponse(str(html_path), media_type="text/html")

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    health = None
    for _ in range(60):
        try:
            with urlopen(
                f"http://127.0.0.1:{PORT}/api/v1/runtime/health", timeout=2
            ) as response:
                if response.status == 200:
                    health = json.loads(response.read().decode("utf-8"))
                    break
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(1)
    if health is None:
        raise RuntimeError("POC API did not become healthy within 60 seconds")

    ngrok.set_auth_token(ngrok_token)
    tunnel = ngrok.connect(
        addr=PORT,
        proto="http",
        bind_tls=True,
        auth=f"{gate_user}:{gate_password}",
    )
    public_url = tunnel.public_url
    query = urlencode({"mode": "api", "api_base": public_url})
    launch_url = f"{public_url}/?{query}"

    def cleanup() -> None:
        global bundle, server, server_thread, public_url
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

    globals()["_SPT_COLAB_CLEANUP"] = cleanup
    atexit.register(cleanup)

    log(f"ready: package={__version__}, model=CPU INT8, storage=/content only")
    log(f"external gate user: {gate_user}")
    if llm_base_url:
        log("generator: remote OpenAI-compatible endpoint with CPU fallback")
    else:
        log("generator: immediate CPU evidence-template fallback")
    display(
        HTML(
            "<h3>SemanticPromptTransfer POC가 준비되었습니다.</h3>"
            f"<p><a href='{launch_url}' target='_blank' rel='noopener'>"
            "심사 화면 열기</a></p>"
            "<p>Colab 런타임을 종료하면 업로드·벡터·사용자 정보가 삭제됩니다.</p>"
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
    raise
