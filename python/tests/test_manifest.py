import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from afvi_ai.manifest import ImageStatus, ManifestIndex, join_manifest_rel


class ManifestTests(unittest.TestCase):
    def _write_manifest(self, path: Path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def _row(self, rel, src=100, out=50):
        return {"rel": rel, "src": src, "out": out, "q": 95, "at": "2026-11-05T00:00:00Z"}

    def test_group_image_path_matches_scanner_relative_path(self):
        self.assertEqual(
            join_manifest_rel("AFVI13", "25FBO015-15/LOT-A/0298", "0001/Unit/U17/FC/p2_Sub1.bmp"),
            "25FBO015-15/LOT-A/0298/0001/Unit/U17/FC/p2_Sub1.bmp",
        )

    def test_jpg_path_uses_label_and_manifest_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            rel = "25FBO015-15/D07834026H3112/3/0001/Unit/U17/FC/p2_Sub1.bmp"
            self._write_manifest(manifest, [self._row(rel)])
            index = ManifestIndex.load(manifest, root / "images", "192.168.1.203")
            ref = index.resolve(rel)
            self.assertEqual(ref.path, root / "images" / "192.168.1.203" / rel.replace(".bmp", ".jpg"))
            self.assertEqual(ref.status, ImageStatus.MAPPED)

    def test_join_matches_real_result_data_group_without_machine_prefix(self):
        self.assertEqual(
            join_manifest_rel(
                "AFVI13",
                "25FBO015-15/D07834026H3112/3",
                "0001/Unit/U17/BC/p2.bmp",
            ),
            "25FBO015-15/D07834026H3112/3/0001/Unit/U17/BC/p2.bmp",
        )

    def test_unlisted_image_is_reported_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            self._write_manifest(manifest, [])
            index = ManifestIndex.load(manifest, root / "images", "label")
            ref = index.resolve("model/lot/1/0001/Unit/U1/FC/p1.bmp")
            self.assertEqual(ref.status, ImageStatus.MISSING_MANIFEST_ENTRY)
            self.assertIsNone(ref.path)

    def test_missing_output_file_is_reported_without_reading_image_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            rel = "model/lot/1/0001/Unit/U1/FC/p1.bmp"
            self._write_manifest(manifest, [self._row(rel)])
            index = ManifestIndex.load(manifest, root / "images", "label")
            ref = index.resolve(rel, verify_exists=True)
            self.assertEqual(ref.status, ImageStatus.MISSING_FILE)

    def test_manifest_conflict_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            rel = "model/lot/1/0001/Unit/U1/FC/p1.bmp"
            self._write_manifest(manifest, [self._row(rel), self._row(rel, out=51)])
            index = ManifestIndex.load(manifest, root / "images", "label")
            self.assertEqual(index.resolve(rel).status, ImageStatus.MANIFEST_CONFLICT)

    def test_identical_manifest_duplicates_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            rel = "model/lot/1/0001/Unit/U1/FC/p1.bmp"
            self._write_manifest(manifest, [self._row(rel), self._row(rel)])
            index = ManifestIndex.load(manifest, root / "images", "label")
            self.assertEqual(index.resolve(rel).status, ImageStatus.MAPPED)
            self.assertEqual(index.duplicate_count, 1)

    def test_manifest_is_read_only_and_image_bytes_are_never_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            rel = "model/lot/1/0001/Unit/U1/FC/p1.bmp"
            self._write_manifest(manifest, [self._row(rel)])
            before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
            image = root / "images" / "label" / rel.replace(".bmp", ".jpg")
            image.parent.mkdir(parents=True)
            image.write_bytes(b"jpg-bytes")
            original_open = Path.open

            def guarded_open(path, *args, **kwargs):
                if path.suffix.lower() in {".jpg", ".jpeg"}:
                    raise AssertionError("image bytes must not be opened")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", guarded_open):
                index = ManifestIndex.load(manifest, root / "images", "label")
                ref = index.resolve(rel, verify_exists=True)
            self.assertEqual(ref.status, ImageStatus.AVAILABLE)
            self.assertEqual((manifest.read_bytes(), manifest.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
