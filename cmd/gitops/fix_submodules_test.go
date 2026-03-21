package gitops

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunRepairSubmodules_ConvertsStandaloneExtension(t *testing.T) {
	t.Setenv("GIT_ALLOW_PROTOCOL", "file")

	// Set up a gitops repo with an initial commit.
	repoPath := t.TempDir()
	initGitRepo(t, repoPath)
	os.WriteFile(filepath.Join(repoPath, ".gitignore"), []byte(".env\n"), 0644)
	runGit(t, repoPath, "add", "-A")
	runGit(t, repoPath, "commit", "-m", "initial")

	// Create a standalone extension git repo inside extensions/.
	extRemote := createGitRepoWithCommit(t, t.TempDir(), "wanda")
	extPath := filepath.Join(repoPath, "extensions", "Wanda")
	runGit(t, repoPath, "clone", extRemote, extPath)
	commitHash := runGit(t, extPath, "rev-parse", "HEAD")

	// Run fix-submodules.
	if err := runRepairSubmodules(repoPath); err != nil {
		t.Fatalf("runRepairSubmodules returned error: %v", err)
	}

	// Verify it was converted to a submodule.
	staged := runGit(t, repoPath, "ls-files", "--stage", "extensions/Wanda")
	if !strings.HasPrefix(staged, "160000") {
		t.Fatalf("expected submodule gitlink (160000), got: %q", staged)
	}

	// Verify the submodule is pinned to the same commit.
	subCommit := runGit(t, extPath, "rev-parse", "HEAD")
	if subCommit != commitHash {
		t.Errorf("expected submodule at %s, got %s", commitHash, subCommit)
	}
}

func TestRunRepairSubmodules_NoGitRepo(t *testing.T) {
	repoPath := t.TempDir()

	// No .git directory — should error.
	err := runRepairSubmodules(repoPath)
	if err == nil {
		t.Fatal("expected error for non-git directory")
	}
	if !strings.Contains(err.Error(), "not a git repository") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestRunRepairSubmodules_NoExtensions(t *testing.T) {
	// A gitops repo with no extensions — should be a no-op.
	repoPath := t.TempDir()
	initGitRepo(t, repoPath)
	os.WriteFile(filepath.Join(repoPath, ".gitignore"), []byte(".env\n"), 0644)
	runGit(t, repoPath, "add", "-A")
	runGit(t, repoPath, "commit", "-m", "initial")

	if err := runRepairSubmodules(repoPath); err != nil {
		t.Fatalf("runRepairSubmodules returned error: %v", err)
	}
}
