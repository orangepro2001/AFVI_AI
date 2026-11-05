import csv
import json
import tempfile
import unittest
from pathlib import Path

from afvi_ai.baseline import fit_logistic, model_to_dict
from afvi_ai.score import main
from test_confirm import make_rows


def export_row(lot, index, size, positive):
    return {
        "machine": "AFVI13",
        "result_group": f"MODEL/{lot}/1",
        "lot": lot,
        "strip": "0001",
        "side": "FC",
        "unit_idx": 0,
        "img_merge_idx": index,
        "features": {"area_total": size, "length_max": size, "width_max": size},
        "vrs_observed": True,
        "ivs_1": "OT15" if positive else "0",
        "inspected_at": "2026-11-10T00:00:00+09:00",
        "image_rel": f"0001/Unit/U0/FC/p{index}.bmp",
    }


class ScoreCliTests(unittest.TestCase):
    def test_writes_ranked_csv_per_lot_with_family_evidence(self):
        training = make_rows([f"OLD-{i}" for i in range(6)])
        model = fit_logistic(training, l2=0.01)
        artifact = model_to_dict(
            model,
            training_lots=sorted({row.lot for row in training}),
            metadata={
                "training_families": ["MODEL"],
                "held_out_breakdown": {"model": [{"name": "MODEL", "lots": 3, "model_roc_auc": 0.81, "model_recall_at_30": 0.62}]},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.json").write_text(json.dumps(artifact))
            with (root / "new.jsonl").open("w") as stream:
                for index, size in enumerate((1.0, 30.0, 5.0), start=1):
                    stream.write(json.dumps(export_row("NEW-LOT", index, size, positive=size > 10)) + "\n")

            image = root / "share" / "ResultData/Data/AFVI13/MODEL/NEW-LOT/1/0001/Unit/U0/FC/p2.bmp"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"")

            code = main(
                [
                    "--model", str(root / "model.json"),
                    "--input", str(root / "new.jsonl"),
                    "--output-dir", str(root / "out"),
                    "--share-root", str(root / "share"),
                    "--check-images", "2",
                ]
            )

            self.assertEqual(code, 0)
            with (root / "out" / "NEW-LOT.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["img_merge_idx"] for row in rows], ["2", "3", "1"])
            self.assertEqual(rows[0]["rank"], "1")
            self.assertEqual(rows[0]["vrs_code"], "OT15")
            self.assertEqual(rows[0]["result_data_image"], "ResultData/Data/AFVI13/MODEL/NEW-LOT/1/0001/Unit/U0/FC/p2.bmp")
            self.assertEqual([row["image_saved"] for row in rows], ["yes", "no", ""])
            self.assertEqual(rows[0]["family_heldout_roc_auc"], "0.810")
            self.assertEqual(rows[0]["family_in_training"], "yes")
            summary = json.loads((root / "out" / "summary.json").read_text())
            self.assertEqual(summary["lots"][0]["scored_points"], 3)
            self.assertFalse(summary["lots"][0]["lot_in_model_training"])

    def test_refuses_output_inside_repository(self):
        repository = Path(__file__).resolve().parents[2]
        with self.assertRaises(SystemExit):
            main(["--model", "missing.json", "--input", "missing.jsonl", "--output-dir", str(repository / "tmp-out")])


if __name__ == "__main__":
    unittest.main()
