import json
import tempfile
import unittest
from pathlib import Path

from afvi_ai.spec_snapshot import main, model_folder, size_values

SPEC = """<?xml version="1.0" encoding="UTF-8"?>
<pixel Version="1.0">
  <GPNODE ID="1">
    <PNODE ID="1" NodeCheck="1">
      <CNODE ID="12" NodeCheck="1">
        <INSPECTION ParamKey="1024" ValR="20.0" ValG="0.0" ValB="50.0"/>
        <INSPECTION ParamKey="1000" ValR="230.0" ValG="0.0" ValB="0.0"/>
      </CNODE>
    </PNODE>
    <PNODE ID="2" NodeCheck="0">
      <CNODE ID="20" NodeCheck="1">
        <INSPECTION ParamKey="1002" ValR="0.0" ValG="7.0" ValB="0.0"/>
      </CNODE>
    </PNODE>
  </GPNODE>
</pixel>
"""

DRAFT = """<?xml version='1.0' encoding='utf-8'?>
<AfviParameterProposal version="0.1" status="DRAFT_NOT_FOR_MACHINE_USE" generated="2026-11-06T00:00:00+00:00">
  <Basis feature="area_max" />
  <Notes><Note>draft</Note></Notes>
  <Model name="7M99-586">
    <Condition side="BC" light="2" channel="3" srMetal="1" codeMain="10510" status="proposed" recommendation="candidate" recommendedMinArea="36" observedMinArea="35" trainObservedMin="35" heldOutObservedMin="35" reviewedPoints="100" keyedPoints="5" trainLotsWithKeyed="3">
      <MinDefectArea lossBudget="0.010" minArea="36" />
    </Condition>
    <Condition side="FC" light="1" channel="2" srMetal="2" status="keep_current_insufficient_data" recommendation="keep_current_insufficient_data" />
  </Model>
</AfviParameterProposal>
"""


class SpecSnapshotTests(unittest.TestCase):
    def test_size_values_reads_only_the_named_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "InspectionSpec.xml"
            path.write_text(SPEC)

            blue = size_values(path, "3")
            green = size_values(path, "2")

            self.assertEqual([(v["key"], v["node"], v["value"]) for v in blue], [("1024", "1-1-12", 50.0), ("1002", "1-2-20", 0.0)])
            self.assertFalse(green[1]["node_enabled"])
            self.assertEqual(green[1]["value"], 7.0)

    def test_model_folder_skips_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("7M99-586-00 - 복사본", "7M99-586-00bk", "7m99-586-00"):
                (root / name).mkdir()

            self.assertEqual(model_folder(root, "7M99-586"), "7m99-586-00")
            self.assertIsNone(model_folder(root, "7M99-999"))

    def test_main_attaches_bottom_vision_values_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_dir = root / "bm/INSPECT_SPEC/7M99-586-00/BOTTOM/LIGHT2"
            spec_dir.mkdir(parents=True)
            (spec_dir / "InspectionSpec.xml").write_text(SPEC)
            (root / "draft.xml").write_text(DRAFT)

            code = main(
                [
                    "--proposals", str(root / "draft.xml"),
                    "--inventory", f"BM={root / 'bm'}",
                    "--inventory", f"FM1={root / 'fm1'}",
                    "--output", str(root / "out.json"),
                ]
            )

            self.assertEqual(code, 0)
            result = json.loads((root / "out.json").read_text())
            conditions = result["drafts"][0]["conditions"]
            self.assertEqual(len(conditions), 1)
            spec = conditions[0]["spec"]
            self.assertEqual([entry["machine"] for entry in spec], ["BM"])
            self.assertEqual(spec[0]["model"], "7M99-586-00")
            self.assertEqual(spec[0]["link"], "/afvi/para/#BM/7M99-586-00/2")
            self.assertEqual(spec[0]["values"][0], {"key": "1024", "node": "1-1-12", "value": 50.0, "node_enabled": True})


if __name__ == "__main__":
    unittest.main()
