from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(source: Path, output: Path) -> None:
    code = source.read_text(encoding="utf-8")
    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# SemanticPromptTransfer v0.22 — 운영 개시\n",
                    "\n",
                    "아래 코드 셀 하나를 실행하면 Google Drive의 검증 자산을 읽어 "
                    "POC 서버와 HTML을 기동합니다.\n",
                    "\n",
                    "Colab Secrets에 `NGROK_AUTHTOKEN`과 8자 이상의 "
                    "`SPT_GATE_PASSWORD`를 저장하면 추가 입력 없이 실행됩니다. "
                    "`SPT_GATE_USER`의 기본값은 `spt-poc`입니다.\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {"cellView": "form"},
                "outputs": [],
                "source": [line + "\n" for line in code.splitlines()],
            },
        ],
        "metadata": {
            "colab": {
                "name": output.name,
                "provenance": [],
            },
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
