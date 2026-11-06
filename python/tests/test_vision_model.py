import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

try:
    import torch
    from PIL import Image
except ImportError:  # pragma: no cover - the structured pipeline runs without PyTorch
    torch = None


@unittest.skipIf(torch is None, "PyTorch is not installed")
class VisionModelTests(unittest.TestCase):
    def test_equal_error_threshold_balances_lot_averaged_rates(self):
        from afvi_ai.vision.train_image import equal_error_threshold, rates_at

        rows = [{"lot": lot, "label": label} for lot in ("A", "B") for label in (0, 0, 1, 1)]
        scores = [0.1, 0.4, 0.6, 0.9, 0.2, 0.3, 0.7, 0.8]
        threshold = equal_error_threshold(rows, scores)

        self.assertTrue(0.4 < threshold["threshold"] <= 0.6)
        self.assertAlmostEqual(threshold["keyed_miss_rate"], 0.0)
        self.assertEqual(rates_at(rows, scores, 0.65), {"keyed_miss_rate": 0.25, "good_false_alarm_rate": 0.0})

    def test_split_keeps_validation_lots_inside_training_lots(self):
        from afvi_ai.vision.train_image import split_rows

        rows = [{"lot": f"T{i}", "label": i % 2, "split": "train"} for i in range(20)]
        rows += [{"lot": "H0", "label": 1, "split": "test"}]
        fit, validation, test = split_rows(rows, seed=0)

        self.assertEqual(len(test), 1)
        self.assertGreaterEqual(len({row["lot"] for row in validation}), 3)
        self.assertFalse({row["lot"] for row in fit} & {row["lot"] for row in validation})

    def test_queue_worker_scores_uploads_and_rejects_garbage(self):
        from afvi_ai.vision.serve import Scorer, process_once
        from afvi_ai.vision.train_image import build_model

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            torch.save(build_model(pretrained=False).state_dict(), root / "model.pt")
            (root / "model.json").write_text(json.dumps({"input_size": 64, "threshold": {"threshold": 0.5}}))
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            buffer = BytesIO()
            Image.new("RGB", (400, 400), (10, 200, 10)).save(buffer, format="BMP")
            (inbox / "good-1.img").write_bytes(buffer.getvalue())
            (inbox / "bad-2.img").write_bytes(b"not an image")

            handled = process_once(Scorer(root, torch.device("cpu")), inbox, outbox)

            self.assertEqual(handled, 2)
            ok = json.loads((outbox / "good-1.json").read_text())
            self.assertEqual(ok["status"], "ok")
            self.assertIn(ok["verdict"], {"good", "defect"})
            self.assertEqual(ok["original_size"], [400, 400])
            self.assertEqual(json.loads((outbox / "bad-2.json").read_text())["status"], "error")
            self.assertEqual(list(inbox.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
