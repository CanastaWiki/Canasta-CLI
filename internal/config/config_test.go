package config

import (
	"bytes"
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

	inst := Instance{
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

	inst := Instance{ID: "exists1", Path: "/tmp/exists1", Orchestrator: "compose"}
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

	inst := Instance{ID: "del1", Path: "/tmp/del1", Orchestrator: "compose"}
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

	inst := Instance{ID: "upd1", Path: "/tmp/upd1", Orchestrator: "compose"}
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

	inst := Instance{ID: "dup1", Path: "/tmp/dup1", Orchestrator: "compose"}
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

	inst1 := Instance{ID: "all1", Path: "/tmp/all1", Orchestrator: "compose"}
	inst2 := Instance{ID: "all2", Path: "/tmp/all2", Orchestrator: "compose"}
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
		t.Errorf("expected 2 instances, got %d", len(all))
	}
}

func TestGetCanastaID(t *testing.T) {
	setupTestDir(t)

	inst := Instance{ID: "pathtest", Path: "/tmp/pathtest", Orchestrator: "compose"}
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

	inst := Instance{ID: "subdir-test", Path: "/srv/canasta/my-wiki", Orchestrator: "compose"}
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
		{"parent of instance", "/srv/canasta", "", true},
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

func TestBuildFromRoundTrip(t *testing.T) {
	dir := setupTestDir(t)

	inst := Instance{
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

	inst := Instance{
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

	// Trigger initialization by calling any public function
	_, _ = Exists("anything")

	confPath := filepath.Join(dir, "conf.json")
	if _, err := os.Stat(confPath); os.IsNotExist(err) {
		t.Errorf("expected conf.json to be created at %s", confPath)
	}
}

func TestListAllNoInstances(t *testing.T) {
	setupTestDir(t)

	var buf bytes.Buffer
	err := ListAll(&buf)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}
	output := buf.String()
	if !strings.Contains(output, "No instances found") {
		t.Errorf("expected output to contain 'No instances found', got: %q", output)
	}
}

func TestListAllOneInstance(t *testing.T) {
	setupTestDir(t)

	inst := Instance{
		ID:           "single-wiki",
		Path:         "/tmp/test-wiki",
		Orchestrator: "compose",
	}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	var buf bytes.Buffer
	err := ListAll(&buf)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}
	output := buf.String()

	// Verify header appears
	if !strings.Contains(output, "Canasta ID") {
		t.Error("expected output to contain 'Canasta ID'")
	}
	// Verify instance ID appears
	if !strings.Contains(output, "single-wiki") {
		t.Error("expected output to contain instance ID 'single-wiki'")
	}
	// Verify instance path appears
	if !strings.Contains(output, "/tmp/test-wiki") {
		t.Error("expected output to contain instance path '/tmp/test-wiki'")
	}
	// Verify orchestrator appears
	if !strings.Contains(output, "compose") {
		t.Error("expected output to contain orchestrator 'compose'")
	}
}

func TestListAllMultipleInstances(t *testing.T) {
	setupTestDir(t)

	inst1 := Instance{
		ID:           "wiki1",
		Path:         "/tmp/wiki1",
		Orchestrator: "compose",
	}
	inst2 := Instance{
		ID:           "wiki2",
		Path:         "/tmp/wiki2",
		Orchestrator: "kubernetes",
	}
	inst3 := Instance{
		ID:           "wiki3",
		Path:         "/tmp/wiki3",
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

	var buf bytes.Buffer
	err := ListAll(&buf)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}
	output := buf.String()

	// Verify all instances appear
	if !strings.Contains(output, "wiki1") {
		t.Error("expected output to contain instance ID 'wiki1'")
	}
	if !strings.Contains(output, "wiki2") {
		t.Error("expected output to contain instance ID 'wiki2'")
	}
	if !strings.Contains(output, "wiki3") {
		t.Error("expected output to contain instance ID 'wiki3'")
	}

	// Verify all paths appear
	if !strings.Contains(output, "/tmp/wiki1") {
		t.Error("expected output to contain path '/tmp/wiki1'")
	}
	if !strings.Contains(output, "/tmp/wiki2") {
		t.Error("expected output to contain path '/tmp/wiki2'")
	}
	if !strings.Contains(output, "/tmp/wiki3") {
		t.Error("expected output to contain path '/tmp/wiki3'")
	}

	// Verify both orchestrators appear
	lines := strings.Split(output, "\n")
	composeCount := 0
	kubernetesCount := 0
	for _, line := range lines {
		if strings.Contains(line, "compose") {
			composeCount++
		}
		if strings.Contains(line, "kubernetes") {
			kubernetesCount++
		}
	}
	if composeCount < 2 {
		t.Errorf("expected at least 2 lines with 'compose', got %d", composeCount)
	}
	if kubernetesCount < 1 {
		t.Errorf("expected at least 1 line with 'kubernetes', got %d", kubernetesCount)
	}
}

func TestListAllMissingInstancePath(t *testing.T) {
	setupTestDir(t)

	inst := Instance{
		ID:           "missing-path",
		Path:         "/nonexistent/path/that/does/not/exist",
		Orchestrator: "compose",
	}
	if err := Add(inst); err != nil {
		t.Fatalf("Add() error = %v", err)
	}

	var buf bytes.Buffer
	err := ListAll(&buf)
	if err != nil {
		t.Fatalf("ListAll() error = %v", err)
	}
	output := buf.String()

	// Verify instance ID appears
	if !strings.Contains(output, "missing-path") {
		t.Error("expected output to contain instance ID 'missing-path'")
	}
	// Verify path is marked as not found
	if !strings.Contains(output, "[not found]") {
		t.Error("expected output to contain '[not found]'")
	}
}
