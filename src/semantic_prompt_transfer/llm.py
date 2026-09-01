from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Generator, Iterator, Protocol
from urllib import error, parse, request


class TextGenerator(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str: ...


class StreamingTextGenerator(TextGenerator, Protocol):
    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]: ...


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
    max_new_tokens: int = 1400
    max_continuations: int = 2
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
        if self.max_new_tokens < 1:
            raise ValueError("max_new_tokens must be positive")
        if self.max_continuations < 0:
            raise ValueError("max_continuations cannot be negative")


class OpenAICompatibleHttpGenerator:
    """Small dependency-free adapter for a remote Colab LLM endpoint."""

    def __init__(self, config: RemoteGenerationConfig) -> None:
        self.config = config

    def _url(self) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"

    def _payload(self, messages: list[dict[str, str]], *, stream: bool) -> bytes:
        return json.dumps(
            {
                "model": self.config.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_new_tokens,
                "stream": stream,
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def _request(self, payload: bytes, *, accept: str) -> request.Request:
        api_key = self.config.api_key or os.environ.get(self.config.api_key_env)
        headers = {"Content-Type": "application/json", "Accept": accept}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return request.Request(self._url(), data=payload, headers=headers, method="POST")

    def generate(self, messages: list[dict[str, str]]) -> str:
        working = [dict(row) for row in messages]
        pieces: list[str] = []
        for continuation in range(self.config.max_continuations + 1):
            call = self._request(self._payload(working, stream=False), accept="application/json")
            try:
                with request.urlopen(call, timeout=self.config.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                detail = exc.read(500).decode("utf-8", errors="replace")
                raise RuntimeError(f"remote LLM HTTP {exc.code}: {detail}") from exc
            except (error.URLError, TimeoutError) as exc:
                raise RuntimeError(f"remote LLM request failed: {exc}") from exc
            try:
                choice = result["choices"][0]
                text = choice["message"]["content"].strip()
                finish_reason = choice.get("finish_reason")
            except (KeyError, IndexError, AttributeError, TypeError) as exc:
                raise RuntimeError("remote LLM response does not contain message content") from exc
            if not text:
                raise RuntimeError("remote LLM returned empty text")
            pieces.append(text)
            if finish_reason != "length":
                return "".join(pieces).strip()
            if continuation >= self.config.max_continuations:
                break
            working = self._continuation_messages(messages, "".join(pieces))
        raise RuntimeError("remote LLM could not complete the response within continuation limit")

    @staticmethod
    def _continuation_messages(
        messages: list[dict[str, str]], generated: str
    ) -> list[dict[str, str]]:
        return [
            *[dict(row) for row in messages],
            {"role": "assistant", "content": generated},
            {
                "role": "user",
                "content": (
                    "직전 응답이 토큰 한도로 중단되었다. 이미 작성한 내용을 반복하지 말고 "
                    "중단된 문장부터 이어서 전체 답변을 완결된 문장으로 마무리하라."
                ),
            },
        ]

    def _stream_once(
        self, messages: list[dict[str, str]]
    ) -> Generator[str, None, str | None]:
        call = self._request(self._payload(messages, stream=True), accept="text/event-stream")
        finish_reason: str | None = None
        try:
            with request.urlopen(call, timeout=self.config.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return finish_reason
                    try:
                        event = json.loads(data)
                        choice = event["choices"][0]
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if choice.get("finish_reason") is not None:
                            finish_reason = str(choice["finish_reason"])
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                        raise RuntimeError("remote LLM stream contains an invalid event") from exc
                    if content:
                        yield str(content)
        except error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise RuntimeError(f"remote LLM HTTP {exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"remote LLM stream failed: {exc}") from exc
        return finish_reason

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        """Yield SSE deltas and continue automatically when vLLM stops on length."""
        working = [dict(row) for row in messages]
        generated: list[str] = []
        for continuation in range(self.config.max_continuations + 1):
            current = self._stream_once(working)
            while True:
                try:
                    token = next(current)
                except StopIteration as stopped:
                    finish_reason = stopped.value
                    break
                generated.append(token)
                yield token
            if finish_reason != "length":
                return
            if continuation >= self.config.max_continuations:
                raise RuntimeError(
                    "remote LLM could not complete the response within continuation limit"
                )
            working = self._continuation_messages(messages, "".join(generated))


class EvidenceTemplateGenerator:
    """Instant deterministic fallback that copies only current-case evidence."""

    _modern_evidence = re.compile(
        r"\[(?P<source>CREDIT_REPORT|ATTACHMENT) EVIDENCE\]\n"
        r"source_class=[^\n]*\n"
        r"evidence_id=(?P<id>[^\n]+)\n"
        r"document_id=[^\n]*\n"
        r"source_filename=[^\n]*\n"
        r"page=[^\n]*\n"
        r"direct_conflict_credit_ids=(?P<conflicts>[^\n]*)\n"
        r"content=(?P<content>.*?)(?=\n\n\[|\n\n\[작성요청\]|\Z)",
        re.DOTALL,
    )
    _legacy_evidence = re.compile(
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
        modern = list(self._modern_evidence.finditer(user))
        if modern:
            safe = [m for m in modern if not (m.group("source") == "ATTACHMENT" and m.group("conflicts").strip())]
            selected = (safe or modern)[: self.max_evidence]
        else:
            legacy = list(self._legacy_evidence.finditer(user))
            selected = legacy[: self.max_evidence]
        if not selected:
            return "현재 제공된 자료에서 해당 심사항목의 직접 근거를 확인하지 못해 추가 확인이 필요하다."
        statements = []
        for row in selected:
            content = " ".join(row.group("content").split())[: self.max_content_chars].rstrip()
            if content:
                statements.append(f"{content} [{row.group('id')}]")
        if not statements:
            return f"확인된 근거자료를 기준으로 추가 검토가 필요하다. [{selected[0].group('id')}]"
        return "확인된 근거자료상 " + " ".join(statements)


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


@dataclass(frozen=True)
class MultimodalGenerationConfig:
    """Generation settings for a Gemma-style processor/model already loaded on GPU."""

    max_new_tokens: int = 1200
    repetition_penalty: float = 1.05
    enable_thinking: bool = False


class TransformersMultimodalGenerator:
    """Adapter for AutoProcessor + AutoModelForMultimodalLM in the same Colab."""

    def __init__(self, processor: Any, model: Any, config: MultimodalGenerationConfig | None = None) -> None:
        self.processor = processor
        self.model = model
        self.config = config or MultimodalGenerationConfig()
        self.tokenizer = getattr(processor, "tokenizer", processor)

    @staticmethod
    def _structured(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "role": str(message.get("role") or "user"),
                "content": [{"type": "text", "text": str(message.get("content") or "")}],
            }
            for message in messages
        ]

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        import gc
        import threading
        import torch
        from transformers import TextIteratorStreamer

        inputs = self.processor.apply_chat_template(
            self._structured(messages),
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=self.config.enable_thinking,
        ).to(self.model.device)
        streamer = TextIteratorStreamer(
            self.tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
            timeout=120.0,
        )
        errors: list[BaseException] = []

        def run_generation() -> None:
            try:
                with torch.inference_mode():
                    self.model.generate(
                        **inputs,
                        streamer=streamer,
                        max_new_tokens=self.config.max_new_tokens,
                        do_sample=False,
                        repetition_penalty=self.config.repetition_penalty,
                        use_cache=True,
                    )
            except BaseException as exc:  # propagated after the streamer closes or times out
                errors.append(exc)

        worker = threading.Thread(target=run_generation, daemon=True)
        worker.start()
        try:
            for token_text in streamer:
                if token_text:
                    yield token_text
        finally:
            worker.join(timeout=10)
            del inputs
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        if errors:
            raise RuntimeError(f"multimodal generation failed: {errors[0]}") from errors[0]

    def generate(self, messages: list[dict[str, str]]) -> str:
        text = "".join(self.stream(messages)).strip()
        if not text:
            raise RuntimeError("multimodal language model returned empty text")
        return text


def default_cpu_generator(
    config: CpuGenerationConfig | None = None,
) -> TransformersCpuGenerator:
    return TransformersCpuGenerator(config)
