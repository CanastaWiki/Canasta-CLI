package canasta

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/crypto/bcrypt"
)

func TestGetEnvVariable(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")

	content := "KEY1=value1\nKEY2=value2\nKEY_WITH_EQUALS=abc=def\nQUOTED=\"hello world\"\n"
	if err := os.WriteFile(envPath, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	vars, err := GetEnvVariable(envPath)
	if err != nil {
		t.Fatalf("GetEnvVariable() error = %v", err)
	}

	tests := []struct {
		key, want string
	}{
		{"KEY1", "value1"},
		{"KEY2", "value2"},
		{"KEY_WITH_EQUALS", "abc=def"},
		{"QUOTED", "hello world"},
	}

	for _, tt := range tests {
		if got := vars[tt.key]; got != tt.want {
			t.Errorf("GetEnvVariable()[%s] = %q, want %q", tt.key, got, tt.want)
		}
	}
}

func TestGetEnvVariableEmpty(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")

	if err := os.WriteFile(envPath, []byte(""), 0644); err != nil {
		t.Fatal(err)
	}

	vars, err := GetEnvVariable(envPath)
	if err != nil {
		t.Fatalf("GetEnvVariable() error = %v", err)
	}
	if len(vars) != 0 {
		t.Errorf("expected empty map, got %v", vars)
	}
}

func TestGetEnvVariableNotFound(t *testing.T) {
	_, err := GetEnvVariable("/nonexistent/.env")
	if err == nil {
		t.Fatal("expected error for nonexistent file, got nil")
	}
}

func TestSaveEnvVariable(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")

	// Start with a file
	initial := "KEY1=old\nKEY2=keep\n"
	if err := os.WriteFile(envPath, []byte(initial), 0644); err != nil {
		t.Fatal(err)
	}

	// Update existing key
	if err := SaveEnvVariable(envPath, "KEY1", "new"); err != nil {
		t.Fatalf("SaveEnvVariable() error = %v", err)
	}

	// Add new key
	if err := SaveEnvVariable(envPath, "KEY3", "added"); err != nil {
		t.Fatalf("SaveEnvVariable() error = %v", err)
	}

	vars, err := GetEnvVariable(envPath)
	if err != nil {
		t.Fatalf("GetEnvVariable() error = %v", err)
	}

	if vars["KEY1"] != "new" {
		t.Errorf("KEY1 = %q, want \"new\"", vars["KEY1"])
	}
	if vars["KEY2"] != "keep" {
		t.Errorf("KEY2 = %q, want \"keep\"", vars["KEY2"])
	}
	if vars["KEY3"] != "added" {
		t.Errorf("KEY3 = %q, want \"added\"", vars["KEY3"])
	}
}

func TestGeneratePasswords(t *testing.T) {
	info := CanastaVariables{
		Id:        "test",
		AdminName: "admin",
	}

	result, err := GeneratePasswords(t.TempDir(), info)
	if err != nil {
		t.Fatalf("GeneratePasswords() error = %v", err)
	}

	// Check admin password
	if len(result.AdminPassword) != 30 {
		t.Errorf("AdminPassword length = %d, want 30", len(result.AdminPassword))
	}

	// Check root DB password
	if len(result.RootDBPassword) != 30 {
		t.Errorf("RootDBPassword length = %d, want 30", len(result.RootDBPassword))
	}

	// Check wiki DB password
	if len(result.WikiDBPassword) != 30 {
		t.Errorf("WikiDBPassword length = %d, want 30", len(result.WikiDBPassword))
	}

	// Check no dollar signs (T355013)
	for _, pw := range []string{result.AdminPassword, result.RootDBPassword, result.WikiDBPassword} {
		if strings.Contains(pw, "$") {
			t.Errorf("password contains $: %s", pw)
		}
	}
}

func TestGeneratePasswordsPreserveExisting(t *testing.T) {
	info := CanastaVariables{
		Id:             "test",
		AdminName:      "admin",
		AdminPassword:  "myadminpass",
		RootDBPassword: "myrootpass",
		WikiDBPassword: "mywikipass",
	}

	result, err := GeneratePasswords(t.TempDir(), info)
	if err != nil {
		t.Fatalf("GeneratePasswords() error = %v", err)
	}

	if result.AdminPassword != "myadminpass" {
		t.Errorf("AdminPassword = %q, want myadminpass", result.AdminPassword)
	}
	if result.RootDBPassword != "myrootpass" {
		t.Errorf("RootDBPassword = %q, want myrootpass", result.RootDBPassword)
	}
	if result.WikiDBPassword != "mywikipass" {
		t.Errorf("WikiDBPassword = %q, want mywikipass", result.WikiDBPassword)
	}
}

func TestGenerateDBPasswordsRootUser(t *testing.T) {
	info := CanastaVariables{
		WikiDBUsername: "root",
	}

	result, err := GenerateDBPasswords(info)
	if err != nil {
		t.Fatalf("GenerateDBPasswords() error = %v", err)
	}

	// When WikiDBUsername is "root", WikiDBPassword should equal RootDBPassword
	if result.WikiDBPassword != result.RootDBPassword {
		t.Errorf("expected WikiDBPassword == RootDBPassword when user is root")
	}
}

func TestValidateDatabasePath(t *testing.T) {
	tests := []struct {
		path    string
		wantErr bool
	}{
		{"/path/to/dump.sql", false},
		{"/path/to/dump.sql.gz", false},
		{"/path/to/dump.txt", true},
		{"/path/to/dump.tar.gz", true},
		{"dump.sql", false},
	}

	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			err := ValidateDatabasePath(tt.path)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateDatabasePath(%q) error = %v, wantErr %v", tt.path, err, tt.wantErr)
			}
		})
	}
}

func TestGenerateSecretKey(t *testing.T) {
	key, err := generateSecretKey()
	if err != nil {
		t.Fatalf("generateSecretKey() error = %v", err)
	}
	if len(key) != 64 {
		t.Errorf("expected 64-char hex string, got length %d", len(key))
	}

	// Verify it's valid hex
	for _, c := range key {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			t.Errorf("invalid hex character: %c", c)
		}
	}

	// Verify uniqueness
	key2, err2 := generateSecretKey()
	if err2 != nil {
		t.Fatalf("generateSecretKey() second call error = %v", err2)
	}
	if key == key2 {
		t.Error("expected different keys on successive calls")
	}
}

func TestContainsProfile(t *testing.T) {
	tests := []struct {
		name     string
		profiles string
		target   string
		want     bool
	}{
		{"exact match", "observable", "observable", true},
		{"in list", "web,observable,worker", "observable", true},
		{"first in list", "observable,worker", "observable", true},
		{"last in list", "web,observable", "observable", true},
		{"not present", "web,worker", "observable", false},
		{"empty string", "", "observable", false},
		{"partial match should not match", "observable-extra", "observable", false},
		{"with whitespace", "web, observable , worker", "observable", true},
		{"single non-matching", "web", "observable", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ContainsProfile(tt.profiles, tt.target)
			if got != tt.want {
				t.Errorf("ContainsProfile(%q, %q) = %v, want %v", tt.profiles, tt.target, got, tt.want)
			}
		})
	}
}

// setupTestInstall creates a minimal installation directory with .env and wikis.yaml
func setupTestInstall(t *testing.T, envContent, wikisYaml string) string {
	t.Helper()
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte(envContent), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte(wikisYaml), 0644); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestEnsureObservabilityCredentials_NotObservable(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web\n", "wikis:\n  - id: main\n    url: example.com\n")

	active, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if active {
		t.Error("expected active=false when observable profile is not set")
	}

	// Verify no OS_ variables were added
	vars, _ := GetEnvVariable(filepath.Join(dir, ".env"))
	if vars["OS_USER"] != "" {
		t.Error("OS_USER should not be set")
	}
}

func TestEnsureObservabilityCredentials_GeneratesCredentials(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=observable\n", "wikis:\n  - id: main\n    url: example.com\n")

	active, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !active {
		t.Error("expected active=true when observable profile is set")
	}

	vars, _ := GetEnvVariable(filepath.Join(dir, ".env"))
	if vars["OS_USER"] != "admin" {
		t.Errorf("OS_USER = %q, want \"admin\"", vars["OS_USER"])
	}
	if len(vars["OS_PASSWORD"]) != 30 {
		t.Errorf("OS_PASSWORD length = %d, want 30", len(vars["OS_PASSWORD"]))
	}
	if !strings.HasPrefix(vars["OS_PASSWORD_HASH"], "$2a$") {
		t.Errorf("OS_PASSWORD_HASH should be a bcrypt hash, got %q", vars["OS_PASSWORD_HASH"])
	}

	// Verify the hash matches the password
	err = bcrypt.CompareHashAndPassword([]byte(vars["OS_PASSWORD_HASH"]), []byte(vars["OS_PASSWORD"]))
	if err != nil {
		t.Errorf("bcrypt hash does not match password: %v", err)
	}
}

func TestEnsureObservabilityCredentials_PreservesExisting(t *testing.T) {
	envContent := "COMPOSE_PROFILES=observable\nOS_USER=customuser\nOS_PASSWORD=custompass\nOS_PASSWORD_HASH=$2a$10$existinghash\n"
	dir := setupTestInstall(t, envContent, "wikis:\n  - id: main\n    url: example.com\n")

	active, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !active {
		t.Error("expected active=true")
	}

	vars, _ := GetEnvVariable(filepath.Join(dir, ".env"))
	if vars["OS_USER"] != "customuser" {
		t.Errorf("OS_USER = %q, want \"customuser\"", vars["OS_USER"])
	}
	if vars["OS_PASSWORD"] != "custompass" {
		t.Errorf("OS_PASSWORD = %q, want \"custompass\"", vars["OS_PASSWORD"])
	}
	if vars["OS_PASSWORD_HASH"] != "$2a$10$existinghash" {
		t.Errorf("OS_PASSWORD_HASH = %q, want \"$2a$10$existinghash\"", vars["OS_PASSWORD_HASH"])
	}
}

func TestRewriteCaddy_WithObservable(t *testing.T) {
	hash := "$2a$10$testhashvalue"
	envContent := "COMPOSE_PROFILES=observable\nOS_USER=admin\nOS_PASSWORD_HASH=" + hash + "\n"
	dir := setupTestInstall(t, envContent, "wikis:\n  - id: main\n    url: example.com\n")

	if err := RewriteCaddy(dir); err != nil {
		t.Fatalf("RewriteCaddy() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "config", "Caddyfile"))
	if err != nil {
		t.Fatalf("failed to read Caddyfile: %v", err)
	}
	caddy := string(content)

	// Should contain opensearch handle block
	if !strings.Contains(caddy, "handle /opensearch/*") {
		t.Error("Caddyfile should contain 'handle /opensearch/*'")
	}
	if !strings.Contains(caddy, "basicauth") {
		t.Error("Caddyfile should contain 'basicauth'")
	}
	if !strings.Contains(caddy, "admin "+hash) {
		t.Errorf("Caddyfile should contain 'admin %s'", hash)
	}
	if !strings.Contains(caddy, "reverse_proxy opensearch-dashboards:5601") {
		t.Error("Caddyfile should contain 'reverse_proxy opensearch-dashboards:5601'")
	}
	// Varnish should be in a handle block
	if !strings.Contains(caddy, "handle {") {
		t.Error("Caddyfile should contain 'handle {' for varnish")
	}
	if !strings.Contains(caddy, "reverse_proxy varnish:80") {
		t.Error("Caddyfile should contain 'reverse_proxy varnish:80'")
	}
}

func TestRewriteCaddy_WithoutObservable(t *testing.T) {
	envContent := "COMPOSE_PROFILES=web\n"
	dir := setupTestInstall(t, envContent, "wikis:\n  - id: main\n    url: example.com\n")

	if err := RewriteCaddy(dir); err != nil {
		t.Fatalf("RewriteCaddy() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "config", "Caddyfile"))
	if err != nil {
		t.Fatalf("failed to read Caddyfile: %v", err)
	}
	caddy := string(content)

	// Should NOT contain opensearch references
	if strings.Contains(caddy, "opensearch") {
		t.Error("Caddyfile should not contain opensearch references when profile is not active")
	}
	if strings.Contains(caddy, "basicauth") {
		t.Error("Caddyfile should not contain basicauth when profile is not active")
	}
	// Should have bare reverse_proxy (not wrapped in handle)
	if strings.Contains(caddy, "handle {") {
		t.Error("Caddyfile should not use handle blocks when observable is not active")
	}
	if !strings.Contains(caddy, "reverse_proxy varnish:80") {
		t.Error("Caddyfile should contain 'reverse_proxy varnish:80'")
	}
}
