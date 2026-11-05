package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"pxspectool/result"
)

type options struct {
	machine     string
	resultGroup string
	groupDir    string
}

type sample struct {
	Machine            string   `json:"machine"`
	ResultGroup        string   `json:"result_group"`
	Lot                string   `json:"lot"`
	Strip              string   `json:"strip"`
	Side               string   `json:"side"`
	UnitIdx            int      `json:"unit_idx"`
	ImageMergeIdx      int      `json:"img_merge_idx"`
	Features           features `json:"features"`
	ImageRel           string   `json:"image_rel"`
	VRSObserved        bool     `json:"vrs_observed"`
	IVS1               string   `json:"ivs_1"`
	IVS1Values         []string `json:"ivs_1_values"`
	MemberCount        int      `json:"member_count"`
	InspectedAt        string   `json:"inspected_at"`
	MissingFeatureBits uint32   `json:"missing_feature_bits"`
}

type features struct {
	AreaSum    float64 `json:"area_sum"`
	Length     float64 `json:"length"`
	Width      float64 `json:"width"`
	StripX     float64 `json:"strip_x"`
	StripY     float64 `json:"strip_y"`
	ImageX     float64 `json:"image_x"`
	ImageY     float64 `json:"image_y"`
	SRMetal    int     `json:"sr_metal"`
	LightID    int     `json:"light_id"`
	ChannelIdx int     `json:"channel_idx"`
	PCIdx      int     `json:"pc_idx"`
	DefSide    string  `json:"def_side"`
	AreaTotal  float64 `json:"area_total"`
	AreaMax    float64 `json:"area_max"`
	LengthMax  float64 `json:"length_max"`
	WidthMax   float64 `json:"width_max"`
	IsDummy    bool    `json:"is_dummy"`
}

type naturalKey struct {
	machine, group, strip, side string
	unit, merge                 int
}

func main() {
	if err := run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "afvi-export:", err)
		os.Exit(1)
	}
}

func run(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("afvi-export", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var opts options
	fs.StringVar(&opts.machine, "machine", "", "machine name, such as AFVI13")
	fs.StringVar(&opts.resultGroup, "group-rel", "", "group path relative to the machine result root, such as MODEL/LOT/3")
	fs.StringVar(&opts.groupDir, "group-dir", "", "absolute path to one ResultData group directory (read-only)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return errors.New("unexpected positional arguments")
	}
	if err := validateOptions(opts); err != nil {
		return err
	}

	samples, err := exportGroup(opts)
	if err != nil {
		return err
	}
	w := bufio.NewWriter(stdout)
	for _, row := range samples {
		if err := writeJSONLine(w, row); err != nil {
			return err
		}
	}
	return w.Flush()
}

func validateOptions(opts options) error {
	if !validAtom(opts.machine) {
		return errors.New("-machine is required and must be a simple identifier")
	}
	if opts.resultGroup == "" || strings.IndexByte(opts.resultGroup, 92) >= 0 || path.IsAbs(opts.resultGroup) {
		return errors.New("-group-rel must be a relative slash-separated group path")
	}
	cleanGroup := path.Clean(opts.resultGroup)
	if cleanGroup == "." || cleanGroup == ".." || strings.HasPrefix(cleanGroup, "../") || cleanGroup != opts.resultGroup {
		return errors.New("-group-rel must be a normalized path that does not escape the machine result root")
	}
	if len(strings.Split(cleanGroup, "/")) != 3 {
		return errors.New("-group-rel must contain exactly model, LOT, and result-group path segments")
	}
	if !filepath.IsAbs(opts.groupDir) {
		return errors.New("-group-dir must be an absolute directory path")
	}
	info, err := os.Stat(opts.groupDir)
	if err != nil {
		return fmt.Errorf("stat group directory: %w", err)
	}
	if !info.IsDir() {
		return errors.New("-group-dir is not a directory")
	}
	if filepath.Base(filepath.Clean(opts.groupDir)) != path.Base(cleanGroup) {
		return errors.New("-group-dir basename does not match -group-rel")
	}
	return nil
}

func validAtom(s string) bool {
	if s == "" || s == "." || s == ".." {
		return false
	}
	for _, r := range s {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_' || r == '-' || r == '.') {
			return false
		}
	}
	return true
}

func exportGroup(opts options) ([]sample, error) {
	groupRel := path.Clean(opts.resultGroup)
	group, err := readGroup(opts.groupDir, groupRel)
	if err != nil {
		return nil, err
	}
	if len(group.Clusters) == 0 {
		return nil, errors.New("result group contains no parsed merge points")
	}

	rows := make([]sample, 0, len(group.Clusters))
	seen := make(map[naturalKey]struct{}, len(group.Clusters))
	groupParts := strings.Split(groupRel, "/")
	lot := groupParts[1]
	for i := range group.Clusters {
		cluster := &group.Clusters[i]
		if cluster.Strip < 0 || cluster.Strip >= len(group.Strips) {
			return nil, fmt.Errorf("parser returned invalid strip index %d", cluster.Strip)
		}
		strip := group.Strips[cluster.Strip]
		file := strip.Files[cluster.Side]
		if file == nil {
			return nil, fmt.Errorf("parser returned merge point without %s file in strip %s", cluster.Side, strip.Dir)
		}
		if len(cluster.Members) == 0 {
			return nil, fmt.Errorf("parser returned empty merge point %s", group.ID(cluster))
		}
		rep := cluster.Rep()
		ivsValues := make([]string, 0, len(cluster.Members))
		for j := range cluster.Members {
			ivsValues = append(ivsValues, cluster.Point(j).IVS1)
		}

		key := naturalKey{machine: opts.machine, group: groupRel, strip: strip.Dir, side: cluster.Side, unit: rep.Unit, merge: rep.Merge}
		if _, exists := seen[key]; exists {
			return nil, fmt.Errorf("duplicate merge-point key %s", group.ID(cluster))
		}
		seen[key] = struct{}{}

		featureRow, err := clusterFeatures(cluster)
		if err != nil {
			return nil, fmt.Errorf("merge point %s: %w", group.ID(cluster), err)
		}
		rows = append(rows, sample{
			Machine:            opts.machine,
			ResultGroup:        groupRel,
			Lot:                lot,
			Strip:              strip.Dir,
			Side:               cluster.Side,
			UnitIdx:            rep.Unit,
			ImageMergeIdx:      rep.Merge,
			Features:           featureRow,
			ImageRel:           group.ImageRel(cluster, 0),
			VRSObserved:        file.VRS(),
			IVS1:               rep.IVS1,
			IVS1Values:         ivsValues,
			MemberCount:        len(cluster.Members),
			InspectedAt:        inspectionTime(file),
			MissingFeatureBits: rep.Missing,
		})
	}

	sort.Slice(rows, func(i, j int) bool {
		a, b := rows[i], rows[j]
		if a.Strip != b.Strip {
			return a.Strip < b.Strip
		}
		if a.Side != b.Side {
			return a.Side < b.Side
		}
		if a.UnitIdx != b.UnitIdx {
			return a.UnitIdx < b.UnitIdx
		}
		return a.ImageMergeIdx < b.ImageMergeIdx
	})
	return rows, nil
}

func readGroup(dir, groupRel string) (*result.Group, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("read result group: %w", err)
	}

	var bundle []byte
	bundleFound := false
	var parsedStrips []*result.Strip
	for _, entry := range entries {
		if !entry.IsDir() && strings.EqualFold(entry.Name(), "Bundle001.ini") {
			if bundleFound {
				return nil, errors.New("result group has multiple Bundle001.ini files")
			}
			bundleFound = true
			bundle, err = os.ReadFile(filepath.Join(dir, entry.Name()))
			if err != nil {
				return nil, fmt.Errorf("read Bundle001.ini: %w", err)
			}
			continue
		}
		if !entry.IsDir() || !result.StripDir.MatchString(entry.Name()) {
			continue
		}

		stripDir := filepath.Join(dir, entry.Name())
		files, err := os.ReadDir(stripDir)
		if err != nil {
			return nil, fmt.Errorf("read strip %s: %w", entry.Name(), err)
		}
		names := make([]string, 0, len(files))
		selectedCounts := make(map[string]int, 4)
		for _, file := range files {
			if file.IsDir() {
				continue
			}
			if file.Type()&os.ModeSymlink != 0 && result.IsStripXML(file.Name()) {
				return nil, fmt.Errorf("refusing symlinked result XML %s/%s", entry.Name(), file.Name())
			}
			names = append(names, file.Name())
			side := result.SideOfName(file.Name())
			if side != "" && result.IsStripXML(file.Name()) {
				rank := "inspection"
				if result.VariantOfName(file.Name()) == result.VariantIVS {
					rank = "VRS rewrite"
				}
				key := side + "/" + rank
				selectedCounts[key]++
				if selectedCounts[key] > 1 {
					return nil, fmt.Errorf("strip %s contains multiple %s XML files for %s", entry.Name(), rank, side)
				}
			}
		}
		pick := result.PickStripFiles(names)
		stripData := result.StripData{Dir: entry.Name()}
		for _, name := range pick.Names() {
			data, err := os.ReadFile(filepath.Join(stripDir, name))
			if err != nil {
				return nil, fmt.Errorf("read result XML %s/%s: %w", entry.Name(), name, err)
			}
			stripData.Files = append(stripData.Files, result.NamedData{Name: name, Data: data})
		}
		if len(stripData.Files) == 0 {
			continue
		}
		parsed := result.ParseStrip(stripData)
		if len(parsed.Errors) > 0 {
			return nil, fmt.Errorf("parse strip %s: %s", entry.Name(), strings.Join(parsed.Errors, "; "))
		}
		parsedStrips = append(parsedStrips, parsed)
	}
	if !bundleFound {
		return nil, errors.New("result group is missing Bundle001.ini")
	}
	if len(parsedStrips) == 0 {
		return nil, errors.New("result group contains no readable strip XML")
	}

	group := result.NewGroup(groupRel, bundle)
	group.Append(parsedStrips)
	return group, nil
}

func clusterFeatures(c *result.Cluster) (features, error) {
	rep := c.Rep()
	values := []float64{rep.Area, rep.Len, rep.Wid, rep.X, rep.Y, rep.IX, rep.IY}
	row := features{
		AreaSum:    rep.Area,
		Length:     rep.Len,
		Width:      rep.Wid,
		StripX:     rep.X,
		StripY:     rep.Y,
		ImageX:     rep.IX,
		ImageY:     rep.IY,
		SRMetal:    rep.SRMetal,
		LightID:    rep.Light,
		ChannelIdx: rep.Ch,
		PCIdx:      rep.PC,
		DefSide:    rep.DefSide,
		AreaMax:    rep.Area,
		LengthMax:  rep.Len,
		WidthMax:   rep.Wid,
		IsDummy:    rep.Unit < 0,
	}
	for i := 1; i < len(c.Members); i++ {
		p := c.Point(i)
		values = append(values, p.Area, p.Len, p.Wid)
		row.AreaMax = math.Max(row.AreaMax, p.Area)
		row.LengthMax = math.Max(row.LengthMax, p.Len)
		row.WidthMax = math.Max(row.WidthMax, p.Wid)
	}
	for i := 0; i < len(c.Members); i++ {
		row.AreaTotal += c.Point(i).Area
	}
	for _, v := range values {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return features{}, errors.New("non-finite numeric feature")
		}
	}
	return row, nil
}

func inspectionTime(f *result.File) string {
	t := result.FileDate(f)
	if t.IsZero() {
		return ""
	}
	return t.Format(time.RFC3339)
}

func writeJSONLine(w io.Writer, row sample) error {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return enc.Encode(row)
}
