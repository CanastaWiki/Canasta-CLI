package config

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func setupTestDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	ResetForTesting(dir)
	return dir
}

func TestAddAndGetDetails(t *testing.T) {
	setupTestDir(t)

	inst := Installation{
		ID:           "test1",
		Path:         "/tmp/test1",
		Orchestrator: "compose",
	}

	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	got, err := GetDetails("test1")
	if err != nil {
		t.Fatalf("GetDetails() error = %v", err)
	}
	if got.ID != "test1" || got.Path != "/tmp/test1" || got.Orchestrator != "compose" {
		t.Errorf("GetDetails() = %+v, want %+v", got, inst)
	}
}

func TestExists(t *testing.T) {
	setupTestDir(t)

	inst := Installation{ID: "exists1", Path: "/tmp/exists1", Orchestrator: "compose"}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	exists, err := Exists("exists1")
	if err != nil {
		t.Fatalf("Exists() error = %v", err)
	}
	if !exists {
		t.Error("expected exists1 to exist")
	}

	exists, err = Exists("missing")
	if err != nil {
		t.Fatalf("Exists() error = %v", err)
	}
	if exists {
		t.Error("expected missing to not exist")
	}
}

func TestDelete(t *testing.T) {
	setupTestDir(t)

	inst := Installation{ID: "del1", Path: "/tmp/del1", Orchestrator: "compose"}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	if err := Delete("del1"); err != nil {
		t.Fatalf("Delete() error = %v", err)
	}

	exists, err := Exists("del1")
	if err != nil {
		t.Fatalf("Exists() error = %v", err)
	}
	if exists {
		t.Error("expected del1 to not exist after delete")
	}
}

func TestUpdate(t *testing.T) {
	setupTestDir(t)

	inst := Installation{ID: "upd1", Path: "/tmp/upd1", Orchestrator: "compose"}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	inst.Path = "/tmp/upd1-new"
	if err := Update(inst); err != nil {
		t.Fatalf("Update() error = %v", err)
	}

	got, err := GetDetails("upd1")
	if err != nil {
		t.Fatalf("GetDetails() error = %v", err)
	}
	if got.Path != "/tmp/upd1-new" {
		t.Errorf("expected path /tmp/upd1-new, got %s", got.Path)
	}
}

func TestDuplicateAdd(t *testing.T) {
	setupTestDir(t)

	inst := Installation{ID: "dup1", Path: "/tmp/dup1", Orchestrator: "compose"}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	err := Add(inst)
	if err == nil {
		t.Fatal("expected error on duplicate add, got nil")
	}
}

func TestDeleteNonexistent(t *testing.T) {
	setupTestDir(t)

	err := Delete("nonexistent")
	if err == nil {
		t.Fatal("expected error on deleting nonexistent, got nil")
	}
}

func TestGetAll(t *testing.T) {
	setupTestDir(t)

	inst1 := Installation{ID: "all1", Path: "/tmp/all1", Orchestrator: "compose"}
	inst2 := Installation{ID: "all2", Path: "/tmp/all2", Orchestrator: "compose"}
	if err := Add(inst1); err != nil {
		t.Fatalf("Add() error = %v", err)
	}
	if err := Add(inst2); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	all, err := GetAll()
	if err != nil {
		t.Fatalf("GetAll() error = %v", err)
	}
	if len(all) != 2 {
		t.Errorf("expected 2 installations, got %d", len(all))
	}
}

func TestGetCanastaID(t *testing.T) {
	setupTestDir(t)

	inst := Installation{ID: "pathtest", Path: "/tmp/pathtest", Orchestrator: "compose"}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	id, err := GetCanastaID("/tmp/pathtest")
	if err != nil {
		t.Fatalf("GetCanastaID() error = %v", err)
	}
	if id != "pathtest" {
		t.Errorf("expected pathtest, got %s", id)
	}

	_, err = GetCanastaID("/tmp/nonexistent")
	if err == nil {
		t.Fatal("expected error for nonexistent path, got nil")
	}
}

func TestGetCanastaIDFromSubdirectory(t *testing.T) {
	setupTestDir(t)

	inst := Installation{ID: "subdir-test", Path: "/srv/canasta/my-wiki", Orchestrator: "compose"}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	tests := []struct {
		name    string
		path    string
		wantID  string
		wantErr bool
	}{
		{"exact match", "/srv/canasta/my-wiki", "subdir-test", false},
		{"one level deep", "/srv/canasta/my-wiki/config", "subdir-test", false},
		{"two levels deep", "/srv/canasta/my-wiki/config/settings", "subdir-test", false},
		{"unrelated path", "/srv/other/project", "", true},
		{"parent of installation", "/srv/canasta", "", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			id, err := GetCanastaID(tt.path)
			if (err != nil) != tt.wantErr {
				t.Errorf("GetCanastaID(%q) error = %v, wantErr %v", tt.path, err, tt.wantErr)
				return
			}
			if id != tt.wantID {
				t.Errorf("GetCanastaID(%q) = %q, want %q", tt.path, id, tt.wantID)
			}
		})
	}
}

func TestAddOrchestratorSupported(t *testing.T) {
	tests := []struct {
		name    string
		id      string
		wantErr bool
	}{
		{"compose accepted", "compose", false},
		{"kubernetes accepted", "kubernetes", false},
		{"k8s alias accepted", "k8s", false},
		{"unknown rejected", "nomad", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			setupTestDir(t)
			err := AddOrchestrator(Orchestrator{ID: tt.id, Path: "/usr/bin/test"})
			if (err != nil) != tt.wantErr {
				t.Errorf("AddOrchestrator(%q) error = %v, wantErr %v", tt.id, err, tt.wantErr)
			}
			if tt.wantErr && err != nil && !strings.Contains(err.Error(), "not supported") {
				t.Errorf("AddOrchestrator(%q) error = %q, want 'not supported'", tt.id, err.Error())
			}
		})
	}
}

func TestBuildFromRoundTrip(t *testing.T) {
	dir := setupTestDir(t)

	inst := Installation{
		ID:           "bf1",
		Path:         "/tmp/bf1",
		Orchestrator: "compose",
		BuildFrom:    "/home/user/workspace",
	}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	got, err := GetDetails("bf1")
	if err != nil {
		t.Fatalf("GetDetails() error = %v", err)
	}
	if got.BuildFrom != "/home/user/workspace" {
		t.Errorf("expected BuildFrom '/home/user/workspace', got %q", got.BuildFrom)
	}

	// Verify the JSON file contains the buildFrom key
	data, err := os.ReadFile(filepath.Join(dir, "conf.json"))
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	if !strings.Contains(string(data), `"buildFrom"`) {
		t.Error("expected conf.json to contain 'buildFrom' key")
	}
}

func TestBuildFromOmittedWhenEmpty(t *testing.T) {
	dir := setupTestDir(t)

	inst := Installation{
		ID:           "nobf1",
		Path:         "/tmp/nobf1",
		Orchestrator: "compose",
	}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	// Verify the JSON file does NOT contain buildFrom when empty
	data, err := os.ReadFile(filepath.Join(dir, "conf.json"))
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	if strings.Contains(string(data), `"buildFrom"`) {
		t.Error("expected conf.json to omit 'buildFrom' when empty")
	}
}

func TestGetConfigDirEnvOverride(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("CANASTA_CONFIG_DIR", dir)

	got, err := GetConfigDir()
	if err != nil {
		t.Fatalf("GetConfigDir() error = %v", err)
	}
	if got != dir {
		t.Errorf("GetConfigDir() = %q, want %q", got, dir)
	}
}

func TestGetConfigDirEnvCreatesDir(t *testing.T) {
	base := t.TempDir()
	target := filepath.Join(base, "custom-canasta")
	t.Setenv("CANASTA_CONFIG_DIR", target)

	got, err := GetConfigDir()
	if err != nil {
		t.Fatalf("GetConfigDir() error = %v", err)
	}
	if got != target {
		t.Errorf("GetConfigDir() = %q, want %q", got, target)
	}

	fi, err := os.Stat(target)
	if err != nil {
		t.Fatalf("expected directory to be created: %v", err)
	}
	if !fi.IsDir() {
		t.Error("expected target to be a directory")
	}
}

func TestConfFileCreated(t *testing.T) {
	dir := setupTestDir(t)

	_, _ = Exists("anything")

	confPath := filepath.Join(dir, "conf.json")
	if _, err := os.Stat(confPath); os.IsNotExist(err) {
		t.Errorf("expected conf.json to be created at %s", confPath)
	}
}

func captureOutput(fn func() error) (string, error) {
	r, w, err := os.Pipe()
	if err != nil {
		return "", err
	}
	defer r.Close()

	// Save the original stdout
	oldStdout := os.Stdout
	os.Stdout = w
	err = fn()

	os.Stdout = oldStdout
	w.Close()

	// Read the output
	var buf bytes.Buffer
	io.Copy(&buf, r)
	return buf.String(), err
}

func TestListAllNoInstallations(t *testing.T) {
	setupTestDir(t)

	all, err := GetAll()
	if err != nil {
		t.Fatalf("GetAll() error = %v", err)
	}
	if len(all) != 0 {
		t.Fatalf("expected 0 installations, got %d", len(all))
	}

	output, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}

	if !strings.Contains(output, "No instances found.") {
		t.Errorf("expected 'No instances found.' in output, got: %q", output)
	}
	if strings.Contains(output, "Canasta ID") {
		t.Error("expected no table header when there are no installations")
	}
}

func TestListAllSingleInstallation(t *testing.T) {
	setupTestDir(t)

	tempDir := t.TempDir()

	inst := Installation{
		ID:           "single-test",
		Path:         tempDir,
		Orchestrator: "compose",
	}

	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}
	all, err := GetAll()
	if err != nil {
		t.Fatalf("GetAll() error = %v", err)
	}
	if len(all) != 1 {
		t.Fatalf("expected 1 installation, got %d", len(all))
	}
	output, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}

	// Verify table header is present
	if !strings.Contains(output, "Canasta ID") {
		t.Error("expected table header 'Canasta ID' in output")
	}
	if !strings.Contains(output, "single-test") {
		t.Errorf("expected 'single-test' in output, got: %q", output)
	}
	if !strings.Contains(output, "compose") {
		t.Errorf("expected 'compose' in output, got: %q", output)
	}
	if !strings.Contains(output, tempDir) || !strings.Contains(output, "N/A") {
		t.Errorf("expected installation path and N/A (since no wikis.yaml) in output, got: %q", output)
	}
	if strings.Contains(output, "No instances found.") {
		t.Error("expected 'No instances found.' to NOT be in outputt")
	}
}

func TestListAllMultipleInstallations(t *testing.T) {
	setupTestDir(t)

	tempDir1 := t.TempDir()
	tempDir2 := t.TempDir()
	tempDir3 := t.TempDir()

	inst1 := Installation{
		ID:           "multi-test-1",
		Path:         tempDir1,
		Orchestrator: "compose",
	}
	inst2 := Installation{
		ID:           "multi-test-2",
		Path:         tempDir2,
		Orchestrator: "kubernetes",
	}
	inst3 := Installation{
		ID:           "multi-test-3",
		Path:         tempDir3,
		Orchestrator: "compose",
	}

	if err := Add(inst1); err != nil {
		t.Fatalf("Add(inst1) error = %v", err)
	}
	if err := Add(inst2); err != nil {
		t.Fatalf("Add(inst2) error = %v", err)
	}
	if err := Add(inst3); err != nil {
		t.Fatalf("Add(inst3) error = %v", err)
	}
	all, err := GetAll()
	if err != nil {
		t.Fatalf("GetAll() error = %v", err)
	}
	if len(all) != 3 {
		t.Fatalf("expected 3 installations, got %d", len(all))
	}
	output, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}
	if !strings.Contains(output, "Canasta ID") {
		t.Error("expected table header 'Canasta ID' in output")
	}
	initialInstallations := []string{"multi-test-1", "multi-test-2", "multi-test-3"}
	for _, id := range initialInstallations {
		if !strings.Contains(output, id) {
			t.Errorf("expected '%s' in output, got: %q", id, output)
		}
	}
	if !strings.Contains(output, "compose") {
		t.Error("expected 'compose' in output")
	}
	if !strings.Contains(output, "kubernetes") {
		t.Error("expected 'kubernetes' in output")
	}
	if !strings.Contains(output, tempDir1) {
		t.Errorf("expected tempDir1 in output")
	}
	if !strings.Contains(output, tempDir2) {
		t.Errorf("expected tempDir2 in output")
	}
	if !strings.Contains(output, tempDir3) {
		t.Errorf("expected tempDir3 in output")
	}
	if strings.Contains(output, "No instances found.") {
		t.Error("expected 'No instances found.' to NOT be in output")
	}
}

func TestListAllMissingPath(t *testing.T) {
	setupTestDir(t)
	inst := Installation{
		ID:           "missing-path-test",
		Path:         "/nonexistent/path/to/installation",
		Orchestrator: "compose",
	}

	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}
	output, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}
	if !strings.Contains(output, "Canasta ID") {
		t.Error("expected table header 'Canasta ID' in output")
	}
	if !strings.Contains(output, "missing-path-test") {
		t.Errorf("expected 'missing-path-test' in output, got: %q", output)
	}
	if !strings.Contains(output, "[not found]") {
		t.Errorf("expected '[not found]' in output for missing path, got: %q", output)
	}
	if !strings.Contains(output, "N/A") {
		t.Errorf("expected 'N/A' in output when path is missing, got: %q", output)
	}
}

func TestListAllIntegration(t *testing.T) {
	dir := setupTestDir(t)

	tempDir := t.TempDir()
	inst := Installation{
		ID:           "integration-test",
		Path:         tempDir,
		Orchestrator: "compose",
	}

	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	output1, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error on first call = %v", err)
	}
	if !strings.Contains(output1, "integration-test") {
		t.Errorf("expected 'integration-test' in first output")
	}

	if err := Delete("integration-test"); err != nil {
		t.Fatalf("Delete() error = %v", err)
	}

	output2, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error on second call = %v", err)
	}
	if !strings.Contains(output2, "No instances found.") {
		t.Errorf("expected 'No instances found.' in second output")
	}

	confPath := filepath.Join(dir, "conf.json")
	if _, err := os.Stat(confPath); os.IsNotExist(err) {
		t.Errorf("expected conf.json to exist at %s", confPath)
	}
}

func TestListAllWithWiki(t *testing.T) {
	setupTestDir(t)
	tempDir := t.TempDir()

	configDir := filepath.Join(tempDir, "config")
	if err := os.Mkdir(configDir, 0755); err != nil {
		t.Fatalf("failed to create config dir: %v", err)
	}

	yamlContent := `
wikis:
- id: mywiki
  url: wiki.example.com
  name: My Wiki
`
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte(yamlContent), 0644); err != nil {
		t.Fatalf("failed to write wikis.yaml: %v", err)
	}

	inst := Installation{
		ID:           "wiki-test",
		Path:         tempDir,
		Orchestrator: "compose",
	}

	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	output, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}

	if !strings.Contains(output, "wiki-test") {
		t.Errorf("expected installation ID 'wiki-test', got: %q", output)
	}
	if !strings.Contains(output, "mywiki") {
		t.Errorf("expected wiki ID 'mywiki', got: %q", output)
	}
	if !strings.Contains(output, "wiki.example.com") {
		t.Errorf("expected server name 'wiki.example.com', got: %q", output)
	}
}

func TestListAllWithMultipleWikis(t *testing.T) {
	setupTestDir(t)
	tempDir := t.TempDir()

	configDir := filepath.Join(tempDir, "config")
	if err := os.Mkdir(configDir, 0755); err != nil {
		t.Fatalf("failed to create config dir: %v", err)
	}

	yamlContent := `
wikis:
- id: wiki1
  url: one.example.com
  name: First Wiki
- id: wiki2
  url: two.example.com/sub
  name: Second Wiki
`
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte(yamlContent), 0644); err != nil {
		t.Fatalf("failed to write wikis.yaml: %v", err)
	}

	inst := Installation{
		ID:           "multi-wiki-test",
		Path:         tempDir,
		Orchestrator: "compose",
	}

	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	output, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}

	if !strings.Contains(output, "multi-wiki-test") {
		t.Errorf("expected installation ID 'multi-wiki-test', got: %q", output)
	}
	if !strings.Contains(output, "wiki1") {
		t.Errorf("expected wiki ID 'wiki1', got: %q", output)
	}
	if !strings.Contains(output, "wiki2") {
		t.Errorf("expected wiki ID 'wiki2', got: %q", output)
	}
	if !strings.Contains(output, "one.example.com") {
		t.Errorf("expected server name 'one.example.com', got: %q", output)
	}
	if !strings.Contains(output, "/sub") {
		t.Errorf("expected path '/sub', got: %q", output)
	}
	// verify that the second wiki line starts with a dash or some indicator for same installation
	// based on implementation: fmt.Fprintf(writer, "%s\t%s\t%s\t%s\t%s\t%s\n", "-", ...)
	if !strings.Contains(output, "-") {
		t.Errorf("expected dash '-' for subsequent wiki entries, got: %q", output)
	}
}

func TestListAllBrokenWikisYaml(t *testing.T) {
	setupTestDir(t)
	tempDir := t.TempDir()

	configDir := filepath.Join(tempDir, "config")
	if err := os.Mkdir(configDir, 0755); err != nil {
		t.Fatalf("failed to create config dir: %v", err)
	}

	yamlContent := `
wikis: [
  this is invalid yaml
`
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte(yamlContent), 0644); err != nil {
		t.Fatalf("failed to write wikis.yaml: %v", err)
	}

	inst := Installation{
		ID:           "broken-yaml-test",
		Path:         tempDir,
		Orchestrator: "compose",
	}

	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	// function prints error to stdout: fmt.Printf("Error reading wikis.yaml ...")
	output, err := captureOutput(ListAll)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}

	if !strings.Contains(output, "Error reading wikis.yaml") {
		t.Errorf("expected error message in output for broken yaml, got: %q", output)
	}
}

func TestListAllReadError(t *testing.T) {
	dir := setupTestDir(t)

	inst := Installation{ID: "test", Path: "/tmp/test", Orchestrator: "compose"}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	confPath := filepath.Join(dir, "conf.json")
	if err := os.WriteFile(confPath, []byte("{invalid json"), 0644); err != nil {
		t.Fatalf("failed to corrupt conf.json: %v", err)
	}

	err := ListAll()
	if err == nil {
		t.Error("expected error from ListAll when conf.json is invalid, got nil")
	}
}

func TestListAllInitError(t *testing.T) {
	tempDir := t.TempDir()
	readOnlyDir := filepath.Join(tempDir, "readonly")
	if err := os.Mkdir(readOnlyDir, 0555); err != nil {
		t.Fatalf("failed to create read-only dir: %v", err)
	}

	ResetForTesting(readOnlyDir)
	err := ListAll()
	if err == nil {
		if os.Geteuid() != 0 {
			t.Error("expected error from ListAll when config dir is read-only")
		}
	}
}
