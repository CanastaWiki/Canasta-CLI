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

	var out, stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		t.Fatalf("git %s failed: %v\nstdout:\n%s\nstderr:\n%s",
			strings.Join(args, " "), err, out.String(), stderr.String())
	}
	return strings.TrimSpace(out.String())
}
