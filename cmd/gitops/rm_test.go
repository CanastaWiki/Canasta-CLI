package gitops

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

func TestRunRm_DeletedFile(t *testing.T) {
	// Set up a git repo with a committed file.
	repoDir := t.TempDir()
	if _, err := execute.Run(repoDir, "git", "init", "-b", "main"); err != nil {
		t.Fatalf("git init: %v", err)
	}
	if _, err := execute.Run(repoDir, "git", "config", "user.email", "test@test.com"); err != nil {
		t.Fatalf("git config email: %v", err)
	}
	if _, err := execute.Run(repoDir, "git", "config", "user.name", "Test"); err != nil {
		t.Fatalf("git config name: %v", err)
	}

	testFile := filepath.Join(repoDir, "tracked.txt")
	if err := os.WriteFile(testFile, []byte("hello"), 0644); err != nil {
		t.Fatalf("writing file: %v", err)
	}
	if _, err := execute.Run(repoDir, "git", "add", "tracked.txt"); err != nil {
		t.Fatalf("git add: %v", err)
	}
	if _, err := execute.Run(repoDir, "git", "commit", "-m", "add file"); err != nil {
		t.Fatalf("git commit: %v", err)
	}

	// Delete the file from disk, then run rm — it should succeed.
	if err := os.Remove(testFile); err != nil {
		t.Fatalf("removing file: %v", err)
	}

	if err := runRm(repoDir, []string{filepath.Join(repoDir, "tracked.txt")}); err != nil {
		t.Fatalf("runRm on deleted file: %v", err)
	}
}
