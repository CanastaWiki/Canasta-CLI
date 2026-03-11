package gitops

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
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

		runGit(t, repoPath, "add", "-A")
		runGit(t, repoPath, "commit", "-m", "repair submodule")

		lsTree := runGit(t, repoPath, "ls-tree", "HEAD", "extensions/Foo")
		if !strings.HasPrefix(lsTree, "160000") {
			t.Fatalf("expected repaired submodule to be gitlink (160000), got: %q", lsTree)
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
		repoPath, brokenGitFile := setupBrokenSubmoduleRepo(t, true)

		repaired, err := repairBrokenSubmodules(repoPath)
		if err != nil {
			t.Fatalf("repairBrokenSubmodules returned error: %v", err)
		}
		if repaired != 1 {
			t.Fatalf("expected 1 repaired submodule, got %d", repaired)
		}

		content, err := os.ReadFile(brokenGitFile)
		if err != nil {
			t.Fatalf("reading post-repair .git file: %v", err)
		}
		if strings.Contains(string(content), "broken-gitlink-marker") {
			t.Fatalf("expected stale gitlink file content to be removed, got: %q", string(content))
		}
	})
}

func setupBrokenSubmoduleRepo(t *testing.T, withBrokenGitFile bool) (string, string) {
	t.Helper()

	repoPath := t.TempDir()
	remotePath := createGitRepoWithCommit(t, t.TempDir(), "submodule")
	modulePath := filepath.Join(repoPath, "extensions", "Foo")

	initGitRepo(t, repoPath)
	if err := os.MkdirAll(modulePath, 0755); err != nil {
		t.Fatalf("creating extension dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(modulePath, "README.md"), []byte("broken tree\n"), 0644); err != nil {
		t.Fatalf("writing extension file: %v", err)
	}
	writeGitmodules(t, repoPath, "extensions/Foo", remotePath)

	brokenGitFile := filepath.Join(modulePath, ".git")
	if withBrokenGitFile {
		if err := os.WriteFile(brokenGitFile, []byte("broken-gitlink-marker\n"), 0644); err != nil {
			t.Fatalf("writing broken .git file: %v", err)
		}
	}

	runGit(t, repoPath, "add", "-A")
	if withBrokenGitFile {
		runGit(t, repoPath, "commit", "-m", "broken submodule with stale gitlink file")
	} else {
		runGit(t, repoPath, "commit", "-m", "broken submodule committed as tree")
	}

	return repoPath, brokenGitFile
}

func writeGitmodules(t *testing.T, repoPath, submodulePath, submoduleURL string) {
	t.Helper()
	content := fmt.Sprintf(`[submodule "%s"]
	path = %s
	url = %s
`, submodulePath, submodulePath, submoduleURL)
	if err := os.WriteFile(filepath.Join(repoPath, ".gitmodules"), []byte(content), 0644); err != nil {
		t.Fatalf("writing .gitmodules: %v", err)
	}
}

func createGitRepoWithCommit(t *testing.T, repoPath, fileStem string) string {
	t.Helper()
	initGitRepo(t, repoPath)
	file := fileStem + ".txt"
	if err := os.WriteFile(filepath.Join(repoPath, file), []byte("content\n"), 0644); err != nil {
		t.Fatalf("writing seed file: %v", err)
	}
	runGit(t, repoPath, "add", file)
	runGit(t, repoPath, "commit", "-m", "initial commit")
	return repoPath
}

func initGitRepo(t *testing.T, repoPath string) {
	t.Helper()
	runGit(t, repoPath, "init", "-b", "main")
	runGit(t, repoPath, "config", "user.email", "test@example.com")
	runGit(t, repoPath, "config", "user.name", "Test User")
	runGit(t, repoPath, "config", "protocol.file.allow", "always")
}

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()

	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")

	var out bytes.Buffer
	var stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		t.Fatalf("git %s failed: %v\nstdout:\n%s\nstderr:\n%s", strings.Join(args, " "), err, out.String(), stderr.String())
	}
	return strings.TrimSpace(out.String())
}
