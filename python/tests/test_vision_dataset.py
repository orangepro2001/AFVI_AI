import json
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is optional for the structured pipeline
    Image = None

from afvi_ai.contracts import normalize_export_record


def record(index, positive, *, image_rel):
    return normalize_export_record(
        {
            "machine": "AFVI13",
            "result_group": "FAM/LOT-A/1",
            "lot": "LOT-A",
            "strip": "0001",
            "side": "FC",
            "unit_idx": 0,
            "img_merge_idx": index,
            "features": {"area_total": 5.0, "length_max": 2.0, "width_max": 1.0, "light_id": 1, "code_main": "10207"},
            "vrs_observed": True,
            "ivs_1": "OT12" if positive else "0",
            "inspected_at": "2026-09-01T00:00:00+09:00",
            "image_rel": image_rel,
        }
    )


@unittest.skipIf(Image is None, "Pillow is not installed")
class VisionDatasetTests(unittest.TestCase):
    def test_build_converts_existing_images_and_records_split(self):
        from afvi_ai.vision.dataset import build

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "share/ResultData/Data/AFVI13/FAM/LOT-A/1/0001/Unit/U0/FC"
            source_dir.mkdir(parents=True)
            Image.new("RGB", (400, 400), (200, 30, 30)).save(source_dir / "p1.bmp")
            samples = [
                record(1, True, image_rel="0001/Unit/U0/FC/p1.bmp"),
                record(2, False, image_rel="0001/Unit/U0/FC/p2.bmp"),
            ]

            counts = build(samples, share_root=root / "share", output_dir=root / "out", split_of={"LOT-A": "test"}, size=64)

            self.assertEqual(counts["written"], 1)
            self.assertEqual(counts["missing"], 1)
            rows = [json.loads(line) for line in (root / "out/manifest.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], 1)
            self.assertEqual(rows[0]["split"], "test")
            self.assertEqual(rows[0]["code_main"], "10207")
            with Image.open(root / "out" / rows[0]["image"]) as image:
                self.assertEqual(image.size, (64, 64))

            again = build(samples, share_root=root / "share", output_dir=root / "out", split_of={"LOT-A": "test"}, size=64)
            self.assertEqual(again["existing"], 1)

    def test_sources_cache_then_share_bmp_then_archived_jpg(self):
        """pximaged's cache first (no share load), then the BMP on the machine's own share, then the JPG that replaced
        an archived LOT's BMP on the NAS."""
        from afvi_ai.vision.dataset import build

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            strip = "ResultData/Data/AFVI13/FAM/LOT-A/1/0001/Unit/U0/FC"
            nas = root / "nas204"
            (nas / strip).mkdir(parents=True)
            Image.new("RGB", (400, 400), (10, 200, 10)).save(nas / strip / "p1.bmp")
            Image.new("RGB", (400, 400), (10, 10, 200)).save(nas / strip / "p2.jpg", quality=92)  # archived on the NAS
            cache = root / "cache"
            (cache / "FAM/LOT-A/1/0001/Unit/U0/FC").mkdir(parents=True)
            Image.new("RGB", (400, 400), (200, 10, 10)).save(cache / "FAM/LOT-A/1/0001/Unit/U0/FC/p3.jpg", quality=92)
            samples = [record(i, i == 1, image_rel=f"0001/Unit/U0/FC/p{i}.bmp") for i in (1, 2, 3, 4)]

            counts = build(samples, share_root=None, output_dir=root / "out", split_of={"LOT-A": "train"}, size=32,
                           machine_roots={"AFVI13": nas}, caches={"AFVI13": cache})

            self.assertEqual((counts["written"], counts["missing"], counts["from_cache"]), (3, 1, 1))
            rows = {json.loads(line)["image"] for line in (root / "out/manifest.jsonl").read_text().splitlines()}
            self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
