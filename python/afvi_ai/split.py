from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .contracts import Sample, SampleKey, validate_unique_keys


@dataclass(frozen=True)
class SplitResult:
    train: tuple[Sample, ...]
    test: tuple[Sample, ...]
    excluded: tuple[Sample, ...]
    train_lots: frozenset[str]
    test_lots: frozenset[str]


def split_by_lot_time(
    samples: Iterable[Sample],
    *,
    cutoff: datetime,
    test_fraction: float = 0.2,
    seed: int = 0,
    test_lots: Iterable[str] | None = None,
) -> SplitResult:
    """Hold each LOT wholly in one split; LOTs at/after cutoff go wholly to test.

    With ``test_lots`` the held-out set is exactly those LOTs and every other LOT trains (time and
    ``test_fraction`` are not used): the automatic retraining holds out the LOTs that arrived since
    the last run, which are new to the models but not necessarily later in time (a share added later).
    """
    if not 0 <= test_fraction < 1:
        raise ValueError("test_fraction must be in [0, 1)")
    cutoff_utc = _utc(cutoff)
    rows = tuple(samples)
    validate_unique_keys(rows)
    grouped: dict[str, list[Sample]] = defaultdict(list)
    for row in rows:
        grouped[row.lot].append(row)
    if test_lots is not None:
        held = frozenset(test_lots)
        test_set = frozenset(lot for lot in grouped if lot in held)
        train_set = frozenset(lot for lot in grouped if lot not in held)
        train = _ordered(row for row in rows if row.lot in train_set)
        test = _ordered(row for row in rows if row.lot in test_set)
        return SplitResult(train, test, (), train_set, test_set)

    test_lots: set[str] = set()
    train_lots: set[str] = set()
    excluded_lots: set[str] = set()
    old_lots: list[str] = []
    for lot, lot_rows in grouped.items():
        known_times = [
            _utc(row.inspected_at)
            for row in lot_rows
            if row.inspected_at is not None and _has_timezone(row.inspected_at)
        ]
        if any(timestamp >= cutoff_utc for timestamp in known_times):
            test_lots.add(lot)
        elif len(known_times) != len(lot_rows):
            excluded_lots.add(lot)
        else:
            old_lots.append(lot)

    old_lots.sort(key=lambda lot: hashlib.sha256(f"{seed}\0{lot}".encode("utf-8")).digest())
    held_out_count = math.floor(len(old_lots) * test_fraction)
    if test_fraction > 0 and len(old_lots) > 1:
        held_out_count = max(1, min(held_out_count, len(old_lots) - 1))
    test_lots.update(old_lots[:held_out_count])
    train_lots.update(old_lots[held_out_count:])

    train = _ordered(row for row in rows if row.lot in train_lots)
    test = _ordered(row for row in rows if row.lot in test_lots)
    excluded = _ordered(row for row in rows if row.lot in excluded_lots)
    return SplitResult(train, test, excluded, frozenset(train_lots), frozenset(test_lots))


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("cutoff and inspected_at values must be datetimes")
    if not _has_timezone(value):
        raise ValueError("cutoff must include a timezone offset")
    return value.astimezone(timezone.utc)


def _has_timezone(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _ordered(rows: Iterable[Sample]) -> tuple[Sample, ...]:
    return tuple(sorted(rows, key=lambda row: _key_order(row.key)))


def _key_order(key: SampleKey) -> tuple[str, str, str, str, int, int]:
    return (key.machine, key.result_group, key.strip, key.side, key.unit_idx, key.img_merge_idx)
