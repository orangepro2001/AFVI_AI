from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping


class ImageStatus(str, Enum):
    MAPPED = "mapped"
    AVAILABLE = "available"
    MISSING_MANIFEST_ENTRY = "missing_manifest_entry"
    MISSING_FILE = "missing_file"
    MANIFEST_CONFLICT = "manifest_conflict"


@dataclass(frozen=True)
class ManifestRecord:
    rel: str
    src_bytes: int
    out_bytes: int
    quality: int | float | None
    created_at: str | None


@dataclass(frozen=True)
class ImageRef:
    rel: str
    path: Path | None
    status: ImageStatus
    src_bytes: int | None = None
    out_bytes: int | None = None


class ManifestIndex:
    def __init__(self, jpg_root: Path, label: str) -> None:
        if not label or label in {".", ".."} or "/" in label or "\\" in label:
            raise ValueError("label must be a single path segment")
        self.jpg_root = Path(jpg_root)
        self.label = label
        self._records: dict[str, ManifestRecord] = {}
        self._conflicts: set[str] = set()
        self.duplicate_count = 0

    @classmethod
    def load(cls, manifest_path: Path, jpg_root: Path, label: str) -> ManifestIndex:
        index = cls(jpg_root, label)
        with Path(manifest_path).open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    record = _parse_record(raw)
                except (json.JSONDecodeError, ValueError, TypeError) as error:
                    raise ValueError(f"invalid manifest row at line {line_number}: {error}") from error
                previous = index._records.get(record.rel)
                if previous is None:
                    index._records[record.rel] = record
                    continue
                index.duplicate_count += 1
                if (previous.src_bytes, previous.out_bytes) != (record.src_bytes, record.out_bytes):
                    index._conflicts.add(record.rel)
        return index

    def resolve(self, rel: str, *, verify_exists: bool = False) -> ImageRef:
        normalized = _safe_rel(rel)
        record = self._records.get(normalized)
        if record is None:
            return ImageRef(normalized, None, ImageStatus.MISSING_MANIFEST_ENTRY)
        if normalized in self._conflicts:
            return ImageRef(
                normalized,
                None,
                ImageStatus.MANIFEST_CONFLICT,
                record.src_bytes,
                record.out_bytes,
            )

        source_path = PurePosixPath(normalized)
        jpg_rel = source_path.with_suffix(".jpg")
        path = self.jpg_root / self.label / Path(*jpg_rel.parts)
        if verify_exists:
            if not path.is_file():
                return ImageRef(normalized, path, ImageStatus.MISSING_FILE, record.src_bytes, record.out_bytes)
            status = ImageStatus.AVAILABLE
        else:
            status = ImageStatus.MAPPED
        return ImageRef(normalized, path, status, record.src_bytes, record.out_bytes)


def join_manifest_rel(machine: str, result_group: str, image_rel: str) -> str:
    """Build the scanner key, which is relative to a machine's ResultData root."""
    if not isinstance(machine, str) or not machine or "/" in machine or "\\" in machine:
        raise ValueError("machine must be a single path segment")
    group = _safe_rel(result_group, require_bmp=False)
    image = _safe_rel(image_rel)
    return _safe_rel(f"{group}/{image}")


def _parse_record(raw: Any) -> ManifestRecord:
    if not isinstance(raw, Mapping):
        raise ValueError("row must be an object")
    rel = _safe_rel(raw.get("rel"))
    src = raw.get("src")
    out = raw.get("out")
    if isinstance(src, bool) or not isinstance(src, int) or src < 0:
        raise ValueError("src must be a non-negative integer byte count")
    if isinstance(out, bool) or not isinstance(out, int) or out < 0:
        raise ValueError("out must be a non-negative integer byte count")
    quality = raw.get("q")
    if quality is not None and (isinstance(quality, bool) or not isinstance(quality, (int, float))):
        raise ValueError("q must be numeric or null")
    created_at = raw.get("at")
    if created_at is not None and not isinstance(created_at, str):
        raise ValueError("at must be a string or null")
    return ManifestRecord(rel, src, out, quality, created_at)


def _safe_rel(value: Any, *, require_bmp: bool = True) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("rel must be a non-empty path")
    normalized = value.replace("\\", "/")
    windows = PureWindowsPath(normalized)
    posix = PurePosixPath(normalized)
    parts = normalized.split("/")
    if posix.is_absolute() or windows.drive or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("rel must be a normalized relative path")
    if require_bmp and posix.suffix.lower() != ".bmp":
        raise ValueError("manifest rel must identify a BMP source path")
    return posix.as_posix()
