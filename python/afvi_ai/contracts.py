from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


class LabelSource(str, Enum):
    VRS_CODE = "vrs_code"
    UNKEYED_ASSUMED_GOOD = "unkeyed_assumed_good"
    NO_VRS_FILE = "no_vrs_file"
    CONFLICT = "conflict"


class LabelConfidence(str, Enum):
    HIGH = "high"
    LOW = "low"
    UNOBSERVED = "unobserved"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class LabelEvidence:
    ivs_1: str | None
    ivs_1_values: tuple[str | None, ...]
    label: int | None
    source: LabelSource
    confidence: LabelConfidence
    excluded: bool = False


@dataclass(frozen=True)
class SampleKey:
    machine: str
    result_group: str
    strip: str
    side: str
    unit_idx: int
    img_merge_idx: int

    def __post_init__(self) -> None:
        for name in ("machine", "result_group", "strip"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.side not in {"FC", "BC"}:
            raise ValueError("side must be FC or BC")
        if isinstance(self.unit_idx, bool) or not isinstance(self.unit_idx, int) or self.unit_idx < -1:
            raise ValueError("unit_idx must be an integer greater than or equal to -1")
        if isinstance(self.img_merge_idx, bool) or not isinstance(self.img_merge_idx, int) or self.img_merge_idx < 1:
            raise ValueError("img_merge_idx must be a positive integer")


@dataclass(frozen=True)
class Sample:
    key: SampleKey
    lot: str
    features: Mapping[str, Any]
    image_ref: str | None
    ivs_1: str | None
    vrs_observed: bool
    label: int | None
    label_source: LabelSource
    label_confidence: LabelConfidence
    inspected_at: datetime | None = None
    image_status: str = "not_checked"
    excluded: bool = False
    missing_feature_bits: int = 0


def label_from_ivs(ivs_1: str | None, *, vrs_observed: bool) -> LabelEvidence:
    _validate_code(ivs_1)
    values = (ivs_1,)
    if not vrs_observed:
        return LabelEvidence(
            ivs_1,
            values,
            None,
            LabelSource.NO_VRS_FILE,
            LabelConfidence.UNOBSERVED,
        )

    code = (ivs_1 or "").strip()
    if code and code != "0":
        return LabelEvidence(ivs_1, values, 1, LabelSource.VRS_CODE, LabelConfidence.HIGH)
    return LabelEvidence(
        ivs_1,
        values,
        0,
        LabelSource.UNKEYED_ASSUMED_GOOD,
        LabelConfidence.LOW,
    )


def label_from_cluster(
    ivs_1_values: Iterable[str | None], *, vrs_observed: bool
) -> LabelEvidence:
    values = tuple(ivs_1_values)
    if not values:
        raise ValueError("a merge point must contain at least one ivs_1 value")
    for value in values:
        _validate_code(value)

    representative = values[0]
    if not vrs_observed:
        return LabelEvidence(
            representative,
            values,
            None,
            LabelSource.NO_VRS_FILE,
            LabelConfidence.UNOBSERVED,
        )

    categories = {_label_category(value) for value in values}
    if len(categories) > 1:
        return LabelEvidence(
            representative,
            values,
            None,
            LabelSource.CONFLICT,
            LabelConfidence.AMBIGUOUS,
            excluded=True,
        )

    category = next(iter(categories))
    is_code = category[0] == "code"
    return LabelEvidence(
        representative,
        values,
        1 if is_code else 0,
        LabelSource.VRS_CODE if is_code else LabelSource.UNKEYED_ASSUMED_GOOD,
        LabelConfidence.HIGH if is_code else LabelConfidence.LOW,
    )


def normalize_export_record(
    row: Mapping[str, Any], *, image_index: Any | None = None, verify_image: bool = False
) -> Sample:
    """Convert one PxSpecTool JSONL merge-point record into the stable Python contract."""
    required = (
        "machine",
        "result_group",
        "strip",
        "side",
        "unit_idx",
        "img_merge_idx",
        "features",
        "vrs_observed",
    )
    missing = [field for field in required if field not in row]
    if missing:
        raise ValueError(f"missing export fields: {', '.join(missing)}")
    if not isinstance(row["vrs_observed"], bool):
        raise ValueError("vrs_observed must be a boolean")
    if not isinstance(row["features"], Mapping):
        raise ValueError("features must be an object")
    if not isinstance(row.get("lot"), str) or not row["lot"].strip():
        raise ValueError("lot must be a non-empty string supplied by the exporter")
    result_group = row["result_group"]
    if not isinstance(result_group, str):
        raise ValueError("result_group must be a relative model/lot/group path")
    group_parts = result_group.split("/")
    normalized_group = PurePosixPath(result_group)
    if (
        len(group_parts) != 3
        or "\\" in result_group
        or normalized_group.is_absolute()
        or normalized_group.as_posix() != result_group
        or any(part in {"", ".", ".."} for part in group_parts)
        or group_parts[1] != row["lot"]
    ):
        raise ValueError("lot must match the second result_group path component")
    missing_feature_bits = row.get("missing_feature_bits", 0)
    if (
        isinstance(missing_feature_bits, bool)
        or not isinstance(missing_feature_bits, int)
        or missing_feature_bits < 0
    ):
        raise ValueError("missing_feature_bits must be a non-negative integer")

    raw_values = row.get("ivs_1_values")
    if raw_values is None:
        raw_values = (row.get("ivs_1"),)
    elif not isinstance(raw_values, (list, tuple)):
        raise ValueError("ivs_1_values must be an array")
    evidence = label_from_cluster(raw_values, vrs_observed=row["vrs_observed"])
    raw_ivs_1 = row.get("ivs_1", evidence.ivs_1)
    _validate_code(raw_ivs_1)
    conflict = row.get("label_conflict", False)
    if not isinstance(conflict, bool):
        raise ValueError("label_conflict must be a boolean")
    if conflict and row["vrs_observed"]:
        evidence = LabelEvidence(
            raw_ivs_1,
            tuple(raw_values),
            None,
            LabelSource.CONFLICT,
            LabelConfidence.AMBIGUOUS,
            excluded=True,
        )

    inspected_at = _parse_timestamp(row.get("inspected_at"))
    image_ref = None
    image_status = "not_checked"
    if image_index is not None:
        from .manifest import join_manifest_rel

        image_rel = row.get("image_rel")
        if not isinstance(image_rel, str) or not image_rel:
            raise ValueError("image_rel must be a non-empty string when a manifest is used")
        manifest_rel = join_manifest_rel(row["machine"], row["result_group"], image_rel)
        image = image_index.resolve(manifest_rel, verify_exists=verify_image)
        image_ref = str(image.path) if image.path is not None else None
        image_status = image.status.value

    key = SampleKey(
        machine=row["machine"],
        result_group=row["result_group"],
        strip=row["strip"],
        side=row["side"],
        unit_idx=row["unit_idx"],
        img_merge_idx=row["img_merge_idx"],
    )
    return Sample(
        key=key,
        lot=row["lot"],
        features=dict(row["features"]),
        image_ref=image_ref,
        ivs_1=raw_ivs_1,
        vrs_observed=row["vrs_observed"],
        label=evidence.label,
        label_source=evidence.source,
        label_confidence=evidence.confidence,
        inspected_at=inspected_at,
        image_status=image_status,
        excluded=evidence.excluded,
        missing_feature_bits=missing_feature_bits,
    )


def validate_unique_keys(samples: Iterable[Sample]) -> None:
    seen: set[SampleKey] = set()
    for sample in samples:
        if sample.key in seen:
            raise ValueError(f"duplicate sample key: {sample.key}")
        seen.add(sample.key)


def _label_category(value: str | None) -> tuple[str, str]:
    code = (value or "").strip()
    if not code or code == "0":
        return ("unkeyed", "")
    return ("code", code)


def _validate_code(value: str | None) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError("ivs_1 values must be strings or None")


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("inspected_at must be an ISO datetime string or None")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"invalid inspected_at timestamp: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("inspected_at timestamp must include a timezone offset")
    return parsed
