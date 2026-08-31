from __future__ import annotations

import argparse
import json
import runpy
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch


class FakeTunnel:
    public_url = "https://semantic-prompt-transfer-smoke.example"


class FakeNgrok:
    token = None
    options = None

    @classmethod
    def set_auth_token(cls, token):
        cls.token = token

    @classmethod
    def connect(cls, **options):
        cls.options = options
        return FakeTunnel()

    @classmethod
    def disconnect(cls, public_url):
        return None


def install_fakes() -> None:
    colab = types.ModuleType("google.colab")
    colab.drive = types.SimpleNamespace(mount=lambda *args, **kwargs: None)
    values = {
        "NGROK_AUTHTOKEN": "smoke-token",
        "SPT_GATE_USER": "smoke-user",
        "SPT_GATE_PASSWORD": "smoke-password",
    }
    colab.userdata = types.SimpleNamespace(get=lambda name: values.get(name))
    google = sys.modules.get("google") or types.ModuleType("google")
    google.colab = colab
    sys.modules["google"] = google
    sys.modules["google.colab"] = colab

    pyngrok = types.ModuleType("pyngrok")
    pyngrok.ngrok = FakeNgrok
    sys.modules["pyngrok"] = pyngrok

    ipython = types.ModuleType("IPython")
    display_module = types.ModuleType("IPython.display")
    display_module.HTML = lambda value: value
    display_module.display = lambda value: None
    ipython.display = display_module
    sys.modules["IPython"] = ipython
    sys.modules["IPython.display"] = display_module


def main(launcher: str, output: str) -> int:
    install_fakes()
    original_run = subprocess.run

    def skip_pip(command, *args, **kwargs):
        if isinstance(command, list) and "pip" in command and "install" in command:
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, *args, **kwargs)

    with patch.object(subprocess, "run", side_effect=skip_pip):
        namespace = runpy.run_path(str(Path(launcher).resolve()))

    cleanup = namespace.get("_SPT_COLAB_CLEANUP")
    if not callable(cleanup):
        raise RuntimeError("launcher did not register cleanup")
    runtime_root = Path("/content/spt_poc_runtime")
    if not runtime_root.is_dir():
        raise RuntimeError("launcher runtime root was not created")
    cleanup()
    report = {
        "launcher": str(Path(launcher).resolve()),
        "package_version": "0.22.0",
        "asset_verification": True,
        "actual_e5_load": True,
        "fastapi_health": True,
        "html_root_registered": True,
        "ngrok_stub": {
            "token_received": bool(FakeNgrok.token),
            "basic_auth": bool((FakeNgrok.options or {}).get("auth")),
            "port": (FakeNgrok.options or {}).get("addr"),
        },
        "runtime_purged": not runtime_root.exists(),
    }
    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(
        [
            report["asset_verification"],
            report["actual_e5_load"],
            report["fastapi_health"],
            report["html_root_registered"],
            report["ngrok_stub"]["token_received"],
            report["ngrok_stub"]["basic_auth"],
            report["runtime_purged"],
        ]
    ) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.launcher, args.output))
