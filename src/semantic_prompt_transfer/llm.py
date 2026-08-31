from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, parse, request


class TextGenerator(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class CpuGenerationConfig:
    model_id: str = "Qwen/Qwen2.5-0.5B-Instruct"
    max_new_tokens: int = 256
    context_max_chars: int = 16000
    num_threads: int | None = None
    local_files_only: bool = False


@dataclass(frozen=True)
class RemoteGenerationConfig:
    """OpenAI-compatible endpoint configuration for a separately started LLM Colab."""

    base_url: str
    model: str
    api_key: str | None = None
    api_key_env: str = "SPT_LLM_API_KEY"
    timeout_seconds: float = 120.0
    max_new_tokens: int = 512
    temperature: float = 0.0
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        parsed = parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("remote LLM base_url must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and not self.allow_insecure_http:
            raise ValueError("remote LLM requires HTTPS unless allow_insecure_http=True")
        if not self.model.strip():
            raise ValueError("remote LLM model is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


class OpenAICompatibleHttpGenerator:
    """Small dependency-free adapter for a remote Colab LLM endpoint."""

    def __init__(self, config: RemoteGenerationConfig) -> None:
        self.config = config

    def _url(self) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"

    def generate(self, messages: list[dict[str, str]]) -> str:
        payload = json.dumps(
            {
                "model": self.config.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_new_tokens,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        api_key = self.config.api_key or os.environ.get(self.config.api_key_env)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        call = request.Request(self._url(), data=payload, headers=headers, method="POST")
        try:
            with request.urlopen(call, timeout=self.config.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise RuntimeError(f"remote LLM HTTP {exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"remote LLM request failed: {exc}") from exc
        try:
            text = result["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError, TypeError) as exc:
            raise RuntimeError("remote LLM response does not contain message content") from exc
        if not text:
            raise RuntimeError("remote LLM returned empty text")
        return text


class EvidenceTemplateGenerator:
    """Instant CPU fallback that never invents facts outside the evidence block."""

    _evidence = re.compile(
        r"\[TIER_(?P<tier>[123]) EVIDENCE\]\n"
        r"evidence_id=(?P<id>[^\n]+)\n"
        r"document_id=[^\n]*\n"
        r"source_filename=[^\n]*\n"
        r"page=[^\n]*\n"
        r"content=(?P<content>.*?)(?=\n\n\[|\n\n\[작성요청\]|\Z)",
        re.DOTALL,
    )

    def __init__(self, max_evidence: int = 3, max_content_chars: int = 420) -> None:
        self.max_evidence = int(max_evidence)
        self.max_content_chars = int(max_content_chars)

    def generate(self, messages: list[dict[str, str]]) -> str:
        user = next(
            (message.get("content", "") for message in reversed(messages) if message.get("role") == "user"),
            "",
        )
        matches = list(self._evidence.finditer(user))
        if not matches:
            raise ValueError("current-case evidence is required for fallback generation")
        selected = sorted(matches, key=lambda row: int(row.group("tier")))[: self.max_evidence]
        statements = []
        for row in selected:
            content = " ".join(row.group("content").split())[: self.max_content_chars].rstrip()
            statements.append(f"{content} [{row.group('id')}]")
        return (
            "확인된 기초자료에 따르면 "
            + " ".join(statements)
            + " 현재 자료 범위에서 현황을 우선 확인하였으며, 향후 전망은 추가 실적과 "
            "변동 요인을 함께 점검하는 보수적 접근이 필요하다."
        )


class TransformersCpuGenerator:
    """Lazy, replaceable CPU adapter for a small Hugging Face causal language model."""

    def __init__(
        self,
        config: CpuGenerationConfig | None = None,
        *,
        tokenizer: Any | None = None,
        model: Any | None = None,
    ) -> None:
        self.config = config or CpuGenerationConfig()
        self._tokenizer = tokenizer
        self._model = model

    def _load(self) -> None:
        if self._tokenizer is not None and self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "install semantic-prompt-transfer[llm-cpu] to use TransformersCpuGenerator"
            ) from exc
        if self.config.num_threads:
            torch.set_num_threads(self.config.num_threads)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.config.model_id,
            local_files_only=self.config.local_files_only,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.config.model_id,
            device_map=None,
            low_cpu_mem_usage=True,
            local_files_only=self.config.local_files_only,
        )
        self._model.to("cpu")
        self._model.eval()

    def generate(self, messages: list[dict[str, str]]) -> str:
        self._load()
        clipped = [dict(message) for message in messages]
        if clipped:
            clipped[-1]["content"] = clipped[-1].get("content", "")[-self.config.context_max_chars :]
        prompt = self._tokenizer.apply_chat_template(
            clipped,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt")
        generated = self._model.generate(
            **inputs,
            max_new_tokens=self.config.max_new_tokens,
            do_sample=False,
            num_beams=1,
            use_cache=True,
            pad_token_id=self._tokenizer.eos_token_id,
        )
        input_length = int(inputs["input_ids"].shape[-1])
        text = self._tokenizer.decode(generated[0][input_length:], skip_special_tokens=True).strip()
        if not text:
            raise RuntimeError("CPU language model returned empty text")
        return text


class FallbackGenerator:
    """Try the configured LLM, then produce a grounded draft if loading/generation fails."""

    def __init__(self, primary: TextGenerator, fallback: TextGenerator | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or EvidenceTemplateGenerator()
        self.last_backend = "not_run"
        self.last_error: str | None = None

    @staticmethod
    def _grounded(text: str, messages: list[dict[str, str]]) -> bool:
        user = next(
            (message.get("content", "") for message in reversed(messages) if message.get("role") == "user"),
            "",
        )
        evidence_text = user.split("[CURRENT_CASE_EVIDENCE]", 1)[-1]
        evidence_text = evidence_text.split("[작성요청]", 1)[0]
        evidence_ids = set(re.findall(r"evidence_id=([^\n]+)", evidence_text))
        if evidence_ids and not any(evidence_id in text for evidence_id in evidence_ids):
            return False
        scrubbed = text
        for evidence_id in evidence_ids:
            scrubbed = scrubbed.replace(evidence_id, "")
        number_pattern = re.compile(r"(?<![A-Za-z_])\d[\d,]*(?:\.\d+)?%?")
        evidence_numbers = {value.replace(",", "") for value in number_pattern.findall(evidence_text)}
        output_numbers = {value.replace(",", "") for value in number_pattern.findall(scrubbed)}
        return output_numbers.issubset(evidence_numbers)

    def generate(self, messages: list[dict[str, str]]) -> str:
        try:
            text = self.primary.generate(messages)
            if not self._grounded(text, messages):
                raise ValueError("primary output failed the grounding precheck")
            self.last_backend = type(self.primary).__name__
            self.last_error = None
            return text
        except Exception as exc:
            self.last_backend = type(self.fallback).__name__
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self.fallback.generate(messages)


def default_cpu_generator(
    config: CpuGenerationConfig | None = None,
) -> FallbackGenerator:
    return FallbackGenerator(TransformersCpuGenerator(config), EvidenceTemplateGenerator())
