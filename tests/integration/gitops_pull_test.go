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

// TestGitops_Pull creates an instance, initializes gitops with a local bare
// repo, pushes the initial state, then makes a change via a clone of the bare
// repo and verifies that "canasta gitops pull" brings the change into the
// instance directory.
func TestGitops_Pull(t *testing.T) {
	inst := createTestInstance(t, "inttest-gitpull")

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

	// Verify the initial commit was pushed to the bare repo
	logCmd := exec.Command("git", "--git-dir", bareRepo, "log", "--oneline", "-1")
	logOut, err := logCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git log on bare repo failed: %v\n%s", err, logOut)
	}
	if !strings.Contains(string(logOut), "Initial gitops") {
		t.Fatalf("expected initial commit in bare repo, got: %s", string(logOut))
	}

	// Clone the bare repo to a temp dir and make a change
	cloneDir := t.TempDir()
	if out, err := exec.Command("git", "clone", bareRepo, cloneDir).CombinedOutput(); err != nil {
		t.Fatalf("git clone failed: %v\n%s", err, out)
	}

	// Create a new settings file in the clone
	settingsDir := filepath.Join(cloneDir, "config", "settings", "global")
	if err := os.MkdirAll(settingsDir, 0755); err != nil {
		t.Fatalf("failed to create settings dir in clone: %v", err)
	}
	testFile := filepath.Join(settingsDir, "PullTest.php")
	if err := os.WriteFile(testFile, []byte("<?php\n// pull integration test\n"), 0644); err != nil {
		t.Fatalf("failed to write test file in clone: %v", err)
	}

	// Commit and push the change from the clone
	gitAdd := exec.Command("git", "-C", cloneDir, "add", "config/settings/global/PullTest.php")
	if out, err := gitAdd.CombinedOutput(); err != nil {
		t.Fatalf("git add in clone failed: %v\n%s", err, out)
	}
	gitCommit := exec.Command("git", "-C", cloneDir, "commit", "-m", "Add PullTest.php")
	gitCommit.Env = append(os.Environ(),
		"GIT_AUTHOR_NAME=Test",
		"GIT_AUTHOR_EMAIL=test@test.com",
		"GIT_COMMITTER_NAME=Test",
		"GIT_COMMITTER_EMAIL=test@test.com",
	)
	if out, err := gitCommit.CombinedOutput(); err != nil {
		t.Fatalf("git commit in clone failed: %v\n%s", err, out)
	}
	gitPush := exec.Command("git", "-C", cloneDir, "push", "origin", "main")
	if out, err := gitPush.CombinedOutput(); err != nil {
		t.Fatalf("git push from clone failed: %v\n%s", err, out)
	}

	// Pull the change into the instance
	out, err = inst.run(t, "gitops", "pull", "-i", inst.ID)
	if err != nil {
		t.Fatalf("gitops pull failed: %v\n%s", err, out)
	}

	// Verify the change appears in the instance directory
	pulledFile := filepath.Join(installDir, "config", "settings", "global", "PullTest.php")
	data, err := os.ReadFile(pulledFile)
	if err != nil {
		t.Fatalf("PullTest.php not found in instance after pull: %v", err)
	}
	if !strings.Contains(string(data), "pull integration test") {
		t.Errorf("PullTest.php has unexpected content: %s", string(data))
	}

	// Verify the pull output reports the change
	if strings.Contains(out, "Already up to date") {
		t.Error("gitops pull reported 'Already up to date' but we pushed a new commit")
	}
	if !strings.Contains(out, "PullTest.php") {
		t.Logf("Note: pull output did not mention PullTest.php (may be expected depending on output format)")
	}
}
