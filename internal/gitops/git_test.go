package gitops

import (
	"strings"
	"testing"
)

func TestParsePorcelainLines(t *testing.T) {
	// Simulate git status --porcelain output with mixed status codes.
	// " M" = modified but unstaged, "??" = untracked, "M " = staged.
	porcelain := strings.Join([]string{
		" M config/.smw.json",
		" M extensions/wikivisor/SkinSettings.php",
		"?? extensions/wikivisor/skins/chameleon/old.custom.scss",
		"M  extensions/wikivisor/skins/chameleon/custom.scss",
	}, "\n")

	want := []string{
		"config/.smw.json",
		"extensions/wikivisor/SkinSettings.php",
		"extensions/wikivisor/skins/chameleon/old.custom.scss",
		"extensions/wikivisor/skins/chameleon/custom.scss",
	}

	// Use TrimRight (not TrimSpace) to preserve leading spaces in
	// the first line's status code.
	porcelain = strings.TrimRight(porcelain, " \t\n\r")

	var files []string
	for _, line := range strings.Split(porcelain, "\n") {
		if len(line) < 4 {
			continue
		}
		files = append(files, line[3:])
	}

	if len(files) != len(want) {
		t.Fatalf("got %d files, want %d", len(files), len(want))
	}
	for i, f := range files {
		if f != want[i] {
			t.Errorf("file[%d] = %q, want %q", i, f, want[i])
		}
	}
}

func TestParsePorcelainLeadingSpacePreserved(t *testing.T) {
	// Regression test: when the first line starts with " M" (unstaged
	// modification), TrimSpace on the whole output would strip the
	// leading space, causing line[3:] to clip the first path character.
	porcelain := " M config/.smw.json\n?? extensions/newfile\n"

	trimmed := strings.TrimRight(porcelain, " \t\n\r")
	lines := strings.Split(trimmed, "\n")

	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d", len(lines))
	}
	if got := lines[0][3:]; got != "config/.smw.json" {
		t.Errorf("first file = %q, want %q", got, "config/.smw.json")
	}
	if got := lines[1][3:]; got != "extensions/newfile" {
		t.Errorf("second file = %q, want %q", got, "extensions/newfile")
	}
}
