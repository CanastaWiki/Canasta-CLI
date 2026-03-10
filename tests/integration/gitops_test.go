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

// TestGitops_InitAndPush creates an instance, initializes gitops with a local
// bare repository (no SSH/credentials needed), makes a configuration change,
// pushes it, and verifies the commit appears in the remote.
func TestGitops_InitAndPush(t *testing.T) {
	if _, err := exec.LookPath("git-crypt"); err != nil {
		t.Skip("git-crypt not installed, skipping gitops test")
	}

	inst := createTestInstance(t, "inttest-gitops")

	// Create a bare git repo to use as the remote
	bareRepo := t.TempDir()
	if out, err := exec.Command("git", "init", "--bare", bareRepo).CombinedOutput(); err != nil {
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

	// Verify .git directory was created
	if _, err := os.Stat(filepath.Join(installDir, ".git")); err != nil {
		t.Fatalf(".git directory not found after gitops init: %v", err)
	}

	// Verify the key file was exported
	if _, err := os.Stat(keyFile); err != nil {
		t.Fatalf("gitops key file not found: %v", err)
	}

	// Verify hosts.yaml exists
	if _, err := os.Stat(filepath.Join(installDir, "hosts.yaml")); err != nil {
		t.Fatalf("hosts.yaml not found: %v", err)
	}

	// Verify the initial commit was pushed to the bare repo
	logCmd := exec.Command("git", "--git-dir", bareRepo, "log", "--oneline", "-1")
	logOut, err := logCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git log on bare repo failed: %v\n%s", err, logOut)
	}
	if !strings.Contains(string(logOut), "Initial gitops") {
		t.Errorf("expected initial commit in bare repo, got: %s", string(logOut))
	}
	t.Logf("Bare repo initial commit: %s", strings.TrimSpace(string(logOut)))

	// Make a configuration change — create a test settings file
	settingsDir := filepath.Join(installDir, "config", "settings", "global")
	testFile := filepath.Join(settingsDir, "GitopsTest.php")
	if err := os.WriteFile(testFile, []byte("<?php\n// gitops integration test\n"), 0644); err != nil {
		t.Fatalf("failed to write test settings file: %v", err)
	}

	// Push the change
	out, err = inst.run(t, "gitops", "push", "-i", inst.ID, "-m", "Add test settings file")
	if err != nil {
		t.Fatalf("gitops push failed: %v\n%s", err, out)
	}

	// Verify "No changes" was NOT the result
	if strings.Contains(out, "No changes") {
		t.Error("gitops push reported no changes, but we added a file")
	}

	// Verify the new commit was pushed to the bare repo
	logCmd = exec.Command("git", "--git-dir", bareRepo, "log", "--oneline", "-2")
	logOut, err = logCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git log on bare repo failed: %v\n%s", err, logOut)
	}
	if !strings.Contains(string(logOut), "Add test settings file") {
		t.Errorf("expected push commit in bare repo, got:\n%s", string(logOut))
	}
	t.Logf("Bare repo log:\n%s", strings.TrimSpace(string(logOut)))

	// Verify the file exists in the remote's tree
	showCmd := exec.Command("git", "--git-dir", bareRepo, "show", "HEAD:config/settings/global/GitopsTest.php")
	showOut, err := showCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("file not found in bare repo: %v\n%s", err, showOut)
	}
	if !strings.Contains(string(showOut), "gitops integration test") {
		t.Errorf("unexpected file content in bare repo: %s", string(showOut))
	}
}
