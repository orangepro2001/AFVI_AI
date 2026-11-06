"""File-queue inference worker for uploaded defect crops (no network port).

The web app writes ``<id>.img`` (and optionally ``<id>.json``) into the inbox directory; this
worker scores each image with the trained model and writes ``<id>.json`` into the outbox.
Images are decoded with Pillow under a pixel limit, preprocessed exactly like the training
set (RGB, resized to the model's input size), and deleted from the inbox after scoring.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path

import torch
from PIL import Image

from .train_image import build_model, eval_transform

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


class Scorer:
    def __init__(self, model_dir: Path, device: torch.device) -> None:
        self.metadata = json.loads((model_dir / "model.json").read_text(encoding="utf-8"))
        self.size = int(self.metadata["input_size"])
        self.threshold = float(self.metadata["threshold"]["threshold"])
        self.device = device
        self.model = build_model(pretrained=False)
        self.model.load_state_dict(torch.load(model_dir / "model.pt", map_location=device))
        self.model.to(device).eval()
        self.transform = eval_transform()

    @torch.no_grad()
    def score(self, data: bytes) -> dict[str, object]:
        with Image.open(BytesIO(data)) as image:
            image.load()
            original = image.size
            rgb = image.convert("RGB")
        resized = rgb.resize((self.size, self.size), Image.Resampling.BILINEAR)
        buffer = BytesIO()
        resized.save(buffer, format="JPEG", quality=90)
        with Image.open(BytesIO(buffer.getvalue())) as encoded:
            tensor = self.transform(encoded.convert("RGB")).unsqueeze(0).to(self.device)
        probability = float(torch.sigmoid(self.model(tensor)).squeeze())
        return {
            "score": probability,
            "threshold": self.threshold,
            "verdict": "defect" if probability >= self.threshold else "good",
            "original_size": list(original),
        }


def process_once(scorer: Scorer, inbox: Path, outbox: Path) -> int:
    handled = 0
    for path in sorted(inbox.glob("*.img")):
        request_id = path.stem
        if not request_id.replace("-", "").isalnum():
            path.unlink(missing_ok=True)
            continue
        result: dict[str, object] = {"id": request_id, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        try:
            if path.stat().st_size > MAX_UPLOAD_BYTES:
                raise ValueError("image is larger than 20 MB")
            result.update(status="ok", **scorer.score(path.read_bytes()))
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            result.update(status="error", error=str(error) or error.__class__.__name__)
        temporary = outbox / f"{request_id}.part"
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, outbox / f"{request_id}.json")
        path.unlink(missing_ok=True)
        (inbox / f"{request_id}.json").unlink(missing_ok=True)
        handled += 1
    return handled


def cleanup(outbox: Path, max_age_seconds: float = 3600) -> None:
    now = time.time()
    for path in outbox.glob("*.json"):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
        except OSError:
            continue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score uploaded defect images from a file queue.")
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--queue-dir", required=True, type=Path, help="directory holding inbox/ and outbox/")
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    parser.add_argument("--cpu", action="store_true", help="score on CPU instead of the GPU")
    args = parser.parse_args(argv)
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    torch.set_num_threads(2)
    scorer = Scorer(args.model_dir, device)
    inbox, outbox = args.queue_dir / "inbox", args.queue_dir / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    (args.queue_dir / "worker.json").write_text(
        json.dumps({"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "device": str(device), "pid": os.getpid()}),
        encoding="utf-8",
    )
    print(json.dumps({"ready": True, "device": str(device), "threshold": scorer.threshold}), flush=True)
    last_cleanup = 0.0
    while True:
        process_once(scorer, inbox, outbox)
        if time.monotonic() - last_cleanup > 600:
            cleanup(outbox)
            last_cleanup = time.monotonic()
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
