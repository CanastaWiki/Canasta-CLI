package execute

import (
	"strings"
	"testing"
)

func TestRunExecutesDirect(t *testing.T) {
	// Run a simple command and check output
	err, output := Run("", "echo", "hello", "world")
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if got := strings.TrimSpace(output); got != "hello world" {
		t.Errorf("Run() output = %q, want %q", got, "hello world")
	}
}

func TestRunNoShellInterpretation(t *testing.T) {
	// Verify that shell metacharacters are NOT interpreted.
	// If Run still used bash -c, $HOME would be expanded by the shell.
	err, output := Run("", "echo", "$HOME")
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if got := strings.TrimSpace(output); got != "$HOME" {
		t.Errorf("Run() expanded shell variable: output = %q, want literal %q", got, "$HOME")
	}
}

func TestRunWithPath(t *testing.T) {
	dir := t.TempDir()
	err, output := Run(dir, "pwd")
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if got := strings.TrimSpace(output); got != dir {
		t.Errorf("Run() dir = %q, want %q", got, dir)
	}
}

func TestRunCommandNotFound(t *testing.T) {
	err, _ := Run("", "nonexistent-command-12345")
	if err == nil {
		t.Error("Run() expected error for nonexistent command, got nil")
	}
}
