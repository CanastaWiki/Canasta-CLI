package list

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
)

func TestList(t *testing.T) {
	tmpDir := t.TempDir()

	config.ResetForTesting(tmpDir)
	t.Cleanup(func() { config.ResetForTesting("") })

	installPath := filepath.Join(tmpDir, "canasta-test")
	err := os.MkdirAll(filepath.Join(installPath, "config"), 0755)
	if err != nil {
		t.Fatalf("Failed to create mock config dir: %v", err)
	}

	err = farmsettings.AddWiki("testwiki", installPath, "testwiki.local", "/", "Test Wiki")
	if err != nil {
		t.Fatalf("Failed to add mock wiki: %v", err)
	}
	err = farmsettings.AddWiki("devwiki", installPath, "devwiki.local", "/", "Dev Wiki")
	if err != nil {
		t.Fatalf("Failed to add second mock wiki: %v", err)
	}

	instance := config.Instance{
		ID:           "test-instance",
		Path:         installPath,
		Orchestrator: "compose",
	}
	err = config.Add(instance)
	if err != nil {
		t.Fatalf("Failed to add instance to config: %v", err)
	}

	oldStdout := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	err = List(config.Instance{}, false)

	w.Close()

	os.Stdout = oldStdout

	if err != nil {
		t.Fatalf("List() returned an error: %v", err)
	}

	var out bytes.Buffer
	_, err = io.Copy(&out, r)
	if err != nil {
		t.Fatalf("Failed to read from stdout pipe: %v", err)
	}

	outputStr := out.String()

	expectedValues := []string{
		"Canasta ID", "Wiki ID", "Server Name", "Server Path", "Instance Path", "Orchestrator",
		"test-instance",
		"testwiki", "testwiki.local",
		"devwiki", "devwiki.local",
		"/",
		installPath,
		"compose",
	}

	for _, expected := range expectedValues {
		if !strings.Contains(outputStr, expected) {
			t.Errorf("Expected output to contain '%s', but it did not.\nFull output:\n%s", expected, outputStr)
		}
	}
}

func TestListCleanup(t *testing.T) {
	tmpDir := t.TempDir()

	config.ResetForTesting(tmpDir)
	t.Cleanup(func() { config.ResetForTesting("") })

	installPath := filepath.Join(tmpDir, "stale-instance")

	instance := config.Instance{
		ID:           "stale-instance",
		Path:         installPath,
		Orchestrator: "compose",
	}
	if err := config.Add(instance); err != nil {
		t.Fatalf("Failed to add instance to config: %v", err)
	}

	oldStdout := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	err := List(config.Instance{}, true)

	w.Close()
	os.Stdout = oldStdout

	if err != nil {
		t.Fatalf("List() returned an error: %v", err)
	}

	var out bytes.Buffer
	if _, err := io.Copy(&out, r); err != nil {
		t.Fatalf("Failed to read from stdout pipe: %v", err)
	}

	outputStr := out.String()
	expectedOutput := "Removed stale entry 'stale-instance' (directory not found)"
	tableStr := strings.Replace(outputStr, expectedOutput, "", 1)

	if !strings.Contains(outputStr, expectedOutput) {
		t.Errorf("Expected output to contain '%s', but it did not.\nFull output:\n%s", expectedOutput, outputStr)
	}

	if strings.Contains(tableStr, "stale-instance") {
		t.Errorf("Expected stale entry 'stale-instance' to not appear in the listed output table, but it did.\nFull output:\n%s", outputStr)
	}

	instances, err := config.GetAll()
	if err != nil {
		t.Fatalf("Failed to get config: %v", err)
	}
	if _, exists := instances["stale-instance"]; exists {
		t.Errorf("Expected stale entry 'stale-instance' to be removed from config, but it still exists")
	}
}

func TestListNotFound(t *testing.T) {
	tmpDir := t.TempDir()

	config.ResetForTesting(tmpDir)
	t.Cleanup(func() { config.ResetForTesting("") })

	installPath := filepath.Join(tmpDir, "missing-instance")

	instance := config.Instance{
		ID:           "missing-instance",
		Path:         installPath,
		Orchestrator: "compose",
	}
	if err := config.Add(instance); err != nil {
		t.Fatalf("Failed to add instance to config: %v", err)
	}

	oldStdout := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	err := List(config.Instance{}, false)

	w.Close()
	os.Stdout = oldStdout

	if err != nil {
		t.Fatalf("List() returned an error: %v", err)
	}

	var out bytes.Buffer
	if _, err := io.Copy(&out, r); err != nil {
		t.Fatalf("Failed to read from stdout pipe: %v", err)
	}

	outputStr := out.String()
	expectedOutput := installPath + " [not found]"
	if !strings.Contains(outputStr, expectedOutput) {
		t.Errorf("Expected output to contain '%s', but it did not.\nFull output:\n%s", expectedOutput, outputStr)
	}
}
