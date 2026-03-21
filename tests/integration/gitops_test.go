//go:build integration

package integration

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"gopkg.in/yaml.v2"
)

// TestGitops_InitAndPush creates an instance, initializes gitops with a local
// bare repository (no SSH/credentials needed), makes a configuration change,
// pushes it, and verifies the commit appears in the remote.
func TestGitops_InitAndPush(t *testing.T) {
	inst := createTestInstance(t, "inttest-gitops")

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

	// Verify wikis.yaml.template was created and contains placeholders,
	// that vars.yaml captured the original URL, and that rendering the
	// template produces the original wikis.yaml content.
	wikisTemplatePath := filepath.Join(installDir, "wikis.yaml.template")
	wikisTemplateData, err := os.ReadFile(wikisTemplatePath)
	if err != nil {
		t.Fatalf("wikis.yaml.template not found after gitops init: %v", err)
	}
	wikisTemplate := string(wikisTemplateData)

	// Template should contain a placeholder, not the literal URL.
	if !strings.Contains(wikisTemplate, "{{wiki_url_") {
		t.Errorf("wikis.yaml.template should contain {{wiki_url_*}} placeholders, got:\n%s", wikisTemplate)
	}
	if strings.Contains(wikisTemplate, "localhost") {
		t.Errorf("wikis.yaml.template should not contain literal 'localhost', got:\n%s", wikisTemplate)
	}

	// vars.yaml should contain the wiki URL value.
	varsData, err := os.ReadFile(filepath.Join(installDir, "hosts", "testhost", "vars.yaml"))
	if err != nil {
		t.Fatalf("vars.yaml not found: %v", err)
	}
	var vars map[string]string
	if err := yaml.Unmarshal(varsData, &vars); err != nil {
		t.Fatalf("failed to parse vars.yaml: %v", err)
	}
	// The wiki created by "canasta create -w main" gets ID "main".
	wikiURL, ok := vars["wiki_url_main"]
	if !ok {
		t.Fatalf("vars.yaml missing wiki_url_main key, keys: %v", keysOf(vars))
	}
	if wikiURL != "localhost" {
		t.Errorf("wiki_url_main = %q, want %q", wikiURL, "localhost")
	}

	// Verify config/wikis.yaml is gitignored (not tracked in the bare repo).
	showWikis := exec.Command("git", "--git-dir", bareRepo, "show", "HEAD:config/wikis.yaml")
	if showOut, err := showWikis.CombinedOutput(); err == nil {
		t.Errorf("config/wikis.yaml should be gitignored but was found in bare repo:\n%s", showOut)
	}

	// Verify wikis.yaml.template IS tracked in the bare repo.
	showTmpl := exec.Command("git", "--git-dir", bareRepo, "show", "HEAD:wikis.yaml.template")
	showTmplOut, err := showTmpl.CombinedOutput()
	if err != nil {
		t.Fatalf("wikis.yaml.template not found in bare repo: %v\n%s", err, showTmplOut)
	}
	if !strings.Contains(string(showTmplOut), "{{wiki_url_") {
		t.Errorf("wikis.yaml.template in bare repo should contain placeholders, got:\n%s", showTmplOut)
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

	// Stage and push the change
	out, err = inst.run(t, "gitops", "add", "-i", inst.ID, "config/settings/global/GitopsTest.php")
	if err != nil {
		t.Fatalf("gitops add failed: %v\n%s", err, out)
	}

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

// keysOf returns the keys of a string map for diagnostic output.
func keysOf(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}
