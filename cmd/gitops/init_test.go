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
		repoPath := setupBrokenSubmoduleRepo(t, false)

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
		repoPath := setupBrokenSubmoduleRepo(t, true)

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

func TestConvertToSubmodulesStandaloneRepoConverted(t *testing.T) {
	t.Setenv("GIT_ALLOW_PROTOCOL", "file")

	instancePath := t.TempDir()
	dirName := "extensions"

	if err := os.MkdirAll(filepath.Join(instancePath, dirName), 0755); err != nil {
		t.Fatalf("creating extensions dir: %v", err)
	}
	initGitRepo(t, instancePath)

	remote := createGitRepoWithCommit(t, t.TempDir(), "module")
	subDir := filepath.Join(instancePath, dirName, "MyModule")
	if err := os.MkdirAll(subDir, 0755); err != nil {
		t.Fatalf("creating subdir: %v", err)
	}
	initGitRepo(t, subDir)
	runGit(t, subDir, "remote", "add", "origin", remote)
	if err := os.WriteFile(filepath.Join(subDir, "plugin.php"), []byte("<?php\n"), 0644); err != nil {
		t.Fatalf("writing plugin file: %v", err)
	}
	runGit(t, subDir, "add", "plugin.php")
	runGit(t, subDir, "commit", "-m", "add plugin")

	err := convertToSubmodules(instancePath, dirName)
	if err != nil {
		t.Fatalf("convertToSubmodules returned error: %v", err)
	}

	gitPath := filepath.Join(subDir, ".git")
	info, err := os.Stat(gitPath)
	if err != nil {
		t.Errorf("expected .git to exist after conversion, got error: %v", err)
	}
	if !info.IsDir() {
		moduleContents, _ := os.ReadFile(gitPath)
		if !strings.Contains(string(moduleContents), "gitdir") {
			t.Errorf("expected .git to be a gitlink file after conversion")
		}
	}
}

func TestConvertToSubmodulesCommitPreserved(t *testing.T) {
	t.Setenv("GIT_ALLOW_PROTOCOL", "file")

	instancePath := t.TempDir()
	dirName := "extensions"

	if err := os.MkdirAll(filepath.Join(instancePath, dirName), 0755); err != nil {
		t.Fatalf("creating extensions dir: %v", err)
	}
	initGitRepo(t, instancePath)

	remotePath := t.TempDir()
	initGitRepo(t, remotePath)
	runGit(t, remotePath, "config", "receive.denyCurrentBranch", "ignore")

	subDir := filepath.Join(instancePath, dirName, "Pinned")
	if err := os.MkdirAll(subDir, 0755); err != nil {
		t.Fatalf("creating subdir: %v", err)
	}
	initGitRepo(t, subDir)
	runGit(t, subDir, "remote", "add", "origin", remotePath)

	if err := os.WriteFile(filepath.Join(subDir, "v1.txt"), []byte("version 1\n"), 0644); err != nil {
		t.Fatalf("writing v1: %v", err)
	}
	runGit(t, subDir, "add", "v1.txt")
	runGit(t, subDir, "commit", "-m", "version 1")

	if err := os.WriteFile(filepath.Join(subDir, "v2.txt"), []byte("version 2\n"), 0644); err != nil {
		t.Fatalf("writing v2: %v", err)
	}
	runGit(t, subDir, "add", "v2.txt")
	runGit(t, subDir, "commit", "-m", "version 2")
	headBeforeConvert := runGit(t, subDir, "rev-parse", "HEAD")

	runGit(t, subDir, "push", "-u", "origin", "main")

	err := convertToSubmodules(instancePath, dirName)
	if err != nil {
		t.Fatalf("convertToSubmodules returned error: %v", err)
	}

	gitPath := filepath.Join(subDir, ".git")
	info, err := os.Stat(gitPath)
	if err != nil {
		t.Fatalf("expected .git to exist after conversion, got error: %v", err)
	}

	if !info.IsDir() {
		gitContent, _ := os.ReadFile(gitPath)
		if !strings.Contains(string(gitContent), "gitdir") {
			t.Fatalf("expected .git to be a gitlink file after conversion")
		}
	}

	currentHead := runGit(t, subDir, "rev-parse", "HEAD")
	if currentHead != headBeforeConvert {
		t.Errorf("expected commit to be preserved at %s, but got %s", headBeforeConvert[:8], currentHead[:8])
	}
}

func TestConvertToSubmodulesNoRemoteSkipped(t *testing.T) {
	instancePath := t.TempDir()
	dirName := "extensions"

	if err := os.MkdirAll(filepath.Join(instancePath, dirName), 0755); err != nil {
		t.Fatalf("creating extensions dir: %v", err)
	}
	initGitRepo(t, instancePath)

	subDir := filepath.Join(instancePath, dirName, "NoRemote")
	if err := os.MkdirAll(subDir, 0755); err != nil {
		t.Fatalf("creating subdir: %v", err)
	}
	initGitRepo(t, subDir)
	if err := os.WriteFile(filepath.Join(subDir, "file.txt"), []byte("content\n"), 0644); err != nil {
		t.Fatalf("writing file: %v", err)
	}
	runGit(t, subDir, "add", "file.txt")
	runGit(t, subDir, "commit", "-m", "initial")

	err := convertToSubmodules(instancePath, dirName)
	if err != nil {
		t.Fatalf("convertToSubmodules returned error: %v", err)
	}

	_, err = os.Stat(subDir)
	if os.IsNotExist(err) {
		t.Errorf("expected subdirectory to be skipped and remain, but it was removed")
	}

	gitDir := filepath.Join(subDir, ".git")
	if _, err := os.Stat(gitDir); os.IsNotExist(err) {
		t.Errorf("expected .git directory to be preserved")
	}
}

func TestConvertToSubmodulesNonGitDirectorySkipped(t *testing.T) {
	instancePath := t.TempDir()
	dirName := "extensions"

	if err := os.MkdirAll(filepath.Join(instancePath, dirName), 0755); err != nil {
		t.Fatalf("creating extensions dir: %v", err)
	}
	initGitRepo(t, instancePath)

	subDir := filepath.Join(instancePath, dirName, "NotGit")
	if err := os.MkdirAll(subDir, 0755); err != nil {
		t.Fatalf("creating subdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(subDir, "readme.txt"), []byte("not a git repo\n"), 0644); err != nil {
		t.Fatalf("writing file: %v", err)
	}

	err := convertToSubmodules(instancePath, dirName)
	if err != nil {
		t.Fatalf("convertToSubmodules returned error: %v", err)
	}

	_, err = os.Stat(subDir)
	if os.IsNotExist(err) {
		t.Errorf("expected non-git directory to be skipped and remain")
	}

	gitDir := filepath.Join(subDir, ".git")
	if _, err := os.Stat(gitDir); err == nil {
		t.Errorf("expected .git not to exist in non-git directory")
	}
}

func TestConvertToSubmodulesGitlinkFileSkipped(t *testing.T) {
	instancePath := t.TempDir()
	dirName := "extensions"

	if err := os.MkdirAll(filepath.Join(instancePath, dirName), 0755); err != nil {
		t.Fatalf("creating extensions dir: %v", err)
	}
	initGitRepo(t, instancePath)

	subDir := filepath.Join(instancePath, dirName, "GitLink")
	if err := os.MkdirAll(subDir, 0755); err != nil {
		t.Fatalf("creating subdir: %v", err)
	}
	gitFile := filepath.Join(subDir, ".git")
	if err := os.WriteFile(gitFile, []byte("gitdir: /some/path\n"), 0644); err != nil {
		t.Fatalf("writing .git file: %v", err)
	}
	if err := os.WriteFile(filepath.Join(subDir, "marker.txt"), []byte("already a submodule\n"), 0644); err != nil {
		t.Fatalf("writing marker: %v", err)
	}

	err := convertToSubmodules(instancePath, dirName)
	if err != nil {
		t.Fatalf("convertToSubmodules returned error: %v", err)
	}

	_, err = os.Stat(subDir)
	if os.IsNotExist(err) {
		t.Errorf("expected subdirectory with .git file to be skipped and remain")
	}

	content, _ := os.ReadFile(gitFile)
	expected := string(content)
	if !strings.Contains(expected, "gitdir") {
		t.Errorf("expected .git file to be preserved")
	}
}

func TestConvertToSubmodulesEmptyDirectory(t *testing.T) {
	instancePath := t.TempDir()
	dirName := "extensions"

	if err := os.MkdirAll(filepath.Join(instancePath, dirName), 0755); err != nil {
		t.Fatalf("creating extensions dir: %v", err)
	}
	initGitRepo(t, instancePath)

	err := convertToSubmodules(instancePath, dirName)
	if err != nil {
		t.Fatalf("convertToSubmodules should handle empty directory without error, got: %v", err)
	}

	_, err = os.Stat(filepath.Join(instancePath, dirName))
	if os.IsNotExist(err) {
		t.Errorf("expected extensions directory to still exist")
	}
}

func TestConvertToSubmodulesMissingDirectory(t *testing.T) {
	instancePath := t.TempDir()
	dirName := "nonexistent"

	err := convertToSubmodules(instancePath, dirName)
	if err != nil {
		t.Fatalf("convertToSubmodules should handle missing directory without error, got: %v", err)
	}
}
