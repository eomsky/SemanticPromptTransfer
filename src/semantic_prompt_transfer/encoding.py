from __future__ import annotations

import hashlib
import json
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterable

import numpy as np


class EncoderBackend(ABC):
    @abstractmethod
    def encode_documents(self, texts: Iterable[str]) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def encode_queries(self, texts: Iterable[str]) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def metadata(self) -> dict:
        raise NotImplementedError


class E5OnnxEncoder(EncoderBackend):
    """CPU-only multilingual E5 encoder using the official INT8 ONNX export."""

    MODEL_ID = "intfloat/multilingual-e5-small"

    def __init__(
        self,
        model_dir: str | Path,
        batch_size: int = 32,
        max_length: int = 512,
        intra_op_threads: int = 0,
    ) -> None:
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "E5 ONNX requires onnxruntime and tokenizers; no fallback is used"
            ) from exc

        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / "onnx/model_qint8_avx512_vnni.onnx"
        self.tokenizer_path = self.model_dir / "onnx/tokenizer.json"
        missing = [
            str(path)
            for path in (self.model_path, self.tokenizer_path)
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(f"missing E5 model files: {missing}")
        self.batch_size = int(batch_size)
        self.max_length = int(max_length)
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self.tokenizer.enable_truncation(
            max_length=self.max_length,
            stride=min(32, max(self.max_length // 8, 0)),
        )
        pad_id = self.tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise ValueError("E5 tokenizer is missing <pad>")
        self.pad_id = int(pad_id)
        options = ort.SessionOptions()
        if intra_op_threads > 0:
            options.intra_op_num_threads = int(intra_op_threads)
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(self.model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        digest = hashlib.sha256()
        with self.model_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        self._model_sha256 = digest.hexdigest()
        self._seconds = 0.0
        self._texts = 0
        self._tokens = 0
        self._encode_lock = threading.RLock()

    @staticmethod
    def _normalize(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def _encode(self, texts: Iterable[str], prefix: str) -> np.ndarray:
        values = [prefix + str(text or "") for text in texts]
        if not values:
            return np.empty((0, 384), dtype=np.float32)
        batches = []
        started = time.perf_counter()
        for start in range(0, len(values), self.batch_size):
            batch = values[start : start + self.batch_size]
            roots = self.tokenizer.encode_batch(batch)
            segments = []
            owners = []
            for owner, root in enumerate(roots):
                for segment in [root, *root.overflowing]:
                    segments.append(segment)
                    owners.append(owner)
            owner_vectors: list[list[np.ndarray]] = [[] for _ in batch]
            for segment_start in range(0, len(segments), self.batch_size):
                encoded = segments[segment_start : segment_start + self.batch_size]
                encoded_owners = owners[
                    segment_start : segment_start + self.batch_size
                ]
                max_len = max(len(item.ids) for item in encoded)
                input_ids = np.full(
                    (len(encoded), max_len), self.pad_id, dtype=np.int64
                )
                attention = np.zeros((len(encoded), max_len), dtype=np.int64)
                token_types = np.zeros((len(encoded), max_len), dtype=np.int64)
                for row, item in enumerate(encoded):
                    length = len(item.ids)
                    input_ids[row, :length] = item.ids
                    attention[row, :length] = item.attention_mask
                    token_types[row, :length] = item.type_ids
                hidden = self.session.run(
                    ["last_hidden_state"],
                    {
                        "input_ids": input_ids,
                        "attention_mask": attention,
                        "token_type_ids": token_types,
                    },
                )[0]
                mask = attention[..., None].astype(np.float32)
                pooled = (hidden * mask).sum(axis=1) / np.maximum(
                    mask.sum(axis=1), 1e-9
                )
                pooled = self._normalize(pooled.astype(np.float32))
                for row, owner in enumerate(encoded_owners):
                    owner_vectors[owner].append(pooled[row])
                self._tokens += int(attention.sum())
            aggregated = np.vstack(
                [np.mean(vectors, axis=0) for vectors in owner_vectors]
            ).astype(np.float32)
            batches.append(self._normalize(aggregated))
        self._seconds += time.perf_counter() - started
        self._texts += len(values)
        return np.vstack(batches)

    def encode_documents(self, texts: Iterable[str]) -> np.ndarray:
        return self._encode(texts, "passage: ")

    def encode_queries(self, texts: Iterable[str]) -> np.ndarray:
        return self._encode(texts, "query: ")

    def metadata(self) -> dict:
        return {
            "provider": "onnxruntime",
            "model_id": self.MODEL_ID,
            "model_file": self.model_path.name,
            "model_sha256": self._model_sha256,
            "device": "cpu",
            "quantization": "int8_avx512_vnni",
            "dimension": 384,
            "normalized": True,
            "max_length": self.max_length,
            "batch_size": self.batch_size,
            "encoded_texts": self._texts,
            "encoded_tokens": self._tokens,
            "elapsed_seconds": round(self._seconds, 4),
            "texts_per_second": round(self._texts / self._seconds, 4)
            if self._seconds
            else None,
        }


class E5GpuEncoder(EncoderBackend):
    """GPU multilingual E5 encoder with overflow-safe batched mean pooling.

    The model is loaded once at Colab startup and shared by document and query
    encoding.  Long chunks are split by the tokenizer and their segment
    vectors are averaged, so lowering ``max_length`` does not silently discard
    the tail of a source chunk.
    """

    MODEL_ID = "intfloat/multilingual-e5-small"

    def __init__(
        self,
        model_id: str = MODEL_ID,
        *,
        token: str | None = None,
        batch_size: int = 128,
        max_length: int = 384,
        stride: int = 32,
        device: str = "cuda",
    ) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("GPU E5 requires torch and transformers") from exc
        if not torch.cuda.is_available() or not str(device).startswith("cuda"):
            raise RuntimeError("GPU E5 requires a CUDA runtime")
        if batch_size < 1 or max_length < 32 or not 0 <= stride < max_length:
            raise ValueError("invalid GPU E5 batch, length or stride")

        self.model_id = str(model_id)
        self.batch_size = int(batch_size)
        self.max_length = int(max_length)
        self.stride = int(stride)
        self.device = str(device)
        self._torch = torch
        self._dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, token=token)
        self.model = AutoModel.from_pretrained(
            self.model_id,
            token=token,
            torch_dtype=self._dtype,
            low_cpu_mem_usage=True,
        ).to(self.device)
        self.model.eval()
        torch.set_float32_matmul_precision("high")
        self.dimension = int(getattr(self.model.config, "hidden_size", 384))
        self._seconds = 0.0
        self._texts = 0
        self._tokens = 0
        self._encode_lock = threading.RLock()

    def _encode(
        self,
        texts: Iterable[str],
        prefix: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        with self._encode_lock:
            return self._encode_locked(texts, prefix, progress_callback)

    def _encode_locked(
        self,
        texts: Iterable[str],
        prefix: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        torch = self._torch
        values = [prefix + str(text or "") for text in texts]
        if not values:
            return np.empty((0, self.dimension), dtype=np.float32)
        started = time.perf_counter()
        output_vectors: list[np.ndarray] = []
        total = len(values)
        for start in range(0, total, self.batch_size):
            batch = values[start : start + self.batch_size]
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                stride=self.stride,
                return_overflowing_tokens=True,
                return_tensors="pt",
            )
            owners = encoded.pop("overflow_to_sample_mapping").tolist()
            segment_vectors: list[np.ndarray] = []
            for segment_start in range(0, len(owners), self.batch_size):
                segment_end = segment_start + self.batch_size
                model_inputs = {
                    key: value[segment_start:segment_end].to(self.device)
                    for key, value in encoded.items()
                }
                with torch.inference_mode():
                    hidden = self.model(**model_inputs).last_hidden_state
                attention = model_inputs["attention_mask"].unsqueeze(-1)
                pooled = (hidden * attention).sum(dim=1) / attention.sum(dim=1).clamp(min=1)
                pooled = torch.nn.functional.normalize(pooled.float(), p=2, dim=1)
                self._tokens += int(model_inputs["attention_mask"].sum().item())
                segment_vectors.extend(pooled.cpu().numpy())
                del hidden, pooled, model_inputs
            by_owner: list[list[np.ndarray]] = [[] for _ in batch]
            for owner, vector in zip(owners, segment_vectors, strict=True):
                by_owner[int(owner)].append(vector)
            aggregated = np.vstack(
                [np.mean(vectors, axis=0, dtype=np.float32) for vectors in by_owner]
            ).astype(np.float32)
            output_vectors.append(self._normalize(aggregated))
            if progress_callback:
                progress_callback(min(start + len(batch), total), total)
        elapsed = time.perf_counter() - started
        self._seconds += elapsed
        self._texts += total
        return np.vstack(output_vectors)

    @staticmethod
    def _normalize(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def encode_documents_with_progress(
        self,
        texts: Iterable[str],
        progress_callback: Callable[[int, int], None],
    ) -> np.ndarray:
        return self._encode(texts, "passage: ", progress_callback)

    def encode_documents(self, texts: Iterable[str]) -> np.ndarray:
        return self._encode(texts, "passage: ")

    def encode_queries(self, texts: Iterable[str]) -> np.ndarray:
        return self._encode(texts, "query: ")

    def metadata(self) -> dict:
        return {
            "provider": "transformers",
            "model_id": self.model_id,
            "device": self.device,
            "dtype": str(self._dtype).replace("torch.", ""),
            "dimension": self.dimension,
            "normalized": True,
            "max_length": self.max_length,
            "stride": self.stride,
            "batch_size": self.batch_size,
            "encoded_texts": self._texts,
            "encoded_tokens": self._tokens,
            "elapsed_seconds": round(self._seconds, 4),
            "texts_per_second": round(self._texts / self._seconds, 4)
            if self._seconds
            else None,
        }


class EncoderRegistry:
    """Model-neutral Cell 5 factory. Qwen adapters can be registered later."""

    _providers = {"e5_onnx": E5OnnxEncoder, "e5_gpu": E5GpuEncoder}

    @classmethod
    def register(cls, name: str, provider: type[EncoderBackend]) -> None:
        cls._providers[str(name)] = provider

    @classmethod
    def create(cls, name: str, **kwargs) -> EncoderBackend:
        if name not in cls._providers:
            raise KeyError(f"unknown encoder backend: {name}")
        return cls._providers[name](**kwargs)
