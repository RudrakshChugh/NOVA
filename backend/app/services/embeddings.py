"""Sentence embeddings via ONNX Runtime.

The model is all-MiniLM-L6-v2 — the same weights sentence-transformers serves — run through ONNX
Runtime with the HuggingFace tokenizer instead of PyTorch. The pipeline reproduces what
sentence-transformers does for this model: mean-pool the token embeddings under the attention
mask, then L2-normalise, so vectors are interchangeable with the PyTorch backend's and the
`chunks.embedding VECTOR(384)` column is unchanged.

Dropping PyTorch removes ~2.5 GB of dependency, starts in under a second, and keeps the stack to
signed binaries, which Windows Application Control requires on the development machine.
"""

from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import settings

MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "models" / "all-MiniLM-L6-v2-onnx"
MODEL_PATH = MODEL_DIR / "onnx" / "model.onnx"
TOKENIZER_PATH = MODEL_DIR / "tokenizer.json"
EMBEDDING_DIM = 384
MAX_TOKENS = 256  # all-MiniLM-L6-v2's max_seq_length


@lru_cache(maxsize=1)
def _runtime():
    import onnxruntime
    from tokenizers import Tokenizer

    if not MODEL_PATH.exists() or not TOKENIZER_PATH.exists():
        raise RuntimeError(
            f"embedding model missing under {MODEL_DIR}. Run scripts/fetch_embedding_model.py."
        )
    tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    session = onnxruntime.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
    inputs = {i.name for i in session.get_inputs()}
    return tokenizer, session, inputs


def _encode_batch(texts: list[str]) -> np.ndarray:
    tokenizer, session, accepted = _runtime()
    encodings = tokenizer.encode_batch(texts)
    ids = np.array([e.ids for e in encodings], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    if "token_type_ids" in accepted:
        feed["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)

    hidden = session.run(None, {k: v for k, v in feed.items() if k in accepted})[0]
    expanded = mask[..., None].astype(np.float32)
    pooled = (hidden * expanded).sum(axis=1) / np.clip(expanded.sum(axis=1), 1e-9, None)
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return pooled / np.clip(norms, 1e-12, None)


def embed(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """L2-normalised 384-dimensional embeddings, so a dot product is the cosine similarity."""
    if not texts:
        return []
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        out.extend(_encode_batch(texts[start : start + batch_size]).tolist())
    return out


def model_name() -> str:
    return f"{settings.embedding_model} (onnxruntime)"
