from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

from .contracts import LabelConfidence, Sample
from .split import split_by_lot_time

FEATURES = ("area_total", "length_max", "width_max")
DEFAULT_NEGATIVE_WEIGHT = 0.25
SENSITIVITY_NEGATIVE_WEIGHTS = (0.1, 0.25, 0.5)
L2_STRENGTH = 2.0
MIN_TRAIN_LOTS = 5
MIN_TEST_LOTS = 5
MIN_CLASS_ROWS = 20
MIN_CLASS_LOTS = 3
BOOTSTRAP_REPLICATES = 100
MAX_ITERATIONS = 350
MAX_REPORTED_SAMPLES = 2000
REFERENCE_NAME = "log1p_area_total"


@dataclass(frozen=True)
class LogisticModel:
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    negative_weight: float

    def score(self, sample: Sample) -> float | None:
        raw = _raw_vector(sample)
        if raw is None:
            return None
        vector = tuple((value - mean) / scale for value, mean, scale in zip(raw, self.means, self.scales))
        return _sigmoid(self.intercept + sum(weight * value for weight, value in zip(self.coefficients, vector)))


def build_report(
    samples: Sequence[Sample],
    *,
    cutoff: datetime,
    data_source: dict[str, str],
    test_fraction: float = 0.2,
    seed: int = 0,
    report_sample_limit: int = MAX_REPORTED_SAMPLES,
    sampling: dict[str, object] | None = None,
) -> dict[str, object]:
    if not samples:
        raise ValueError("no merge-point samples were loaded")
    if report_sample_limit < 1:
        raise ValueError("report_sample_limit must be positive")

    split = split_by_lot_time(samples, cutoff=cutoff, test_fraction=test_fraction, seed=seed)
    eligible = tuple(row for row in samples if _usable_label(row) and _raw_vector(row) is not None)
    split_train = tuple(row for row in split.train if _usable_label(row) and _raw_vector(row) is not None)
    split_test = tuple(row for row in split.test if _usable_label(row) and _raw_vector(row) is not None)

    evaluation_status, evaluation_reason = _evaluation_readiness(split_train, split_test)
    evaluated = evaluation_status == "evaluated"
    fit_rows = split_train if evaluated else eligible
    fit_lots = {row.lot for row in fit_rows}
    can_fit = _has_both_classes(fit_rows)
    model = fit_logistic(fit_rows, negative_weight=DEFAULT_NEGATIVE_WEIGHT) if can_fit else None

    if evaluated:
        model_status = "validated_candidate" if len(fit_lots) >= MIN_TRAIN_LOTS else "exploratory_only"
    elif model is not None:
        model_status = "exploratory_only"
    else:
        model_status = "unavailable"

    metrics: dict[str, float | int] | None = None
    bootstrap: dict[str, object] | None = None
    reference: dict[str, object] | None = None
    breakdown: dict[str, list[dict[str, object]]] | None = None
    if evaluated and model is not None:
        scored_test = [(row, model.score(row)) for row in split_test]
        usable_scores = [(row, score) for row, score in scored_test if score is not None]
        labels = [int(row.label) for row, _ in usable_scores]
        scores = [float(score) for _, score in usable_scores]
        metric_values = _weighted_metrics(usable_scores)
        metrics = {
            **metric_values,
            "evaluated_rows": len(labels),
            "positive_rows": sum(labels),
            "negative_rows": len(labels) - sum(labels),
        }
        bootstrap = _lot_bootstrap(usable_scores, seed=seed)
        paired = [(row, score, reference_score(row)) for row, score in usable_scores]
        paired = [(row, score, ref) for row, score, ref in paired if ref is not None]
        reference = {
            "name": REFERENCE_NAME,
            "description": "Fixed single-feature ranking by log1p(area_total); no fitting.",
            "metrics": _weighted_metrics([(row, ref) for row, _, ref in paired]),
            "paired_bootstrap": _paired_auc_bootstrap(paired, seed=seed),
        }
        breakdown = {
            "machine": _breakdown(paired, lambda row: row.key.machine),
            "model": _breakdown(paired, lambda row: row.key.result_group.split("/", 1)[0]),
        }

    sensitivity: dict[str, object] = {
        "negative_weights": list(SENSITIVITY_NEGATIVE_WEIGHTS),
        "rank_top_10_overlap": None,
        "note": "Jaccard overlap of top-decile rankings under alternative weights for assumed-good negatives; it is stability, not accuracy.",
    }
    if can_fit:
        sensitivity_models = [
            model if weight == DEFAULT_NEGATIVE_WEIGHT else fit_logistic(fit_rows, negative_weight=weight)
            for weight in SENSITIVITY_NEGATIVE_WEIGHTS
        ]
        ranking_rows = split.test if evaluated else samples
        sensitivity_rows = tuple(row for row in ranking_rows if _raw_vector(row) is not None)
        top_sets = [_top_decile(model_variant, sensitivity_rows) for model_variant in sensitivity_models]
        overlaps: dict[str, float | None] = {}
        for left, right in ((0, 1), (1, 2), (0, 2)):
            key = f"{SENSITIVITY_NEGATIVE_WEIGHTS[left]:.2f}_vs_{SENSITIVITY_NEGATIVE_WEIGHTS[right]:.2f}"
            union = top_sets[left] | top_sets[right]
            overlaps[key] = len(top_sets[left] & top_sets[right]) / len(union) if union else None
        sensitivity["rank_top_10_overlap"] = overlaps

    prediction_rows = split.test if evaluated else tuple(samples)
    prediction_role = "held_out" if evaluated else "exploratory_in_sample"
    scores_by_key: dict[object, float | None] = {}
    if model is not None:
        scores_by_key = {row.key: model.score(row) for row in prediction_rows}
    ranked = sorted(
        prediction_rows,
        key=lambda row: (scores_by_key.get(row.key) is not None, scores_by_key.get(row.key) or 0.0),
        reverse=True,
    )[:report_sample_limit]

    all_lots = {row.lot for row in samples}
    coverage = {
        "samples": _weighted_count(samples),
        "loaded_rows": len(samples),
        "lots": len(all_lots),
        "observed": _weighted_count(row for row in samples if row.vrs_observed),
        "unlabeled": _weighted_count(row for row in samples if row.label is None and not row.excluded),
        "positive": _weighted_count(row for row in samples if row.label == 1 and not row.excluded),
        "assumed_good": _weighted_count(
            row for row in samples if row.label == 0 and row.label_confidence == LabelConfidence.LOW
        ),
        "conflicting": _weighted_count(row for row in samples if row.excluded),
        "feature_incomplete": _weighted_count(row for row in samples if row.missing_feature_bits != 0),
        "images": dict(Counter(row.image_status for row in samples)),
    }

    train_classes = _class_counts(split_train)
    test_classes = _class_counts(split_test)
    evaluation = {
        "status": evaluation_status,
        "reason": evaluation_reason,
        "split": {
            "cutoff": _iso(cutoff),
            "train_lots": sorted({row.lot for row in split_train}),
            "test_lots": sorted({row.lot for row in split_test}),
            "excluded_lots": sorted({row.lot for row in split.excluded}),
            "train_rows": len(split_train),
            "test_rows": len(split_test),
            "train_classes": train_classes,
            "test_classes": test_classes,
            "train_class_lots": _class_lot_counts(split_train),
            "test_class_lots": _class_lot_counts(split_test),
        },
        "metrics": metrics,
        "metric_weighting": "Each class receives equal total weight; within each class, each represented LOT receives equal weight.",
        "bootstrap": bootstrap,
        "reference": reference,
        "breakdown": breakdown,
        "label_basis": "Historical VRS rewrite: keyed non-zero ivs_1 positive; zero/empty assumed good with low confidence; absent rewrites and conflicts excluded.",
        "policy": {
            "min_train_lots": MIN_TRAIN_LOTS,
            "min_test_lots": MIN_TEST_LOTS,
            "min_class_rows_per_split": MIN_CLASS_ROWS,
            "min_class_lots_per_split": MIN_CLASS_LOTS,
            "test_fraction_for_pre_cutoff_lots": test_fraction,
        },
    }
    model_info = {
        "status": model_status,
        "name": "regularized_structured_logistic",
        "version": "structured-logistic-v2",
        "feature_allowlist": list(FEATURES),
        "negative_weight": DEFAULT_NEGATIVE_WEIGHT,
        "l2_strength": L2_STRENGTH,
        "coefficients": (
            {"intercept": model.intercept, **dict(zip(FEATURES, model.coefficients))}
            if model is not None
            else None
        ),
        "threshold_policy": "ranking_only_no_operational_cutoff",
        "training_rows": len(fit_rows) if model is not None else 0,
        "training_lots": sorted(fit_lots) if model is not None else [],
    }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": data_source,
        "historical_vrs_only": True,
        "scope": "Ranks already-detected AFVI merge points against historical VRS rewrites; it cannot identify defects AFVI did not detect or validate production recall.",
        "coverage": coverage,
        "sampling": sampling,
        "evaluation": evaluation,
        "model": model_info,
        "samples": [
            _sample_report(row, scores_by_key.get(row.key), prediction_role=prediction_role)
            for row in ranked
        ],
    }


def fit_logistic(samples: Sequence[Sample], *, negative_weight: float = DEFAULT_NEGATIVE_WEIGHT) -> LogisticModel:
    if not 0 < negative_weight <= 1:
        raise ValueError("negative_weight must be in (0, 1]")
    usable = [(row, _raw_vector(row)) for row in samples if _usable_label(row)]
    usable = [(row, vector) for row, vector in usable if vector is not None]
    labels = [int(row.label) for row, _ in usable]
    if len(set(labels)) < 2:
        raise ValueError("training requires both positive and negative labels")
    if not usable:
        raise ValueError("no complete labeled training samples")

    row_weights = _training_weights(usable, negative_weight)
    total_weight = sum(row_weights)
    raw_vectors = [vector for _, vector in usable]
    means = tuple(
        sum(weight * row[i] for row, weight in zip(raw_vectors, row_weights)) / total_weight
        for i in range(len(FEATURES))
    )
    scales = tuple(
        max(
            1.0e-9,
            math.sqrt(
                sum(weight * (row[i] - means[i]) ** 2 for row, weight in zip(raw_vectors, row_weights))
                / total_weight
            ),
        )
        for i in range(len(FEATURES))
    )
    vectors = [tuple((value - mean) / scale for value, mean, scale in zip(row, means, scales)) for row in raw_vectors]
    weights = row_weights
    coefficients = [0.0] * len(FEATURES)
    intercept = math.log((sum(w for w, y in zip(weights, labels) if y == 1) + 0.5) / (sum(w for w, y in zip(weights, labels) if y == 0) + 0.5))
    learning_rate = 0.08

    for _ in range(MAX_ITERATIONS):
        grad_intercept = 0.0
        grad = [L2_STRENGTH * value for value in coefficients]
        for vector, label, weight in zip(vectors, labels, weights):
            error = (_sigmoid(intercept + sum(coef * value for coef, value in zip(coefficients, vector))) - label) * weight / total_weight
            grad_intercept += error
            for i, value in enumerate(vector):
                grad[i] += error * value
        step = max(abs(grad_intercept), *(abs(value) for value in grad))
        if step < 1.0e-7:
            break
        intercept -= learning_rate * grad_intercept
        coefficients = [value - learning_rate * derivative for value, derivative in zip(coefficients, grad)]

    return LogisticModel(means, scales, tuple(coefficients), intercept, negative_weight)


def _training_weights(
    samples_and_vectors: Sequence[tuple[Sample, tuple[float, ...]]], negative_weight: float
) -> tuple[float, ...]:
    base_weights = [
        (1.0 if sample.label == 1 else negative_weight) * sample.weight for sample, _ in samples_and_vectors
    ]
    lot_totals: dict[str, float] = defaultdict(float)
    for (sample, _), weight in zip(samples_and_vectors, base_weights):
        lot_totals[sample.lot] += weight
    return tuple(
        weight / lot_totals[sample.lot]
        for (sample, _), weight in zip(samples_and_vectors, base_weights)
    )


def reference_score(sample: Sample) -> float | None:
    """Fixed comparison ranking: the first allow-listed feature, log1p(area_total)."""
    raw = _raw_vector(sample)
    return None if raw is None else raw[0]


def _weighted_count(samples: Iterable[Sample]) -> int:
    return round(sum(row.weight for row in samples))


def _breakdown(
    rows: Sequence[tuple[Sample, float, float]], group_of: Callable[[Sample], str]
) -> list[dict[str, object]]:
    grouped: dict[str, list[tuple[Sample, float, float]]] = defaultdict(list)
    for item in rows:
        grouped[group_of(item[0])].append(item)
    result: list[dict[str, object]] = []
    for name in sorted(grouped):
        items = grouped[name]
        lots_by_class = _class_lot_counts(row for row, _, _ in items)
        entry: dict[str, object] = {
            "name": name,
            "rows": len(items),
            "lots": len({row.lot for row, _, _ in items}),
            "class_lots": lots_by_class,
            "model_roc_auc": None,
            "reference_roc_auc": None,
        }
        if min(lots_by_class.values()) > 0:
            entry["model_roc_auc"] = _weighted_metrics([(row, score) for row, score, _ in items])["roc_auc"]
            entry["reference_roc_auc"] = _weighted_metrics([(row, ref) for row, _, ref in items])["roc_auc"]
        result.append(entry)
    return result


def _paired_auc_bootstrap(
    rows: Sequence[tuple[Sample, float, float]], *, seed: int
) -> dict[str, object] | None:
    """LOT-cluster bootstrap of model AUC minus reference AUC on the same draws."""
    by_lot: dict[str, list[tuple[Sample, float, float]]] = defaultdict(list)
    for item in rows:
        by_lot[item[0].lot].append(item)
    lots = sorted(by_lot)
    if len(lots) < MIN_TEST_LOTS:
        return None
    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        selected = [rng.choice(lots) for _ in lots]
        draw: list[tuple[Sample, float, float]] = []
        draw_lot_ids: list[str] = []
        for occurrence, lot in enumerate(selected):
            draw.extend(by_lot[lot])
            draw_lot_ids.extend(f"{occurrence}:{lot}" for _ in by_lot[lot])
        labels_by_lot: dict[int, set[str]] = defaultdict(set)
        for (sample, _, _), lot_id in zip(draw, draw_lot_ids):
            labels_by_lot[int(sample.label)].add(lot_id)
        if not labels_by_lot[0] or not labels_by_lot[1]:
            continue
        model_auc = _weighted_metrics([(row, score) for row, score, _ in draw], metric_lot_ids=draw_lot_ids)["roc_auc"]
        reference_auc = _weighted_metrics([(row, ref) for row, _, ref in draw], metric_lot_ids=draw_lot_ids)["roc_auc"]
        differences.append(model_auc - reference_auc)
    if len(differences) < BOOTSTRAP_REPLICATES * 0.8:
        return {"method": "paired_lot_cluster_bootstrap", "valid_replicates": len(differences), "roc_auc_difference": None}
    return {
        "method": "paired_lot_cluster_bootstrap",
        "valid_replicates": len(differences),
        "roc_auc_difference": {
            "low": _quantile(differences, 0.025),
            "high": _quantile(differences, 0.975),
        },
    }


def _raw_vector(sample: Sample) -> tuple[float, ...] | None:
    if sample.missing_feature_bits:
        return None
    values: list[float] = []
    for name in FEATURES:
        value = sample.features.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number) or number < 0:
            return None
        values.append(math.log1p(number))
    return tuple(values)


def _usable_label(sample: Sample) -> bool:
    return sample.label in (0, 1) and not sample.excluded and sample.vrs_observed


def _has_both_classes(samples: Iterable[Sample]) -> bool:
    return {row.label for row in samples if _usable_label(row)} == {0, 1}


def _class_counts(samples: Iterable[Sample]) -> dict[str, int]:
    counts = Counter(str(row.label) for row in samples if _usable_label(row))
    return {"positive": counts.get("1", 0), "assumed_good": counts.get("0", 0)}


def _class_lot_counts(samples: Iterable[Sample]) -> dict[str, int]:
    lots_by_class: dict[int, set[str]] = defaultdict(set)
    for row in samples:
        if _usable_label(row):
            lots_by_class[int(row.label)].add(row.lot)
    return {
        "positive": len(lots_by_class[1]),
        "assumed_good": len(lots_by_class[0]),
    }


def _evaluation_readiness(
    train: Sequence[Sample], test: Sequence[Sample]
) -> tuple[str, str | None]:
    if len({row.lot for row in train}) < MIN_TRAIN_LOTS:
        return "insufficient_support", f"Need at least {MIN_TRAIN_LOTS} independent training LOTs."
    if len({row.lot for row in test}) < MIN_TEST_LOTS:
        return "insufficient_support", f"Need at least {MIN_TEST_LOTS} independent held-out LOTs."
    if not _has_both_classes(train):
        return "insufficient_support", "Training split does not contain both label classes."
    if not _has_both_classes(test):
        return "insufficient_support", "Held-out LOTs do not contain both label classes."
    for name, rows in (("training", train), ("held-out", test)):
        lots_by_class = _class_lot_counts(rows)
        for class_name, count in lots_by_class.items():
            if count < MIN_CLASS_LOTS:
                return (
                    "insufficient_support",
                    f"{name.capitalize()} {class_name} labels need at least {MIN_CLASS_LOTS} independent LOTs.",
                )
    for name, rows in (("training", train), ("held-out", test)):
        counts = _class_counts(rows)
        if min(counts.values()) < MIN_CLASS_ROWS:
            return "insufficient_support", f"{name.capitalize()} split needs at least {MIN_CLASS_ROWS} rows from each label class."
    return "evaluated", None


def _weighted_metrics(
    rows: Sequence[tuple[Sample, float]], *, metric_lot_ids: Sequence[str] | None = None
) -> dict[str, float]:
    if not rows:
        raise ValueError("metrics require labeled predictions")
    if metric_lot_ids is None:
        metric_lot_ids = [sample.lot for sample, _ in rows]
    if len(metric_lot_ids) != len(rows):
        raise ValueError("metric LOT ids must match the prediction rows")

    labels = [int(sample.label) for sample, _ in rows]
    scores = [score for _, score in rows]
    weights = _equal_class_lot_weights(labels, metric_lot_ids)
    return {
        "roc_auc": _weighted_roc_auc(labels, scores, weights),
        "average_precision": _weighted_average_precision(labels, scores, weights),
    }


def _equal_class_lot_weights(labels: Sequence[int], lot_ids: Sequence[str]) -> tuple[float, ...]:
    lots_by_class: dict[int, set[str]] = defaultdict(set)
    rows_by_group: Counter[tuple[int, str]] = Counter()
    for label, lot_id in zip(labels, lot_ids):
        if label not in (0, 1):
            raise ValueError("metrics require binary labels")
        lots_by_class[label].add(lot_id)
        rows_by_group[(label, lot_id)] += 1
    if not lots_by_class[0] or not lots_by_class[1]:
        raise ValueError("metrics require both classes")
    return tuple(
        0.5 / len(lots_by_class[label]) / rows_by_group[(label, lot_id)]
        for label, lot_id in zip(labels, lot_ids)
    )


def _weighted_roc_auc(
    labels: Sequence[int], scores: Sequence[float], weights: Sequence[float]
) -> float:
    ordered = sorted(zip(scores, labels, weights), key=lambda row: row[0])
    positive_total = sum(weight for _, label, weight in ordered if label == 1)
    negative_total = sum(weight for _, label, weight in ordered if label == 0)
    concordant = 0.0
    negative_below = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        group = ordered[index:end]
        positive_weight = sum(weight for _, label, weight in group if label == 1)
        negative_weight = sum(weight for _, label, weight in group if label == 0)
        concordant += positive_weight * (negative_below + 0.5 * negative_weight)
        negative_below += negative_weight
        index = end
    return concordant / (positive_total * negative_total)


def _weighted_average_precision(
    labels: Sequence[int], scores: Sequence[float], weights: Sequence[float]
) -> float:
    ordered = sorted(zip(scores, labels, weights), key=lambda row: row[0], reverse=True)
    positive_total = sum(weight for _, label, weight in ordered if label == 1)
    cumulative_positive = 0.0
    cumulative_weight = 0.0
    average_precision = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        group = ordered[index:end]
        positive_weight = sum(weight for _, label, weight in group if label == 1)
        group_weight = sum(weight for _, _, weight in group)
        cumulative_positive += positive_weight
        cumulative_weight += group_weight
        if positive_weight:
            average_precision += (positive_weight / positive_total) * (
                cumulative_positive / cumulative_weight
            )
        index = end
    return average_precision


def _lot_bootstrap(
    rows: Sequence[tuple[Sample, float]], *, seed: int
) -> dict[str, object] | None:
    by_lot: dict[str, list[tuple[Sample, float]]] = defaultdict(list)
    for sample, score in rows:
        by_lot[sample.lot].append((sample, score))
    lots = sorted(by_lot)
    if len(lots) < MIN_TEST_LOTS:
        return None
    rng = random.Random(seed)
    metrics: dict[str, list[float]] = {"roc_auc": [], "average_precision": []}
    valid = 0
    for _ in range(BOOTSTRAP_REPLICATES):
        selected = [rng.choice(lots) for _ in lots]
        draw: list[tuple[Sample, float]] = []
        draw_lot_ids: list[str] = []
        for occurrence, lot in enumerate(selected):
            lot_rows = by_lot[lot]
            draw.extend(lot_rows)
            draw_lot_ids.extend(f"{occurrence}:{lot}" for _ in lot_rows)
        if len({sample.label for sample, _ in draw}) < 2:
            continue
        valid += 1
        draw_metrics = _weighted_metrics(draw, metric_lot_ids=draw_lot_ids)
        metrics["roc_auc"].append(draw_metrics["roc_auc"])
        metrics["average_precision"].append(draw_metrics["average_precision"])
    if valid < BOOTSTRAP_REPLICATES * 0.8:
        return {
            "method": "lot_cluster_bootstrap_equal_class_lot_weighted",
            "weighting": "equal_class_then_equal_lot_within_class",
            "valid_replicates": valid,
            "intervals": None,
        }
    intervals = {
        name: {"low": _quantile(values, 0.025), "high": _quantile(values, 0.975)}
        for name, values in metrics.items()
    }
    return {
        "method": "lot_cluster_bootstrap_equal_class_lot_weighted_holdout_predictions",
        "weighting": "equal_class_then_equal_lot_within_class",
        "valid_replicates": valid,
        "intervals": intervals,
    }


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _top_decile(model: LogisticModel, samples: Sequence[Sample]) -> set[object]:
    scored = [(row.key, score) for row in samples if (score := model.score(row)) is not None]
    if not scored:
        return set()
    top_count = max(1, math.ceil(len(scored) * 0.1))
    return {key for key, _ in sorted(scored, key=lambda row: row[1], reverse=True)[:top_count]}


def _sample_report(
    sample: Sample, score: float | None, *, prediction_role: str
) -> dict[str, object]:
    evidence_names = ("area_total", "area_max", "length_max", "width_max", "light_id", "channel_idx", "sr_metal", "pc_idx")
    return {
        "key": {
            "machine": sample.key.machine,
            "result_group": sample.key.result_group,
            "strip": sample.key.strip,
            "side": sample.key.side,
            "unit_idx": sample.key.unit_idx,
            "img_merge_idx": sample.key.img_merge_idx,
        },
        "lot": sample.lot,
        "score": score,
        "prediction_role": prediction_role,
        "label": sample.label,
        "label_source": sample.label_source.value,
        "label_confidence": sample.label_confidence.value,
        "ivs_1": sample.ivs_1,
        "image_ref": sample.image_ref,
        "image_status": sample.image_status,
        "evidence": {name: sample.features.get(name) for name in evidence_names if name in sample.features},
    }


def _sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-min(value, 700.0))
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(max(value, -700.0))
    return exponential / (1.0 + exponential)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cutoff must include a timezone offset")
    return value.isoformat()
