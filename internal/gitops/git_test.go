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
