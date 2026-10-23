"""Config (TOML) and on-disk paths. `EMAIL_TOWN_LABELER_HOME` puts everything under one directory (Docker: /data)."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from .taxonomy import DEFAULT_LABEL_COLORS, DISPLAY_NAMES, LABEL_CLASSES

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

APP = "email-town-labeler"
HOME_ENV = "EMAIL_TOWN_LABELER_HOME"
REMOTE_ENDPOINT_ENV = (
    "EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT"  # "1": same as [model] allow_remote_endpoint = true
)
UNKNOWN_ACTIONS = ("skip", "label")
# [policy] keys a model package may recommend (model.json config.recommended_policy); config.toml wins
MODEL_POLICY_KEYS = ("action_threshold", "promotional_threshold")
log = logging.getLogger("email_town_labeler.config")
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


class ConfigError(ValueError):
    """A config problem worth showing to the user verbatim."""


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    data_dir: Path

    @classmethod
    def default(cls, home: str | Path | None = None) -> Paths:
        home = home or os.environ.get(HOME_ENV)
        if home:
            h = Path(home).expanduser()
            return cls(h / "config", h / "data")
        from platformdirs import user_config_dir, user_data_dir

        return cls(Path(user_config_dir(APP, appauthor=False)), Path(user_data_dir(APP, appauthor=False)))

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def client_secret(self) -> Path:
        return self.config_dir / "client_secret.json"

    @property
    def token(self) -> Path:
        return self.config_dir / "token.json"

    @property
    def pending_auth(self) -> Path:
        return self.config_dir / "pending_auth.json"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state.sqlite"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure(self) -> Paths:
        for d in (self.config_dir, self.data_dir, self.models_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


# (section, key) -> Config field name. Order defines the written TOML layout.
_LAYOUT: list[tuple[str, str, str]] = [
    ("gmail", "label_prefix", "label_prefix"),
    ("gmail", "poll_seconds", "poll_seconds"),
    ("gmail", "skip_label_names", "skip_label_names"),
    ("gmail", "label_colors", "label_colors"),
    ("model", "ref", "model_ref"),
    ("model", "min_confidence", "min_confidence"),
    ("model", "unknown_action", "unknown_action"),
    ("model", "device", "device"),
    ("model", "allow_remote_endpoint", "allow_remote_endpoint"),
    ("engine", "dry_run", "dry_run"),
    ("engine", "recovery_max_days", "recovery_max_days"),
    ("policy", "action_threshold", "action_threshold"),
    ("policy", "promotional_threshold", "promotional_threshold"),
    ("policy", "protect_known_contacts", "protect_known_contacts"),
    ("policy", "new_sender", "new_sender"),
    ("policy", "contacts_scan_days", "contacts_scan_days"),
    ("policy", "contacts_scan_limit", "contacts_scan_limit"),
]


@dataclass
class Config:
    label_prefix: str = "email-town-labeler"
    poll_seconds: int = 60
    skip_label_names: list[str] = field(default_factory=list)
    label_colors: bool = True  # color our class labels (DEFAULT_LABEL_COLORS + [label_colors] overrides)
    model_ref: str = "rules"
    min_confidence: float = 0.55
    unknown_action: str = "skip"
    device: str = "cpu"
    allow_remote_endpoint: bool = False  # let a model backend send mail text to a non-loopback host
    dry_run: bool = False
    recovery_max_days: int = 30  # history expired: rescan at most this far back (never before setup)
    # [policy]: how model probabilities become a class (policy.py), then the new-sender rule (engine.py)
    action_threshold: float = 0.2  # p[action_needed] >= this -> action_needed (its confidence bar too)
    promotional_threshold: float = 0.6  # promotional only when p[promotional] >= this
    protect_known_contacts: bool = True  # never promotional for an address you have written to
    new_sender: bool = True  # label unknown Primary mail from never-written-to addresses "new sender"
    contacts_scan_days: int = 730  # setup scans sent mail this far back for contacts...
    contacts_scan_limit: int = 5000  # ...but at most this many sent messages
    label_names: dict[str, str] = field(default_factory=dict)  # optional [labels] overrides
    # optional [label_colors] overrides: class -> {"background", "text"} or False (leave that label alone)
    label_color_overrides: dict[str, Any] = field(default_factory=dict)
    # field names the user set in config.toml (filled by config_from_dict), and names for_model() filled in
    _explicit: set[str] = field(default_factory=set, repr=False, compare=False)
    _from_model: set[str] = field(default_factory=set, repr=False, compare=False)

    def is_explicit(self, name: str) -> bool:
        """Set in config.toml, or changed in code from the built-in default (but not by for_model)."""
        if name in self._explicit:
            return True
        default = next(f.default for f in fields(self) if f.name == name)
        return name not in self._from_model and getattr(self, name) != default

    def policy_sources(self, manifest: Any = None) -> dict[str, tuple[float, str]]:
        """MODEL_POLICY_KEYS -> (effective value, source): "config", "model" (recommended_policy) or "default"."""
        rec = _recommended_policy(manifest)
        out: dict[str, tuple[float, str]] = {}
        for k in MODEL_POLICY_KEYS:
            if self.is_explicit(k):
                out[k] = (getattr(self, k), "config")
            elif k in rec:
                out[k] = (rec[k], "model")
            else:
                out[k] = (getattr(self, k), "model" if k in self._from_model else "default")
        return out

    def for_model(self, manifest: Any) -> Config:
        """This config with the model's recommended thresholds for keys the user did not set (else self)."""
        changes = {k: v for k, (v, src) in self.policy_sources(manifest).items() if src == "model"}
        if not changes or all(getattr(self, k) == v for k, v in changes.items()):
            return self
        return replace(self, **changes, _from_model=self._from_model | set(changes))

    def display_names(self) -> dict[str, str]:
        return {**DISPLAY_NAMES, **self.label_names}

    def colors(self) -> dict[str, dict[str, str]]:
        """class -> Gmail color {"textColor", "backgroundColor"} to apply; empty when colors are off."""
        if not self.label_colors:
            return {}
        out = {c: dict(v) for c, v in DEFAULT_LABEL_COLORS.items()}
        for c, v in self.label_color_overrides.items():
            if v is False:
                out.pop(c, None)
            else:
                out[c] = {"textColor": v["text"].lower(), "backgroundColor": v["background"].lower()}
        return out

    def uses_contacts(self) -> bool:
        """Whether anything needs the contacts table (and so the sent-mail scan)."""
        return self.protect_known_contacts or self.new_sender

    def remote_endpoint_allowed(self) -> bool:
        """The user's opt-in (config or env var) to model endpoints off this machine. Never from a package."""
        return self.allow_remote_endpoint or os.environ.get(REMOTE_ENDPOINT_ENV, "").strip() == "1"

    def validate(self) -> Config:
        errs: list[str] = []
        p = self.label_prefix.strip().strip("/")
        if not p:
            errs.append("gmail.label_prefix must not be empty")
        elif p.upper() in {"INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "UNREAD"}:
            errs.append(f"gmail.label_prefix '{p}' clashes with a Gmail system label")
        self.label_prefix = p
        if not 10 <= self.poll_seconds <= 86400:
            errs.append(f"gmail.poll_seconds must be between 10 and 86400 (got {self.poll_seconds})")
        if not 1 <= self.recovery_max_days <= 365:
            errs.append(f"engine.recovery_max_days must be between 1 and 365 (got {self.recovery_max_days})")
        if not 0.0 <= self.min_confidence <= 1.0:
            errs.append(f"model.min_confidence must be between 0 and 1 (got {self.min_confidence})")
        if self.unknown_action not in UNKNOWN_ACTIONS:
            errs.append(
                f"model.unknown_action must be one of {list(UNKNOWN_ACTIONS)} (got '{self.unknown_action}')"
            )
        if not 0.0 < self.action_threshold <= 1.0:
            errs.append(
                f"policy.action_threshold must be above 0 and at most 1 (got {self.action_threshold})"
            )
        if not 0.0 <= self.promotional_threshold <= 1.0:
            errs.append(
                f"policy.promotional_threshold must be between 0 and 1 (got {self.promotional_threshold})"
            )
        if not 1 <= self.contacts_scan_days <= 3650:
            errs.append(
                f"policy.contacts_scan_days must be between 1 and 3650 (got {self.contacts_scan_days})"
            )
        if not 0 <= self.contacts_scan_limit <= 100_000:
            errs.append(
                f"policy.contacts_scan_limit must be between 0 and 100000 (got {self.contacts_scan_limit})"
            )
        if not self.model_ref.strip():
            errs.append('model.ref must not be empty (try "rules")')
        bad = sorted(set(self.label_names) - set(LABEL_CLASSES))
        if bad:
            errs.append(f"[labels] has unknown classes {bad}; valid: {LABEL_CLASSES}")
        for c, n in self.label_names.items():
            if not str(n).strip() or "/" in str(n):
                errs.append(f"[labels] {c} must be a non-empty name without '/'")
        bad = sorted(set(self.label_color_overrides) - set(LABEL_CLASSES))
        if bad:
            errs.append(f"[label_colors] has unknown classes {bad}; valid: {LABEL_CLASSES}")
        for c, v in sorted(self.label_color_overrides.items()):
            if v is False:
                continue
            ok = (
                isinstance(v, dict)
                and set(v) == {"background", "text"}
                and all(isinstance(x, str) and HEX_COLOR.fullmatch(x) for x in v.values())
            )
            if not ok:
                errs.append(
                    f'[label_colors] {c} must be false or {{ background = "#rrggbb", text = "#rrggbb" }} '
                    "(colors from Gmail's label palette)"
                )
        if errs:
            raise ConfigError("invalid config:\n  - " + "\n  - ".join(errs))
        return self


def _recommended_policy(manifest: Any) -> dict[str, float]:
    """Valid entries of manifest.config.recommended_policy (invalid ones are ignored, with a warning)."""
    rec = (getattr(manifest, "config", None) or {}).get("recommended_policy") or {}
    if not isinstance(rec, dict):
        return {}
    out: dict[str, float] = {}
    for k in MODEL_POLICY_KEYS:
        v = rec.get(k)
        if v is None:
            continue
        num = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        if num and (0.0 < v <= 1.0 if k == "action_threshold" else 0.0 <= v <= 1.0):
            out[k] = float(v)
        else:
            log.warning("ignoring the model's recommended_policy.%s: not a valid threshold", k)
    return out


_EXPECT = {
    "label_prefix": str,
    "poll_seconds": int,
    "skip_label_names": list,
    "label_colors": bool,
    "model_ref": str,
    "min_confidence": float,
    "unknown_action": str,
    "device": str,
    "dry_run": bool,
    "allow_remote_endpoint": bool,
    "recovery_max_days": int,
    "action_threshold": float,
    "promotional_threshold": float,
    "protect_known_contacts": bool,
    "new_sender": bool,
    "contacts_scan_days": int,
    "contacts_scan_limit": int,
}


def _coerce(section: str, key: str, name: str, value: Any) -> Any:
    want = _EXPECT[name]
    where = f"{section}.{key}"
    if want is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{where} must be true or false (got {value!r})")
    elif want is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where} must be a whole number (got {value!r})")
    elif want is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where} must be a number (got {value!r})")
        value = float(value)
    elif want is str:
        if not isinstance(value, str):
            raise ConfigError(f"{where} must be a string (got {value!r})")
    elif want is list:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{where} must be a list of strings (got {value!r})")
        value = list(value)
    return value


def config_from_dict(data: dict[str, Any]) -> Config:
    cfg = Config()
    known = {s for s, _, _ in _LAYOUT} | {"labels", "label_colors"}
    unknown_sections = sorted(set(data) - known)
    if unknown_sections:
        raise ConfigError(f"unknown config section(s) {unknown_sections}; expected {sorted(known)}")
    for section in {s for s, _, _ in _LAYOUT}:
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise ConfigError(f"[{section}] must be a table")
        keys = {k: n for s, k, n in _LAYOUT if s == section}
        extra = sorted(set(table) - set(keys))
        if extra:
            raise ConfigError(f"unknown key(s) in [{section}]: {extra}; expected {sorted(keys)}")
        for k, v in table.items():
            setattr(cfg, keys[k], _coerce(section, k, keys[k], v))
            cfg._explicit.add(keys[k])
    labels = data.get("labels", {})
    if not isinstance(labels, dict) or not all(isinstance(v, str) for v in labels.values()):
        raise ConfigError("[labels] must map class names to label names (strings)")
    cfg.label_names = dict(labels)
    colors = data.get("label_colors", {})
    if not isinstance(colors, dict) or not all(v is False or isinstance(v, dict) for v in colors.values()):
        raise ConfigError(
            '[label_colors] must map class names to false or { background = "...", text = "..." }'
        )
    cfg.label_color_overrides = {c: v if v is False else dict(v) for c, v in colors.items()}
    return cfg.validate()


def load_config(path: str | Path | None = None) -> Config:
    """Load config.toml; a missing file means defaults."""
    p = Path(path) if path else Paths.default().config_file
    if not p.exists():
        return Config().validate()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p}: not valid TOML ({e})") from e
    try:
        return config_from_dict(data)
    except ConfigError as e:
        raise ConfigError(f"{p}: {e}") from e


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def dump_config(cfg: Config) -> str:
    out: list[str] = ["# email-town-labeler config. See docs/configuration.md"]
    section = None
    for s, k, n in _LAYOUT:
        if s != section:
            out += ["", f"[{s}]"]
            section = s
        line = f"{k} = {_toml_value(getattr(cfg, n))}"
        if n in MODEL_POLICY_KEYS and not cfg.is_explicit(n):  # unset: the model's recommended value applies
            line = f"# {line}  (unset: the model's recommended value, else this default)"
        out.append(line)
    if cfg.label_names:
        out += ["", "[labels]"] + [
            f"{json.dumps(c)} = {_toml_value(n)}" for c, n in sorted(cfg.label_names.items())
        ]
    if cfg.label_color_overrides:
        out += ["", "[label_colors]"]
        for c, v in sorted(cfg.label_color_overrides.items()):
            val = (
                "false"
                if v is False
                else f"{{ background = {_toml_value(v['background'])}, text = {_toml_value(v['text'])} }}"
            )
            out.append(f"{json.dumps(c)} = {val}")
    return "\n".join(out) + "\n"


def save_config(cfg: Config, path: str | Path | None = None) -> Path:
    cfg.validate()
    p = Path(path) if path else Paths.default().config_file
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".toml.tmp")
    tmp.write_text(dump_config(cfg), encoding="utf-8")
    os.replace(tmp, p)
    return p
