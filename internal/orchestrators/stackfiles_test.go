package orchestrators

import (
	"os"
	"path/filepath"
	"testing"
)

func TestUpdateStackFiles_NoClobberSkipsDeletedExample(t *testing.T) {
	dir := t.TempDir()
	c := &ComposeOrchestrator{}

	// Initial write — creates all stack files including the .example.
	if err := c.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles: %v", err)
	}
	examplePath := filepath.Join(dir, "docker-compose.override.yml.example")
	if _, err := os.Stat(examplePath); err != nil {
		t.Fatalf("example file not created on initial write: %v", err)
	}

	// Delete the example file (simulates user removing it).
	if err := os.Remove(examplePath); err != nil {
		t.Fatalf("failed to remove example file: %v", err)
	}

	// Update — the example should NOT be recreated (no-clobber).
	changed, err := c.UpdateStackFiles(dir, false)
	if err != nil {
		t.Fatalf("UpdateStackFiles: %v", err)
	}
	if _, err := os.Stat(examplePath); err == nil {
		t.Error("noClobber file was recreated after deletion, expected it to stay gone")
	}
	if changed {
		t.Error("expected changed=false when only a noClobber file is missing")
	}
}

func TestUpdateStackFiles_RecreatesDeletedComposeFile(t *testing.T) {
	dir := t.TempDir()
	c := &ComposeOrchestrator{}

	// Initial write.
	if err := c.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles: %v", err)
	}

	composePath := filepath.Join(dir, "docker-compose.yml")
	if err := os.Remove(composePath); err != nil {
		t.Fatalf("failed to remove compose file: %v", err)
	}

	// Update — non-noClobber file should be recreated.
	changed, err := c.UpdateStackFiles(dir, false)
	if err != nil {
		t.Fatalf("UpdateStackFiles: %v", err)
	}
	if _, err := os.Stat(composePath); err != nil {
		t.Error("docker-compose.yml was not recreated after deletion")
	}
	if !changed {
		t.Error("expected changed=true when docker-compose.yml is missing")
	}
}

func TestUpdateStackFiles_NoClobberModifiedExampleIsRestored(t *testing.T) {
	dir := t.TempDir()
	c := &ComposeOrchestrator{}

	// Initial write.
	if err := c.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles: %v", err)
	}

	examplePath := filepath.Join(dir, "docker-compose.override.yml.example")
	original, err := os.ReadFile(examplePath)
	if err != nil {
		t.Fatalf("failed to read original: %v", err)
	}

	// Modify the file (user edited it, not deleted).
	if err := os.WriteFile(examplePath, []byte("user modified\n"), 0644); err != nil {
		t.Fatalf("failed to modify: %v", err)
	}

	// Update — file exists but differs, so it should be restored.
	changed, err := c.UpdateStackFiles(dir, false)
	if err != nil {
		t.Fatalf("UpdateStackFiles: %v", err)
	}
	if !changed {
		t.Error("expected changed=true when noClobber file was modified")
	}
	got, _ := os.ReadFile(examplePath)
	if string(got) != string(original) {
		t.Errorf("expected file to be restored to embedded content, got %q", string(got))
	}
}
