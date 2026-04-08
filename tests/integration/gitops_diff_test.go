//go:build integration

package integration

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestGitops_Diff creates an instance, initializes gitops with a local bare
// repo, then tests that "canasta gitops diff" correctly reports both local
// uncommitted changes and remote changes.
func TestGitops_Diff(t *testing.T) {
	inst := createTestInstance(t, "inttest-gitdiff")

	// Create a bare git repo to use as the remote
	bareRepo := t.TempDir()
	if out, err := exec.Command("git", "init", "--bare", "-b", "main", bareRepo).CombinedOutput(); err != nil {
		t.Fatalf("git init --bare failed: %v\n%s", err, out)
	}

	// Create the instance
	out, err := inst.run(t, "create",
		"-i", inst.ID,
		"-w", "main",
		"-n", "localhost",
		"-p", inst.WorkDir,
		"-e", inst.EnvFile,
	)
	if err != nil {
		t.Fatalf("canasta create failed: %v\n%s", err, out)
	}

	// Wait for the wiki to be ready
	waitForWiki(t, inst.HTTPPort, 5*time.Minute)

	// Initialize gitops with the local bare repo as the remote
	keyFile := filepath.Join(inst.WorkDir, "gitops-key")
	installDir := filepath.Join(inst.WorkDir, inst.ID)
	out, err = inst.run(t, "gitops", "init",
		"-i", inst.ID,
		"-n", "testhost",
		"--repo", bareRepo,
		"--key", keyFile,
	)
	if err != nil {
		t.Fatalf("gitops init failed: %v\n%s", err, out)
	}

	// Verify clean state — diff should report no changes
	out, err = inst.run(t, "gitops", "diff", "-i", inst.ID)
	if err != nil {
		t.Fatalf("gitops diff (clean state) failed: %v\n%s", err, out)
	}
	if !strings.Contains(out, "No changes") && !strings.Contains(out, "in sync") {
		t.Logf("Note: initial diff output was not 'no changes': %s", out)
	}

	// Make a local uncommitted change to a tracked file
	settingsFile := filepath.Join(installDir, "config", "settings", "global", "DiffTest.php")
	if err := os.WriteFile(settingsFile, []byte("<?php\n// diff local test\n"), 0644); err != nil {
		t.Fatalf("failed to write local test file: %v", err)
	}
	// Stage the file so it shows as an uncommitted change
	gitAdd := exec.Command("git", "-C", installDir, "add", "config/settings/global/DiffTest.php")
	if addOut, err := gitAdd.CombinedOutput(); err != nil {
		t.Fatalf("git add failed: %v\n%s", err, addOut)
	}

	// Verify diff detects the local change
	out, err = inst.run(t, "gitops", "diff", "-i", inst.ID)
	if err != nil {
		t.Fatalf("gitops diff (local change) failed: %v\n%s", err, out)
	}
	if !strings.Contains(out, "DiffTest.php") {
		t.Errorf("gitops diff should mention DiffTest.php for local change, got:\n%s", out)
	}
	if !strings.Contains(out, "Uncommitted") && !strings.Contains(out, "uncommitted") {
		t.Errorf("gitops diff should report uncommitted changes, got:\n%s", out)
	}

	// Commit the local change so we can test committed local vs remote
	gitCommit := exec.Command("git", "-C", installDir, "commit", "-m", "Add DiffTest.php")
	gitCommit.Env = append(os.Environ(),
		"GIT_AUTHOR_NAME=Test",
		"GIT_AUTHOR_EMAIL=test@test.com",
		"GIT_COMMITTER_NAME=Test",
		"GIT_COMMITTER_EMAIL=test@test.com",
	)
	if commitOut, err := gitCommit.CombinedOutput(); err != nil {
		t.Fatalf("git commit failed: %v\n%s", err, commitOut)
	}

	// Now make a remote change: clone bare repo, add a file, push
	cloneDir := t.TempDir()
	if cloneOut, err := exec.Command("git", "clone", bareRepo, cloneDir).CombinedOutput(); err != nil {
		t.Fatalf("git clone failed: %v\n%s", err, cloneOut)
	}

	remoteSettingsDir := filepath.Join(cloneDir, "config", "settings", "global")
	if err := os.MkdirAll(remoteSettingsDir, 0755); err != nil {
		t.Fatalf("failed to create settings dir in clone: %v", err)
	}
	remoteFile := filepath.Join(remoteSettingsDir, "RemoteDiffTest.php")
	if err := os.WriteFile(remoteFile, []byte("<?php\n// remote diff test\n"), 0644); err != nil {
		t.Fatalf("failed to write remote test file: %v", err)
	}

	gitAddRemote := exec.Command("git", "-C", cloneDir, "add", "config/settings/global/RemoteDiffTest.php")
	if addOut, err := gitAddRemote.CombinedOutput(); err != nil {
		t.Fatalf("git add in clone failed: %v\n%s", err, addOut)
	}
	gitCommitRemote := exec.Command("git", "-C", cloneDir, "commit", "-m", "Add RemoteDiffTest.php")
	gitCommitRemote.Env = append(os.Environ(),
		"GIT_AUTHOR_NAME=Test",
		"GIT_AUTHOR_EMAIL=test@test.com",
		"GIT_COMMITTER_NAME=Test",
		"GIT_COMMITTER_EMAIL=test@test.com",
	)
	if commitOut, err := gitCommitRemote.CombinedOutput(); err != nil {
		t.Fatalf("git commit in clone failed: %v\n%s", err, commitOut)
	}
	gitPushRemote := exec.Command("git", "-C", cloneDir, "push", "origin", "main")
	if pushOut, err := gitPushRemote.CombinedOutput(); err != nil {
		t.Fatalf("git push from clone failed: %v\n%s", err, pushOut)
	}

	// Verify diff detects both local and remote changes
	out, err = inst.run(t, "gitops", "diff", "-i", inst.ID)
	if err != nil {
		t.Fatalf("gitops diff (local + remote) failed: %v\n%s", err, out)
	}
	if !strings.Contains(out, "DiffTest.php") {
		t.Errorf("gitops diff should mention DiffTest.php as a local change, got:\n%s", out)
	}
	if !strings.Contains(out, "RemoteDiffTest.php") {
		t.Errorf("gitops diff should mention RemoteDiffTest.php as a remote change, got:\n%s", out)
	}
	t.Logf("gitops diff output:\n%s", out)
}
