package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"pxspectool/result"
)

func demoGroup(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	if err := result.WriteDemo(root); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(root, "M1", "TEST1", "1")
}

func demoOptions(groupDir string) options {
	return options{machine: "AFVI13", resultGroup: "M1/TEST1/1", groupDir: groupDir}
}

func sampleAt(t *testing.T, rows []sample, strip, side string, unit, merge int) sample {
	t.Helper()
	for _, row := range rows {
		if row.Strip == strip && row.Side == side && row.UnitIdx == unit && row.ImageMergeIdx == merge {
			return row
		}
	}
	t.Fatalf("sample not found: strip=%s side=%s unit=%d merge=%d", strip, side, unit, merge)
	return sample{}
}

func TestExportKeepsVRSCodeAndParserImageMapping(t *testing.T) {
	groupDir := demoGroup(t)
	rows, err := exportGroup(demoOptions(groupDir))
	if err != nil {
		t.Fatal(err)
	}

	numeric := sampleAt(t, rows, "0001", "FC", 0, 1)
	if !numeric.VRSObserved || numeric.IVS1 != "187" {
		t.Fatalf("numeric code was not kept as a VRS label: %+v", numeric)
	}
	if numeric.ImageRel != "0001/Unit/U0/FC/p1.bmp" {
		t.Fatalf("image path = %q", numeric.ImageRel)
	}
	if numeric.MemberCount < 1 {
		t.Fatalf("unexpected member count: %+v", numeric)
	}
	if numeric.Lot != "TEST1" {
		t.Fatalf("LOT should be the second path segment, got %q", numeric.Lot)
	}
	if _, err := time.Parse(time.RFC3339, numeric.InspectedAt); err != nil {
		t.Fatalf("inspection time should preserve a timezone: %q (%v)", numeric.InspectedAt, err)
	}

	alpha := sampleAt(t, rows, "0001", "FC", 1, 1)
	if !alpha.VRSObserved || alpha.IVS1 != "OT15" || len(alpha.IVS1Values) == 0 || alpha.IVS1Values[0] != "OT15" {
		t.Fatalf("letter code was not preserved: %+v", alpha)
	}
	if alpha.ImageRel != "0001/Unit/U1/FC/p1.bmp" {
		t.Fatalf("image path = %q", alpha.ImageRel)
	}
	if alpha.MemberCount < 1 || alpha.Features.AreaTotal <= 0 {
		t.Fatalf("merge-point features were not exported: %+v", alpha)
	}
}

func TestRawInspectionXMLDoesNotCountAsObservedVRS(t *testing.T) {
	groupDir := demoGroup(t)
	rows, err := exportGroup(demoOptions(groupDir))
	if err != nil {
		t.Fatal(err)
	}
	row := sampleAt(t, rows, "0001", "BC", 0, 1)
	if row.VRSObserved {
		t.Fatalf("raw inspection XML was misclassified as VRS: %+v", row)
	}
	if row.ImageRel != "0001/Unit/U0/BC/p1.bmp" {
		t.Fatalf("image path = %q", row.ImageRel)
	}
}

func TestVRSRewriteWithZeroCodeRemainsObserved(t *testing.T) {
	groupDir := demoGroup(t)
	rows, err := exportGroup(demoOptions(groupDir))
	if err != nil {
		t.Fatal(err)
	}
	row := sampleAt(t, rows, "0002", "BC", 2, 1)
	if !row.VRSObserved || row.IVS1 != "0" {
		t.Fatalf("zero code was not preserved as an observed VRS label: %+v", row)
	}
}

func TestExportRejectsMultipleXMLFilesForTheSameSideAndRank(t *testing.T) {
	groupDir := demoGroup(t)
	stripDir := filepath.Join(groupDir, "0001")
	source := filepath.Join(stripDir, "S0001_FC.xml")
	data, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stripDir, "S9999_FC.xml"), data, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := exportGroup(demoOptions(groupDir)); err == nil || !strings.Contains(err.Error(), "multiple inspection XML files") {
		t.Fatalf("expected duplicate inspection XML error, got %v", err)
	}
}

func TestExportReadsResultInputsWithoutModifyingThem(t *testing.T) {
	groupDir := demoGroup(t)
	before := hashResultInputs(t, groupDir)
	if _, err := exportGroup(demoOptions(groupDir)); err != nil {
		t.Fatal(err)
	}
	after := hashResultInputs(t, groupDir)
	if !reflect.DeepEqual(before, after) {
		t.Fatal("export changed a ResultData INI or XML input")
	}
}

func TestRunWritesOneJSONRecordPerMergePoint(t *testing.T) {
	groupDir := demoGroup(t)
	rows, err := exportGroup(demoOptions(groupDir))
	if err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	err = run([]string{"-machine", "AFVI13", "-group-rel", "M1/TEST1/1", "-group-dir", groupDir}, &stdout, &stderr)
	if err != nil {
		t.Fatalf("run failed: %v (%s)", err, stderr.String())
	}
	if !strings.HasSuffix(stdout.String(), "\n") {
		t.Fatal("JSONL output must end with a newline")
	}
	lines := strings.Split(strings.TrimSpace(stdout.String()), "\n")
	for _, line := range lines {
		var row sample
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatal(err)
		}
	}
	if len(lines) != len(rows) {
		t.Fatalf("JSONL rows=%d, parsed merge points=%d", len(lines), len(rows))
	}
	if !strings.Contains(stdout.String(), "\"ivs_1_values\"") || !strings.Contains(stdout.String(), "\"missing_feature_bits\"") {
		t.Fatal("JSONL is missing label audit or feature completeness fields")
	}
}

func TestGroupPathRejectsTraversal(t *testing.T) {
	groupDir := demoGroup(t)
	for _, groupRel := range []string{"../1", "M1/../../1", "M1/../1", "M1//TEST1/1", "M1/TEST1/1/", "/absolute/1", "M1\\TEST1\\1", "M1/TEST1", "M1/TEST1/1/extra"} {
		opts := demoOptions(groupDir)
		opts.resultGroup = groupRel
		if err := validateOptions(opts); err == nil {
			t.Errorf("accepted unsafe group path %q", groupRel)
		}
	}
}

func hashResultInputs(t *testing.T, root string) map[string]string {
	t.Helper()
	hashes := map[string]string{}
	err := filepath.WalkDir(root, func(name string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		ext := strings.ToLower(filepath.Ext(name))
		if ext != ".xml" && ext != ".ini" {
			return nil
		}
		data, err := os.ReadFile(name)
		if err != nil {
			return err
		}
		hashes[filepath.Base(name)+"/"+filepath.Dir(name)] = fmt.Sprintf("%x", sha256.Sum256(data))
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return hashes
}
