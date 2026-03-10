package gitops

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEnsureGitignoreEntries(t *testing.T) {
	tests := []struct {
		name        string
		initial     string
		wantAdded   bool
		wantPattern string
	}{
		{
			name:        "missing entry gets added",
			initial:     "# existing\n.env\nimages/\n",
			wantAdded:   true,
			wantPattern: "config/backup/",
		},
		{
			name:        "entry already present",
			initial:     ".env\nconfig/backup/\nimages/\n",
			wantAdded:   false,
			wantPattern: "config/backup/",
		},
		{
			name:        "no trailing newline",
			initial:     ".env\nimages/",
			wantAdded:   true,
			wantPattern: "config/backup/",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmpDir := t.TempDir()
			giPath := filepath.Join(tmpDir, ".gitignore")
			if err := os.WriteFile(giPath, []byte(tt.initial), 0644); err != nil {
				t.Fatal(err)
			}

			if err := ensureGitignoreEntries(tmpDir); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			got, err := os.ReadFile(giPath)
			if err != nil {
				t.Fatal(err)
			}
			content := string(got)

			if tt.wantAdded {
				if !strings.Contains(content, tt.wantPattern) {
					t.Errorf("expected .gitignore to contain %q, got:\n%s", tt.wantPattern, content)
				}
			} else {
				// Count occurrences — should appear exactly once.
				count := strings.Count(content, tt.wantPattern)
				if count != 1 {
					t.Errorf("expected exactly 1 occurrence of %q, got %d in:\n%s", tt.wantPattern, count, content)
				}
			}
		})
	}
}

func TestEnsureGitignoreEntriesNoFile(t *testing.T) {
	tmpDir := t.TempDir()
	// No .gitignore file — should be a no-op, not an error.
	if err := ensureGitignoreEntries(tmpDir); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
