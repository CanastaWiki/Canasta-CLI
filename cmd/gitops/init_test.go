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

func TestRepairBrokenSubmodules(t *testing.T) {
	t.Setenv("GIT_ALLOW_PROTOCOL", "file")

	t.Run("broken submodule repaired", func(t *testing.T) {
		repoPath, _ := setupBrokenSubmoduleRepo(t, false)

		repaired, err := repairBrokenSubmodules(repoPath)
		if err != nil {
			t.Fatalf("repairBrokenSubmodules returned error: %v", err)
		}
		if repaired != 1 {
			t.Fatalf("expected 1 repaired submodule, got %d", repaired)
		}

		// index already has 160000 after submodule add, no commit needed
		staged := runGit(t, repoPath, "ls-files", "--stage", "extensions/Foo")
		if !strings.HasPrefix(staged, "160000") {
			t.Fatalf("expected staged submodule to be gitlink (160000), got: %q", staged)
		}
	})

	t.Run("healthy submodule unchanged", func(t *testing.T) {
		repoPath := t.TempDir()
		remotePath := createGitRepoWithCommit(t, t.TempDir(), "submodule")

		initGitRepo(t, repoPath)
		runGit(t, repoPath, "submodule", "add", remotePath, "extensions/Foo")
		runGit(t, repoPath, "commit", "-am", "add healthy submodule")

		repaired, err := repairBrokenSubmodules(repoPath)
		if err != nil {
			t.Fatalf("repairBrokenSubmodules returned error: %v", err)
		}
		if repaired != 0 {
			t.Fatalf("expected 0 repaired submodules, got %d", repaired)
		}
	})

	t.Run("no gitmodules file", func(t *testing.T) {
		repoPath := t.TempDir()
		repaired, err := repairBrokenSubmodules(repoPath)
		if err != nil {
			t.Fatalf("repairBrokenSubmodules returned error: %v", err)
		}
		if repaired != 0 {
			t.Fatalf("expected 0 repaired submodules, got %d", repaired)
		}
	})

	t.Run("empty gitmodules", func(t *testing.T) {
		repoPath := t.TempDir()
		if err := os.WriteFile(filepath.Join(repoPath, ".gitmodules"), []byte(""), 0644); err != nil {
			t.Fatalf("writing empty .gitmodules: %v", err)
		}

		repaired, err := repairBrokenSubmodules(repoPath)
		if err != nil {
			t.Fatalf("repairBrokenSubmodules returned error: %v", err)
		}
		if repaired != 0 {
			t.Fatalf("expected 0 repaired submodules, got %d", repaired)
		}
	})

	t.Run("broken gitlink file removed", func(t *testing.T) {
		repoPath, _ := setupBrokenSubmoduleRepo(t, true)

		repaired, err := repairBrokenSubmodules(repoPath)
		if err != nil {
			t.Fatalf("repairBrokenSubmodules returned error: %v", err)
		}
		if repaired != 1 {
			t.Fatalf("expected 1 repaired submodule, got %d", repaired)
		}

		staged := runGit(t, repoPath, "ls-files", "--stage", "extensions/Foo")
		if !strings.HasPrefix(staged, "160000") {
			t.Fatalf("expected repaired submodule to be gitlink (160000), got: %q", staged)
		}
	})
}
