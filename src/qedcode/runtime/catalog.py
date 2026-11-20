"""The model catalog: load and validate `catalog.yaml` (AGENTS.md section 10)."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from qedcode.config import models_dir

PLACEHOLDERS = ("<verify>", "<fill>")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(RuntimeError):
    """The catalog file is missing or invalid."""


class GgufSource(BaseModel):
    """Where a GGUF file lives on Hugging Face and how to check it."""

    repo: str
    file: str
    revision: str = ""
    size_bytes: int = 0
    size_gb: float | str = "<verify>"
    sha256: str = "<fill>"

    @field_validator("sha256")
    @classmethod
    def _sha_shape(cls, value: str) -> str:
        if value in PLACEHOLDERS or _SHA256.match(value):
            return value
        raise ValueError("sha256 must be 64 lowercase hex characters (or a placeholder)")

    @field_validator("size_gb")
    @classmethod
    def _size_shape(cls, value: float | str) -> float | str:
        if isinstance(value, str) and value not in PLACEHOLDERS:
            raise ValueError("size_gb must be a number or a placeholder")
        return value


class CatalogEntry(BaseModel):
    id: str
    display: str
    gguf: GgufSource
    ollama_tag: str = ""
    license: str = ""
    kv_bytes_per_token_f16: int
    state_mb_per_seq: float = 0.0
    rank: int

    @property
    def weights_gb(self) -> float | None:
        """Model file size in (decimal) GB, or None when it is still unverified."""
        size = self.gguf.size_gb
        return float(size) if isinstance(size, (int, float)) else None

    def unverified_fields(self) -> list[str]:
        """Names of fields that still hold a '<verify>' or '<fill>' placeholder."""
        found = []
        for name in ("repo", "file", "revision", "size_gb", "sha256"):
            value = getattr(self.gguf, name)
            if isinstance(value, str) and (value in PLACEHOLDERS or "<verify" in value):
                found.append(f"gguf.{name}")
        return found

    @property
    def verified(self) -> bool:
        return not self.unverified_fields()


class Catalog(BaseModel):
    entries: list[CatalogEntry] = Field(default_factory=list)

    def get(self, model_id: str) -> CatalogEntry | None:
        return next((e for e in self.entries if e.id == model_id), None)

    def require(self, model_id: str) -> CatalogEntry:
        entry = self.get(model_id)
        if entry is None:
            known = ", ".join(e.id for e in self.entries)
            raise CatalogError(
                f"There is no model called '{model_id}' in the catalog. "
                f"Run `qed models list` to see the choices: {known}."
            )
        return entry

    def by_rank(self) -> list[CatalogEntry]:
        return sorted(self.entries, key=lambda e: e.rank)


def parse_catalog(text: str, where: str = "catalog.yaml") -> Catalog:
    """Validate catalog YAML text; raises CatalogError with a plain-English message."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(
            f"The model catalog ({where}) is not valid YAML: {exc}. "
            "Reinstall QEDCode, or fix the file if you edited it."
        ) from exc
    if not isinstance(data, list):
        raise CatalogError(
            f"The model catalog ({where}) must be a list of models. "
            "Reinstall QEDCode, or fix the file if you edited it."
        )
    try:
        catalog = Catalog(entries=[CatalogEntry.model_validate(item) for item in data])
    except ValidationError as exc:
        raise CatalogError(
            f"The model catalog ({where}) has an invalid entry: {exc}. "
            "Reinstall QEDCode, or fix the entry if you edited the file."
        ) from exc
    ids = [e.id for e in catalog.entries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise CatalogError(
            f"The model catalog ({where}) repeats ids: {', '.join(duplicates)}. "
            "Give every model its own id."
        )
    return catalog


def load_catalog(path: Path | None = None) -> Catalog:
    """Load the packaged catalog (or the file at `path`)."""
    if path is None:
        text = resources.files("qedcode.runtime").joinpath("catalog.yaml").read_text("utf-8")
        return parse_catalog(text)
    try:
        return parse_catalog(Path(path).read_text(encoding="utf-8"), str(path))
    except OSError as exc:
        raise CatalogError(
            f"Cannot read the model catalog {path}: {exc}. "
            "Check that the file exists and can be read."
        ) from exc


def local_path(entry: CatalogEntry) -> Path:
    """Where the GGUF file for this entry lives once downloaded."""
    return models_dir() / entry.id / entry.gguf.file
