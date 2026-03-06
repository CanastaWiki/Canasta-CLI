package gitops

import (
	"os"
	"path/filepath"
	"testing"
)

func TestHostsConfigRoundTrip(t *testing.T) {
	dir := t.TempDir()

	original := &HostsConfig{
		CanastaID:    "my-wiki",
		PullRequests: true,
		Hosts: map[string]HostEntry{
			"prod": {Hostname: "server1.example.com", Role: RoleSource},
			"dev":  {Hostname: "server2.example.com", Role: RoleSink},
		},
	}

	if err := SaveHostsConfig(dir, original); err != nil {
		t.Fatalf("SaveHostsConfig: %v", err)
	}

	loaded, err := LoadHostsConfig(dir)
	if err != nil {
		t.Fatalf("LoadHostsConfig: %v", err)
	}

	if loaded.CanastaID != original.CanastaID {
		t.Errorf("CanastaID = %q, want %q", loaded.CanastaID, original.CanastaID)
	}
	if loaded.PullRequests != original.PullRequests {
		t.Errorf("PullRequests = %v, want %v", loaded.PullRequests, original.PullRequests)
	}
	if len(loaded.Hosts) != 2 {
		t.Fatalf("len(Hosts) = %d, want 2", len(loaded.Hosts))
	}
	if loaded.Hosts["prod"].Role != RoleSource {
		t.Errorf("prod role = %q, want %q", loaded.Hosts["prod"].Role, RoleSource)
	}
}

func TestHostsConfigDefaultRole(t *testing.T) {
	dir := t.TempDir()

	cfg := &HostsConfig{
		CanastaID: "test",
		Hosts: map[string]HostEntry{
			"solo": {Hostname: "server1.example.com"},
		},
	}
	if err := SaveHostsConfig(dir, cfg); err != nil {
		t.Fatalf("SaveHostsConfig: %v", err)
	}

	loaded, err := LoadHostsConfig(dir)
	if err != nil {
		t.Fatalf("LoadHostsConfig: %v", err)
	}

	if loaded.Hosts["solo"].Role != RoleBoth {
		t.Errorf("single-host default role = %q, want %q", loaded.Hosts["solo"].Role, RoleBoth)
	}
}

func TestVarsRoundTrip(t *testing.T) {
	dir := t.TempDir()

	vars := VarsMap{
		"mysql_password":    "secret123",
		"mw_site_server":    "https://wiki.example.com",
		"admin_password_mw": "adminpass",
	}

	if err := SaveVars(dir, "prod", vars); err != nil {
		t.Fatalf("SaveVars: %v", err)
	}

	loaded, err := LoadVars(dir, "prod")
	if err != nil {
		t.Fatalf("LoadVars: %v", err)
	}

	for k, v := range vars {
		if loaded[k] != v {
			t.Errorf("vars[%q] = %q, want %q", k, loaded[k], v)
		}
	}
}

func TestLoadCustomKeysNotExist(t *testing.T) {
	dir := t.TempDir()

	keys, err := LoadCustomKeys(dir)
	if err != nil {
		t.Fatalf("LoadCustomKeys: %v", err)
	}
	if len(keys) != 0 {
		t.Errorf("expected empty keys, got %v", keys)
	}
}

func TestLoadCustomKeys(t *testing.T) {
	dir := t.TempDir()

	content := "keys:\n  - MY_API_KEY\n  - MY_SECRET\n"
	if err := os.WriteFile(filepath.Join(dir, customKeysFile), []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	keys, err := LoadCustomKeys(dir)
	if err != nil {
		t.Fatalf("LoadCustomKeys: %v", err)
	}
	if len(keys) != 2 {
		t.Fatalf("expected 2 keys, got %d", len(keys))
	}
	if keys[0] != "MY_API_KEY" || keys[1] != "MY_SECRET" {
		t.Errorf("unexpected keys: %v", keys)
	}
}

func TestEnvTemplateRoundTrip(t *testing.T) {
	dir := t.TempDir()

	content := "DB_HOST=localhost\nDB_PASS={{mysql_password}}\n"
	if err := SaveEnvTemplate(dir, content); err != nil {
		t.Fatalf("SaveEnvTemplate: %v", err)
	}

	loaded, err := LoadEnvTemplate(dir)
	if err != nil {
		t.Fatalf("LoadEnvTemplate: %v", err)
	}
	if loaded != content {
		t.Errorf("template mismatch: got %q, want %q", loaded, content)
	}
}

func TestAppliedCommitRoundTrip(t *testing.T) {
	dir := t.TempDir()

	hash := "abc123def456"
	if err := SaveAppliedCommit(dir, hash); err != nil {
		t.Fatalf("SaveAppliedCommit: %v", err)
	}

	loaded, err := LoadAppliedCommit(dir)
	if err != nil {
		t.Fatalf("LoadAppliedCommit: %v", err)
	}
	if loaded != hash {
		t.Errorf("got %q, want %q", loaded, hash)
	}
}

func TestLoadAppliedCommitNotExist(t *testing.T) {
	dir := t.TempDir()

	loaded, err := LoadAppliedCommit(dir)
	if err != nil {
		t.Fatalf("LoadAppliedCommit: %v", err)
	}
	if loaded != "" {
		t.Errorf("expected empty string, got %q", loaded)
	}
}

func TestReadWriteAdminPasswords(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Write password files manually.
	if err := os.WriteFile(filepath.Join(configDir, "admin-password_mywiki"), []byte("pass1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "admin-password_otherwiki"), []byte("pass2\n"), 0600); err != nil {
		t.Fatal(err)
	}
	// Non-password file should be ignored.
	if err := os.WriteFile(filepath.Join(configDir, "LocalSettings.php"), []byte("<?php"), 0644); err != nil {
		t.Fatal(err)
	}

	passwords, err := ReadAdminPasswords(dir)
	if err != nil {
		t.Fatalf("ReadAdminPasswords: %v", err)
	}
	if len(passwords) != 2 {
		t.Fatalf("expected 2 passwords, got %d", len(passwords))
	}
	if passwords["mywiki"] != "pass1" {
		t.Errorf("mywiki password = %q, want %q", passwords["mywiki"], "pass1")
	}
	if passwords["otherwiki"] != "pass2" {
		t.Errorf("otherwiki password = %q, want %q", passwords["otherwiki"], "pass2")
	}

	// Test WriteAdminPasswords.
	dir2 := t.TempDir()
	vars := VarsMap{
		"admin_password_wiki1": "newpass1",
		"admin_password_wiki2": "newpass2",
		"mw_site_server":       "https://example.com",
	}
	if err := WriteAdminPasswords(dir2, vars); err != nil {
		t.Fatalf("WriteAdminPasswords: %v", err)
	}

	data1, err := os.ReadFile(filepath.Join(dir2, "config", "admin-password_wiki1"))
	if err != nil {
		t.Fatalf("reading wiki1 password file: %v", err)
	}
	if string(data1) != "newpass1\n" {
		t.Errorf("wiki1 password file = %q, want %q", string(data1), "newpass1\n")
	}

	// Non-password key should not create a file.
	if _, err := os.Stat(filepath.Join(dir2, "config", "admin-password_mw_site_server")); !os.IsNotExist(err) {
		t.Error("non-password key should not create a file")
	}
}
