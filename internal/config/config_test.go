package config

import (
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
		Id:           "test1",
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
	if got.Id != "test1" || got.Path != "/tmp/test1" || got.Orchestrator != "compose" {
		t.Errorf("GetDetails() = %+v, want %+v", got, inst)
	}
}

func TestExists(t *testing.T) {
	setupTestDir(t)

	inst := Installation{Id: "exists1", Path: "/tmp/exists1", Orchestrator: "compose"}
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

	inst := Installation{Id: "del1", Path: "/tmp/del1", Orchestrator: "compose"}
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

	inst := Installation{Id: "upd1", Path: "/tmp/upd1", Orchestrator: "compose"}
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

	inst := Installation{Id: "dup1", Path: "/tmp/dup1", Orchestrator: "compose"}
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

	inst1 := Installation{Id: "all1", Path: "/tmp/all1", Orchestrator: "compose"}
	inst2 := Installation{Id: "all2", Path: "/tmp/all2", Orchestrator: "compose"}
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

	inst := Installation{Id: "pathtest", Path: "/tmp/pathtest", Orchestrator: "compose"}
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
			err := AddOrchestrator(Orchestrator{Id: tt.id, Path: "/usr/bin/test"})
			if (err != nil) != tt.wantErr {
				t.Errorf("AddOrchestrator(%q) error = %v, wantErr %v", tt.id, err, tt.wantErr)
			}
			if tt.wantErr && err != nil && !strings.Contains(err.Error(), "not supported") {
				t.Errorf("AddOrchestrator(%q) error = %q, want 'not supported'", tt.id, err.Error())
			}
		})
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
