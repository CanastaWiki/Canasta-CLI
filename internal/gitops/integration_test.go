package gitops

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

// TestFullConfigWorkflow exercises the end-to-end config workflow:
// extract template from .env → save template + vars → render back → verify round-trip.
func TestFullConfigWorkflow(t *testing.T) {
	dir := t.TempDir()

	// Simulate an existing .env file.
	envContent := `MW_SITE_SERVER=https://wiki.example.com
MW_SITE_FQDN=wiki.example.com
MYSQL_PASSWORD=secret123
WIKI_DB_PASSWORD=dbpass456
MW_SECRET_KEY=longsecretkey789
HTTP_PORT=80
HTTPS_PORT=443
MW_DB_NAME=mediawiki
MW_SITE_NAME=My Wiki
`
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte(envContent), 0644); err != nil {
		t.Fatal(err)
	}

	// Write custom-keys.yaml.
	customKeysContent := "keys:\n  - MY_CUSTOM_API_KEY\n"
	if err := os.WriteFile(filepath.Join(dir, customKeysFile), []byte(customKeysContent), 0644); err != nil {
		t.Fatal(err)
	}

	// Add the custom key to .env.
	envContent += "MY_CUSTOM_API_KEY=customvalue\n"
	if err := os.WriteFile(envPath, []byte(envContent), 0644); err != nil {
		t.Fatal(err)
	}

	// Load custom keys.
	customKeys, err := LoadCustomKeys(dir)
	if err != nil {
		t.Fatalf("LoadCustomKeys: %v", err)
	}

	// Extract template.
	placeholderKeys := AllPlaceholderKeys(customKeys)
	template, vars := ExtractTemplate(envContent, placeholderKeys)

	// Save template and vars.
	if err := SaveEnvTemplate(dir, template); err != nil {
		t.Fatalf("SaveEnvTemplate: %v", err)
	}
	if err := SaveVars(dir, "prod", vars); err != nil {
		t.Fatalf("SaveVars: %v", err)
	}

	// Reload and render.
	loadedTemplate, err := LoadEnvTemplate(dir)
	if err != nil {
		t.Fatalf("LoadEnvTemplate: %v", err)
	}
	loadedVars, err := LoadVars(dir, "prod")
	if err != nil {
		t.Fatalf("LoadVars: %v", err)
	}

	rendered, err := RenderTemplate(loadedTemplate, loadedVars)
	if err != nil {
		t.Fatalf("RenderTemplate: %v", err)
	}

	if rendered != envContent {
		t.Errorf("round-trip mismatch.\nGot:\n%s\nWant:\n%s", rendered, envContent)
	}

	// Verify secrets are in vars.
	if vars["mysql_password"] != "secret123" {
		t.Errorf("mysql_password = %q, want %q", vars["mysql_password"], "secret123")
	}
	if vars["my_custom_api_key"] != "customvalue" {
		t.Errorf("my_custom_api_key = %q, want %q", vars["my_custom_api_key"], "customvalue")
	}

	// Verify non-secret keys are NOT in vars (they stay in the template).
	if _, ok := vars["mw_db_name"]; ok {
		t.Error("mw_db_name should not be in vars")
	}
}

// TestAdminPasswordWorkflow tests reading passwords, adding to vars, and writing back.
func TestAdminPasswordWorkflow(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}

	// Write initial password files.
	if err := os.WriteFile(filepath.Join(configDir, "admin-password_main"), []byte("mainpass\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "admin-password_secondary"), []byte("secpass\n"), 0600); err != nil {
		t.Fatal(err)
	}

	// Read them.
	passwords, err := ReadAdminPasswords(dir)
	if err != nil {
		t.Fatalf("ReadAdminPasswords: %v", err)
	}

	// Add to vars.
	vars := VarsMap{"mysql_password": "dbpass"}
	for wikiID, password := range passwords {
		vars["admin_password_"+wikiID] = password
	}

	// Save vars.
	if err := SaveVars(dir, "testhost", vars); err != nil {
		t.Fatalf("SaveVars: %v", err)
	}

	// Write passwords from vars to a fresh directory.
	dir2 := t.TempDir()
	loadedVars, err := LoadVars(dir, "testhost")
	if err != nil {
		t.Fatalf("LoadVars: %v", err)
	}

	if err := WriteAdminPasswords(dir2, loadedVars); err != nil {
		t.Fatalf("WriteAdminPasswords: %v", err)
	}

	// Verify.
	data, err := os.ReadFile(filepath.Join(dir2, "config", "admin-password_main"))
	if err != nil {
		t.Fatalf("reading main password: %v", err)
	}
	if string(data) != "mainpass\n" {
		t.Errorf("main password = %q, want %q", string(data), "mainpass\n")
	}
}

// TestGitInitAndCommit tests basic git operations without git-crypt.
func TestGitInitAndCommit(t *testing.T) {
	dir := t.TempDir()

	if err := InitRepo(dir); err != nil {
		t.Fatalf("InitRepo: %v", err)
	}

	// Configure git user for CI environments where global config may not exist.
	if _, err := execute.Run(dir, "git", "config", "user.name", "Test"); err != nil {
		t.Fatalf("git config user.name: %v", err)
	}
	if _, err := execute.Run(dir, "git", "config", "user.email", "test@test.com"); err != nil {
		t.Fatalf("git config user.email: %v", err)
	}

	// Verify .git exists.
	if _, err := os.Stat(filepath.Join(dir, ".git")); err != nil {
		t.Fatalf(".git directory not found: %v", err)
	}

	// Create a file.
	if err := os.WriteFile(filepath.Join(dir, "test.txt"), []byte("hello\n"), 0644); err != nil {
		t.Fatal(err)
	}

	// Add and commit.
	if err := AddAll(dir); err != nil {
		t.Fatalf("AddAll: %v", err)
	}

	hash, err := Commit(dir, "test commit")
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if hash == "" {
		t.Error("expected non-empty commit hash")
	}

	// Verify current commit (Commit returns short hash, CurrentCommitHash returns full).
	current, err := CurrentCommitHash(dir)
	if err != nil {
		t.Fatalf("CurrentCommitHash: %v", err)
	}
	if !strings.HasPrefix(current, hash) {
		t.Errorf("current commit %q does not start with short hash %q", current, hash)
	}

	// Verify no uncommitted changes.
	hasChanges, _, err := HasUncommittedChanges(dir)
	if err != nil {
		t.Fatalf("HasUncommittedChanges: %v", err)
	}
	if hasChanges {
		t.Error("expected no uncommitted changes after commit")
	}

	// Make a change and verify detection.
	if err := os.WriteFile(filepath.Join(dir, "test.txt"), []byte("modified\n"), 0644); err != nil {
		t.Fatal(err)
	}

	hasChanges, files, err := HasUncommittedChanges(dir)
	if err != nil {
		t.Fatalf("HasUncommittedChanges: %v", err)
	}
	if !hasChanges {
		t.Error("expected uncommitted changes")
	}
	if len(files) != 1 {
		t.Errorf("expected 1 changed file, got %d", len(files))
	}
}

// TestHostsConfigWithMultipleHosts verifies that roles are preserved
// for multi-host configs and that missing roles produce an error.
func TestHostsConfigWithMultipleHosts(t *testing.T) {
	dir := t.TempDir()

	// Valid multi-host config with all roles set.
	cfg := &HostsConfig{
		CanastaID: "test",
		Hosts: map[string]HostEntry{
			"source": {Hostname: "s1.example.com", Role: RoleSource},
			"sink":   {Hostname: "s2.example.com", Role: RoleSink},
		},
	}
	if err := SaveHostsConfig(dir, cfg); err != nil {
		t.Fatalf("SaveHostsConfig: %v", err)
	}

	loaded, err := LoadHostsConfig(dir)
	if err != nil {
		t.Fatalf("LoadHostsConfig: %v", err)
	}
	if loaded.Hosts["source"].Role != RoleSource {
		t.Errorf("source role = %q, want %q", loaded.Hosts["source"].Role, RoleSource)
	}
	if loaded.Hosts["sink"].Role != RoleSink {
		t.Errorf("sink role = %q, want %q", loaded.Hosts["sink"].Role, RoleSink)
	}
}

// TestHostsConfigMissingRoleError verifies that a multi-host config with
// a missing role produces an error.
func TestHostsConfigMissingRoleError(t *testing.T) {
	dir := t.TempDir()

	cfg := &HostsConfig{
		CanastaID: "test",
		Hosts: map[string]HostEntry{
			"source": {Hostname: "s1.example.com", Role: RoleSource},
			"sink":   {Hostname: "s2.example.com"},
		},
	}
	if err := SaveHostsConfig(dir, cfg); err != nil {
		t.Fatalf("SaveHostsConfig: %v", err)
	}

	_, err := LoadHostsConfig(dir)
	if err == nil {
		t.Fatal("expected error for multi-host config with missing role")
	}
}

// TestHostsConfigInvalidRoleError verifies that an invalid role produces
// an error.
func TestHostsConfigInvalidRoleError(t *testing.T) {
	dir := t.TempDir()

	cfg := &HostsConfig{
		CanastaID: "test",
		Hosts: map[string]HostEntry{
			"prod": {Hostname: "s1.example.com", Role: "primary"},
		},
	}
	if err := SaveHostsConfig(dir, cfg); err != nil {
		t.Fatalf("SaveHostsConfig: %v", err)
	}

	_, err := LoadHostsConfig(dir)
	if err == nil {
		t.Fatal("expected error for invalid role")
	}
}
