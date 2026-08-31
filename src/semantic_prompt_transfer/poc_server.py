"""Uvicorn entry point for the disposable Colab POC runtime.

Run with::

    uvicorn semantic_prompt_transfer.poc_server:app --host 0.0.0.0 --port 8000

All runtime data remains below ``SPT_POC_ROOT`` and is removed when the Python
process exits normally.  The root must not point to Google Drive.
"""

from __future__ import annotations

import atexit

from .poc_bootstrap import build_colab_poc_from_env


bundle = build_colab_poc_from_env()
app = bundle.app
atexit.register(bundle.close)

