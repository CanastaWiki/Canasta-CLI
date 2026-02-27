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
	tmpDir, err := os.MkdirTemp("", "canasta-list-test")
	if err != nil {
		t.Fatalf("Failed to create temp config dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	config.ResetForTesting(tmpDir)

	installPath := filepath.Join(tmpDir, "canasta-test")
	err = os.MkdirAll(filepath.Join(installPath, "config"), 0755)
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

	installation := config.Installation{
		Id:           "test-instance",
		Path:         installPath,
		Orchestrator: "compose",
	}
	err = config.Add(installation)
	if err != nil {
		t.Fatalf("Failed to add installation to config: %v", err)
	}

	oldStdout := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	err = List(config.Installation{}, false)
	
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
		"Canasta ID", "Wiki ID", "Server Name", "Server Path", "Installation Path", "Orchestrator",
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
