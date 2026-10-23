"""Resolve a model ref to a loaded Classifier.

ref forms: `rules` (built-in), a catalog name such as `town-modernbert` (catalog.py: a pinned, published
package, downloaded on first use), a local directory with model.json, `hf:<org>/<repo>[@rev]`,
an https/file URL of a .tar.gz/.zip package, or a name installed under `models_dir/<name>/`.

Backends are plug-ins: a module exposing `load(manifest, *, device, **kw) -> Classifier`.
Built-ins are imported lazily; third parties register via the `email_town_labeler.backends` entry-point
group (`name = "pkg.module:load"`) or `register_backend()`.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

from . import catalog, hub
from .backends._common import SOURCE, MissingExtraError
from .base import MANIFEST, Classifier, ModelManifest

Loader = Callable[..., Classifier]
BUILTIN = {
    n: f"email_town_labeler.models.backends.{n}"
    for n in (
        "rules",
        "embedding_probe",
        "onnx_classifier",
        "hf_sequence_classifier",
        "onnx_sequence_classifier",
        "ollama_choice",
    )
}
_custom: dict[str, Loader] = {}

__all__ = [
    "MissingExtraError",
    "UnknownModelError",
    "backend_names",
    "list_models",
    "load_model",
    "register_backend",
    "resolve",
]


class UnknownModelError(LookupError):
    pass


def register_backend(name: str, loader: Loader) -> None:
    _custom[name] = loader


def _entry_points() -> dict[str, metadata.EntryPoint]:
    try:
        return {ep.name: ep for ep in metadata.entry_points(group="email_town_labeler.backends")}
    except Exception:  # noqa: BLE001 - broken plug-in metadata must not break the builtins
        return {}


def backend_names() -> list[str]:
    return sorted(set(BUILTIN) | set(_custom) | set(_entry_points()))


def get_backend(name: str) -> Loader:
    if name in _custom:
        return _custom[name]
    if name in BUILTIN:
        return importlib.import_module(BUILTIN[name]).load
    eps = _entry_points()
    if name in eps:
        return eps[name].load()
    raise UnknownModelError(
        f"unknown model backend {name!r}; known: {', '.join(backend_names())}. "
        "If a plug-in provides it, install that package (ML backends: "
        f'pip install "email-town-labeler[embed] @ {SOURCE}" or "email-town-labeler[onnx] @ {SOURCE}").'
    )


def _is_url(ref: str) -> bool:
    return ref.startswith(("https://", "http://", "file://"))


def resolve(ref: str, *, models_dir: Path, download: bool = True) -> ModelManifest:
    """ref -> manifest (with .path set, except for the builtin `rules`). May download hf:/URL refs."""
    ref = ref.strip()
    if ref == "rules":
        from .backends.rules import MANIFEST as RULES

        return RULES
    entry = catalog.get(ref)
    if entry is not None:
        found = catalog.installed(entry, models_dir)
        if found is None:
            if not download:
                raise UnknownModelError(
                    f"model {ref!r} is not installed; run `email-town-labeler model pull {ref}` "
                    f"(downloads {catalog.human_size(entry.size)} once)"
                )
            found = catalog.fetch(entry, models_dir)
        return ModelManifest.load(found)
    if ref.startswith("hf:") or _is_url(ref):
        found = hub.find_installed(ref, models_dir)
        if found is None:
            if not download:
                raise UnknownModelError(
                    f"model {ref!r} is not installed; run `email-town-labeler model pull {ref}`"
                )
            found = hub.fetch(ref, models_dir)
        return ModelManifest.load(found)
    p = Path(ref).expanduser()
    if (p / MANIFEST).is_file():
        return ModelManifest.load(p)
    if hub.NAME_RE.match(ref) and (Path(models_dir) / ref / MANIFEST).is_file():
        return ModelManifest.load(Path(models_dir) / ref)
    raise UnknownModelError(
        f"no model {ref!r}: not `rules`, not a directory with {MANIFEST}, "
        f"not hf:<org>/<repo>[@rev], and not installed under {models_dir}"
    )


def _accepts(fn: Callable[..., object], name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def load_model(
    ref: str, *, models_dir: Path, device: str = "cpu", allow_remote_endpoint: bool = False, **backend_kwargs
) -> Classifier:
    """Load a Classifier. `backend_kwargs` go to the backend (e.g. `embedder=` in tests).

    `allow_remote_endpoint` is the user's opt-in (Config.remote_endpoint_allowed()) for backends that talk to
    a server, such as ollama_choice: without it they refuse any host that is not loopback. It is passed to
    every backend whose loader accepts it; a model package can never set it.
    """
    entry = catalog.get(ref)
    if (
        entry is not None
        and catalog.installed(entry, models_dir) is None
        and not catalog.extra_installed(entry.extra)
    ):
        # fail before an ~800 MB download that could not run anyway
        raise MissingExtraError(catalog.EXTRA_MODULES[entry.extra][0], entry.extra, ref)
    man = resolve(ref, models_dir=models_dir)
    loader = get_backend(man.backend)
    if _accepts(loader, "allow_remote_endpoint"):
        backend_kwargs["allow_remote_endpoint"] = bool(allow_remote_endpoint)
    return loader(man, device=device, **backend_kwargs)


def list_models(models_dir: Path) -> list[ModelManifest]:
    """The builtin `rules` plus every valid package under models_dir (sorted by name)."""
    from .backends.rules import MANIFEST as RULES

    out = [RULES]
    d = Path(models_dir)
    if d.is_dir():
        for sub in sorted(d.iterdir()):
            if sub.name.startswith(".") or not (sub / MANIFEST).is_file():
                continue
            try:
                out.append(ModelManifest.load(sub))
            except (ValueError, TypeError, OSError):
                continue
    return out
