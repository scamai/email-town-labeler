"""sentence-transformers embedder + numpy softmax head (needs the `[embed]` extra).

model.json `config`:
    embedder: {repo, revision, st_kwargs?}   HF repo of the SentenceTransformer, pinned revision
        st_kwargs is package-supplied, so only an allowlist passes (see `safe_st_kwargs`):
        config_kwargs, tokenizer_kwargs, and model_kwargs limited to torch_dtype/dtype.
        trust_remote_code is always False: a model package never runs code.
    prompt: str, max_seq_length: int, normalize: bool, batch_size: int, dim: int
    head: {coef: "coef.npy", intercept: "intercept.npy"}   rows follow manifest.classes
Runs on CPU (float32) unless device="cuda". Nothing here logs texts.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..base import ModelManifest
from ._common import Embedder, HeadClassifier, SoftmaxHead, require

BACKEND = "embedding_probe"
ST_KWARGS = ("config_kwargs", "tokenizer_kwargs", "model_kwargs")
MODEL_KWARGS = ("torch_dtype", "dtype")
DTYPES = ("auto", "float32", "float16", "bfloat16")
# never from a package: these would unpin the revision, load code, or read other repos/credentials
REFUSED_NESTED = (
    "revision",
    "code_revision",
    "token",
    "use_auth_token",
    "cache_dir",
    "local_files_only",
    "auto_map",
    "_commit_hash",
)


def safe_st_kwargs(raw: object) -> dict:
    """Package-supplied `st_kwargs` -> kwargs safe to pass to SentenceTransformer, or ValueError.

    Anything that could make loading run code (trust_remote_code, custom modules, other repos) is refused;
    trust_remote_code=False is forced at every level.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise TypeError("embedding_probe config.embedder.st_kwargs must be an object")
    bad = sorted(set(raw) - set(ST_KWARGS))
    if bad:
        raise ValueError(
            f"embedding_probe config.embedder.st_kwargs: refusing {bad}; allowed keys: {list(ST_KWARGS)}"
        )
    out: dict = {}
    for k in ST_KWARGS:
        v = raw.get(k)
        if v is None:
            continue
        if not isinstance(v, dict):
            raise TypeError(f"embedding_probe config.embedder.st_kwargs.{k} must be an object")
        v = dict(v)
        if k == "model_kwargs":
            extra = sorted(set(v) - set(MODEL_KWARGS))
            if extra:
                raise ValueError(
                    f"embedding_probe st_kwargs.model_kwargs: refusing {extra}; allowed: {list(MODEL_KWARGS)}"
                )
            for dk, dv in v.items():
                if dv not in DTYPES:
                    raise ValueError(
                        f"embedding_probe st_kwargs.model_kwargs.{dk} must be one of {list(DTYPES)}"
                    )
        if v.get("trust_remote_code"):
            raise ValueError(f"embedding_probe st_kwargs.{k}: trust_remote_code is not allowed")
        refused = sorted(set(v) & set(REFUSED_NESTED))
        if refused:
            raise ValueError(f"embedding_probe st_kwargs.{k}: refusing {refused}")
        v["trust_remote_code"] = False
        out[k] = v
    out["trust_remote_code"] = False
    return out


class SentenceTransformerEmbedder:
    def __init__(self, cfg: dict, device: str = "cpu"):
        torch = require("torch", BACKEND)
        st = require("sentence_transformers", BACKEND)
        e = cfg.get("embedder") or {}
        if not e.get("repo"):
            raise ValueError("embedding_probe config.embedder.repo is required")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("device 'cuda' requested but no CUDA device is available")
        # CPU: float32 (bf16 is slow or unsupported on many laptop CPUs).
        dtype = (
            torch.bfloat16 if device.startswith("cuda") and torch.cuda.is_bf16_supported() else torch.float32
        )
        kw = safe_st_kwargs(e.get("st_kwargs"))
        mk = kw.pop("model_kwargs", {})
        mk.pop("trust_remote_code", None)
        model_kwargs = {"torch_dtype": dtype, **mk, "trust_remote_code": False}
        if "dtype" in mk:  # one dtype key only: the package's spelling wins
            model_kwargs.pop("torch_dtype")
        if e.get("revision"):
            kw["revision"] = str(e["revision"])
        self.model = st.SentenceTransformer(str(e["repo"]), device=device, model_kwargs=model_kwargs, **kw)
        msl = int(cfg.get("max_seq_length") or self.model.max_seq_length or 512)
        self.model.max_seq_length = min(msl, self.model.max_seq_length or msl)
        self.prompt = cfg.get("prompt") or None
        self.normalize = bool(cfg.get("normalize", True))
        self.batch_size = int(cfg.get("batch_size", 8))

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        e = self.model.encode(
            list(texts),
            prompt=self.prompt,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(e, dtype=np.float32)


class EmbeddingProbe(HeadClassifier):
    def __init__(self, manifest: ModelManifest, *, device: str = "cpu", embedder: Embedder | None = None):
        self.manifest = manifest
        self.head = SoftmaxHead.load(manifest)
        self.embedder = (
            embedder if embedder is not None else SentenceTransformerEmbedder(manifest.config, device)
        )

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return self.embedder.embed(texts)


def load(manifest: ModelManifest, *, device: str = "cpu", embedder: Embedder | None = None) -> EmbeddingProbe:
    return EmbeddingProbe(manifest, device=device, embedder=embedder)
