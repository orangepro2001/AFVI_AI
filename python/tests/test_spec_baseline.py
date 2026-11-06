import json
import tempfile
import unittest
from pathlib import Path

from afvi_ai.spec_baseline import main, production_models

DEFS = {
    "SpecParameter.xml": """<?xml version="1.0" encoding="UTF-8"?>
<pixel><stringpack LangCode="0"><string ParamKey="1024" Text="Min Defect Size(Pixel)"/><string ParamKey="100" Text="Common"/></stringpack>
<stringpack LangCode="1"><string ParamKey="1024" Text="최소 불량 크기"/></stringpack></pixel>""",
    "SpecTreeNode.xml": """<?xml version="1.0" encoding="UTF-8"?>
<pixel><gpnode><node ID="1" Name="Unit"/></gpnode><pnode><node ID="2" Name="AU"/></pnode><cnode><node ID="20" Name="C-Pad"/></cnode></pixel>""",
    "SpecTreeNodeList.xml": """<?xml version="1.0" encoding="UTF-8"?>
<pixel><system><gpnode ID="1"><pnode ID="2"><cnode ID="20"/></pnode></gpnode></system></pixel>""",
}


def spec(value_r, value_g, master="1.0", enabled="1"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<pixel Version="1.0"><GPNODE ID="1"><PNODE ID="2" NodeCheck="{enabled}"><CNODE ID="20" NodeCheck="1">
<MASTER ParamKey="100" Val="{master}"/>
<INSPECTION ParamKey="1024" ValR="{value_r}" ValG="{value_g}" ValB="0.0"/>
</CNODE></PNODE></GPNODE></pixel>"""


class SpecBaselineTests(unittest.TestCase):
    def build(self, root: Path) -> Path:
        inventory = root / "PxInventory"
        for name, text in DEFS.items():
            (inventory / name).parent.mkdir(parents=True, exist_ok=True)
            (inventory / name).write_text(text)
        models = {
            "MA-00": spec(20, 0), "MB-00": spec(20, 0), "MC-00": spec(20, 5), "MD-00": spec(20, 0),
            "ME-00": spec(35, 0), "MF-00": spec(20, 0, enabled="0"), "MA-00 - 복사본": spec(99, 99), "MAbk": spec(99, 99),
        }
        for model, text in models.items():
            path = inventory / "INSPECT_SPEC" / model / "TOP" / "LIGHT1" / "InspectionSpec.xml"
            path.parent.mkdir(parents=True)
            path.write_text(text)
        default = inventory / "INSPECT_SPEC/DEFAULT_SPEC/Default/TOP/LIGHT1/InspectionSpec.xml"
        default.parent.mkdir(parents=True)
        default.write_text(spec(25, 0))
        return inventory

    def test_production_models_exclude_copies_backups_and_default(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.build(Path(directory))

            self.assertEqual(production_models(inventory / "INSPECT_SPEC"), ["MA-00", "MB-00", "MC-00", "MD-00", "ME-00", "MF-00"])

    def test_baseline_rows_consensus_and_deviations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = self.build(root)

            code = main(["--inventory", f"FM1={inventory}:TOP", "--output-dir", str(root / "out")])

            self.assertEqual(code, 0)
            rows = json.loads((root / "out/FM1-L1.json").read_text())["rows"]
            red = next(row for row in rows if row["key"] == "1024" and row["channel"] == "R")
            self.assertEqual(red["node_name"], "Unit / AU / C-Pad")
            self.assertEqual(red["name_ko"], "최소 불량 크기")
            self.assertEqual((red["models"], red["enabled"], red["active"]), (6, 5, 5))
            self.assertEqual(red["stats"]["mode"], 20.0)
            self.assertAlmostEqual(red["stats"]["mode_share"], 0.8)
            self.assertEqual(red["default"], 25.0)
            green = next(row for row in rows if row["key"] == "1024" and row["channel"] == "G")
            self.assertAlmostEqual(green["inactive_share"], 0.8)
            self.assertEqual(green["active"], 1)
            deviations = json.loads((root / "out/FM1-models.json").read_text())["deviations"]
            self.assertEqual([(d["key"], d["channel"], d["value"]) for d in deviations["ME-00"]], [("1024", "R", 35.0)])
            self.assertNotIn("MF-00", deviations)
            own = json.loads((root / "out/FM1-model/ME-00.json").read_text())
            self.assertEqual(own["lights"]["1"]["1-2-20|INSPECTION|1024|R"], [35.0, True])
            index = json.loads((root / "out/index.json").read_text())
            self.assertEqual(index["visions"][0]["models"], 6)


if __name__ == "__main__":
    unittest.main()
