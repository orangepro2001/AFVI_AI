"""Fine-tune a small CNN on defect crops to predict whether VRS keyed the point.

Training LOTs come from the split report; a seeded subset of them is held aside for early
stopping and for the decision threshold, so the report's held-out LOTs are scored once at the
end. Sampling gives every (LOT, class) cell the same weight, like the structured evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import models, transforms

from ..baseline import _lot_bootstrap, _weighted_metrics, review_recall
from ..train import write_report

MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
VALIDATION_FRACTION = 0.15


def eval_transform() -> transforms.Compose:
    return transforms.Compose([transforms.ToTensor(), transforms.Normalize(MEAN, STD)])


def train_transform() -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.RandomHorizontalFlip(),
            transforms.RandomVerticalFlip(),
            transforms.RandomChoice([transforms.RandomRotation((angle, angle)) for angle in (0, 90, 180, 270)]),
            transforms.ToTensor(),
            transforms.Normalize(MEAN, STD),
        ]
    )


class CropDataset(Dataset):
    def __init__(self, root: Path, rows: list[dict], transform: transforms.Compose) -> None:
        self.root = root
        self.rows = rows
        self.transform = transform

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        row = self.rows[index]
        with Image.open(self.root / row["image"]) as image:
            tensor = self.transform(image.convert("RGB"))
        return tensor, torch.tensor(float(row["label"])), index


def build_model(pretrained: bool = True) -> nn.Module:
    weights = models.ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
    model = models.resnet18(weights=weights)
    model.fc = nn.Linear(model.fc.in_features, 1)
    return model


def balanced_weights(rows: list[dict]) -> list[float]:
    """Each (LOT, label) cell gets equal total sampling probability."""
    cells: dict[tuple, int] = defaultdict(int)
    for row in rows:
        cells[(row["lot"], row["label"])] += 1
    return [1.0 / cells[(row["lot"], row["label"])] for row in rows]


def split_rows(rows: list[dict], seed: int) -> tuple[list[dict], list[dict], list[dict]]:
    train_lots = sorted({row["lot"] for row in rows if row["split"] == "train"})
    ordered = sorted(train_lots, key=lambda lot: hashlib.sha256(f"{seed}\0val\0{lot}".encode()).digest())
    validation_lots = set(ordered[: max(3, math.ceil(len(ordered) * VALIDATION_FRACTION))])
    fit = [row for row in rows if row["split"] == "train" and row["lot"] not in validation_lots]
    validation = [row for row in rows if row["split"] == "train" and row["lot"] in validation_lots]
    test = [row for row in rows if row["split"] == "test"]
    return fit, validation, test


@torch.no_grad()
def predict(model: nn.Module, loader: DataLoader, device: torch.device) -> list[float]:
    model.eval()
    scores = [0.0] * len(loader.dataset)
    for images, _, indices in loader:
        logits = model(images.to(device, non_blocking=True)).squeeze(1)
        for index, value in zip(indices.tolist(), torch.sigmoid(logits).cpu().tolist()):
            scores[index] = value
    return scores


def as_metric_rows(rows: list[dict], scores: list[float]) -> list[tuple[SimpleNamespace, float]]:
    return [
        (SimpleNamespace(label=int(row["label"]), lot=row["lot"], weight=float(row.get("weight") or 1.0)), score)
        for row, score in zip(rows, scores)
    ]


def equal_error_threshold(rows: list[dict], scores: list[float]) -> dict[str, float]:
    """Threshold where the LOT-averaged keyed miss rate equals the LOT-averaged good false-alarm rate."""
    by_lot: dict[str, dict[int, list[float]]] = defaultdict(lambda: {0: [], 1: []})
    for row, score in zip(rows, scores):
        by_lot[row["lot"]][int(row["label"])].append(score)
    lots = [cells for cells in by_lot.values() if cells[0] and cells[1]]

    def rates(threshold: float) -> tuple[float, float]:
        miss = sum(sum(s < threshold for s in cells[1]) / len(cells[1]) for cells in lots) / len(lots)
        alarm = sum(sum(s >= threshold for s in cells[0]) / len(cells[0]) for cells in lots) / len(lots)
        return miss, alarm

    low, high = 0.0, 1.0
    for _ in range(40):
        middle = (low + high) / 2
        miss, alarm = rates(middle)
        if miss < alarm:
            low = middle
        else:
            high = middle
    threshold = (low + high) / 2
    miss, alarm = rates(threshold)
    return {"threshold": threshold, "keyed_miss_rate": miss, "good_false_alarm_rate": alarm}


def rates_at(rows: list[dict], scores: list[float], threshold: float) -> dict[str, float | None]:
    by_lot: dict[str, dict[int, list[float]]] = defaultdict(lambda: {0: [], 1: []})
    for row, score in zip(rows, scores):
        by_lot[row["lot"]][int(row["label"])].append(score)
    lots = [cells for cells in by_lot.values() if cells[0] and cells[1]]
    if not lots:
        return {"keyed_miss_rate": None, "good_false_alarm_rate": None}
    return {
        "keyed_miss_rate": sum(sum(s < threshold for s in c[1]) / len(c[1]) for c in lots) / len(lots),
        "good_false_alarm_rate": sum(sum(s >= threshold for s in c[0]) / len(c[0]) for c in lots) / len(lots),
    }


def evaluate(rows: list[dict], scores: list[float], threshold: float, seed: int) -> dict[str, object]:
    metric_rows = as_metric_rows(rows, scores)
    families: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        families[row["family"]].append(index)
    breakdown = []
    for family, indices in sorted(families.items()):
        subset = [metric_rows[i] for i in indices]
        labels = {sample.label for sample, _ in subset}
        breakdown.append(
            {
                "family": family,
                "lots": len({rows[i]["lot"] for i in indices}),
                "images": len(indices),
                "roc_auc": _weighted_metrics(subset)["roc_auc"] if labels == {0, 1} else None,
            }
        )
    return {
        "images": len(rows),
        "lots": len({row["lot"] for row in rows}),
        "metrics": _weighted_metrics(metric_rows),
        "bootstrap": _lot_bootstrap(metric_rows, seed=seed),
        "at_threshold": rates_at(rows, scores, threshold),
        "review_curve": {"fractions": [0.1, 0.2, 0.3, 0.5], "model": review_recall(metric_rows)},
        "by_family": breakdown,
    }


def compare_on(model_dir: Path, loader: DataLoader, rows: list[dict], device: torch.device, seed: int) -> dict[str, object]:
    """The given model on the same held-out rows, at its own threshold (the one it was promoted with)."""
    info = json.loads((model_dir / "model.json").read_text(encoding="utf-8"))
    model = build_model(pretrained=False).to(device)
    model.load_state_dict(torch.load(model_dir / "model.pt", map_location=device))
    scores = predict(model, loader, device)
    result = evaluate(rows, scores, float(info["threshold"]["threshold"]), seed)
    return {"model_dir": str(model_dir.resolve()), **result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the AFVI defect-image model.")
    parser.add_argument("--dataset", required=True, type=Path, help="directory written by afvi_ai.vision.dataset")
    parser.add_argument("--output-dir", required=True, type=Path, help="model directory outside Git")
    parser.add_argument("--report", required=True, type=Path, help="evaluation JSON destination outside Git")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--samples-per-epoch", type=int, default=40000)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--compare-model",
        type=Path,
        help="model directory (model.pt + model.json) also scored on the same held-out rows, for the automatic promotion",
    )
    args = parser.parse_args(argv)

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    rows = [json.loads(line) for line in (args.dataset / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    fit_rows, validation_rows, test_rows = split_rows(rows, args.seed)
    print(json.dumps({"fit": len(fit_rows), "validation": len(validation_rows), "test": len(test_rows), "device": str(device)}), flush=True)

    sampler = WeightedRandomSampler(balanced_weights(fit_rows), num_samples=args.samples_per_epoch, replacement=True)
    loader_options = {"batch_size": args.batch_size, "num_workers": args.workers, "pin_memory": device.type == "cuda"}
    fit_loader = DataLoader(CropDataset(args.dataset, fit_rows, train_transform()), sampler=sampler, **loader_options)
    validation_loader = DataLoader(CropDataset(args.dataset, validation_rows, eval_transform()), **loader_options)
    test_loader = DataLoader(CropDataset(args.dataset, test_rows, eval_transform()), **loader_options)

    model = build_model().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    loss_function = nn.BCEWithLogitsLoss()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    best_auc, best_epoch, history = -1.0, -1, []
    for epoch in range(args.epochs):
        model.train()
        started, total_loss, batches = time.monotonic(), 0.0, 0
        for images, labels, _ in fit_loader:
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(images.to(device, non_blocking=True)).squeeze(1), labels.to(device))
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            batches += 1
        scheduler.step()
        validation_scores = predict(model, validation_loader, device)
        auc = _weighted_metrics(as_metric_rows(validation_rows, validation_scores))["roc_auc"]
        history.append({"epoch": epoch + 1, "loss": total_loss / max(batches, 1), "validation_roc_auc": auc, "seconds": time.monotonic() - started})
        print(json.dumps(history[-1]), flush=True)
        if auc > best_auc:
            best_auc, best_epoch = auc, epoch + 1
            torch.save(model.state_dict(), args.output_dir / "model.pt")

    model.load_state_dict(torch.load(args.output_dir / "model.pt", map_location=device))
    validation_scores = predict(model, validation_loader, device)
    threshold = equal_error_threshold(validation_rows, validation_scores)
    validation_result = evaluate(validation_rows, validation_scores, threshold["threshold"], args.seed)
    test_scores = predict(model, test_loader, device)
    test_result = evaluate(test_rows, test_scores, threshold["threshold"], args.seed)
    compared = None
    if args.compare_model is not None:
        compared = compare_on(args.compare_model, test_loader, test_rows, device, args.seed)
    dataset_info = json.loads((args.dataset / "dataset.json").read_text(encoding="utf-8"))
    metadata = {
        "architecture": "resnet18_imagenet_finetuned",
        "input_size": dataset_info["size"],
        "normalize": {"mean": MEAN, "std": STD},
        "label_meaning": "1 = VRS operator keyed a defect code for this merge point; 0 = reviewed and left unkeyed",
        "threshold": threshold,
        "best_epoch": best_epoch,
        "history": history,
        "validation_lots": sorted({row["lot"] for row in validation_rows}),
        "dataset": {"images": len(rows), "per_lot_class": dataset_info["per_lot_class"], "counts": dataset_info["counts"]},
    }
    (args.output_dir / "model.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=1) + "\n")
    write_report(
        args.report,
        {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": metadata,
            "validation": validation_result,
            "held_out": test_result,
            "held_out_note": "Held-out LOTs are the structured evaluation's test LOTs; the image model never trained or tuned on them.",
            "compared_model_on_held_out": compared,
        },
    )
    print(json.dumps({"best_epoch": best_epoch, "validation_roc_auc": best_auc, "held_out_roc_auc": test_result["metrics"]["roc_auc"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
