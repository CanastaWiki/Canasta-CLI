package orchestrators

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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

func TestComposeInitConfig(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web\n", "wikis:\n  - id: main\n    url: example.com\n")

	c := &ComposeOrchestrator{}
	if err := c.InitConfig(dir); err != nil {
		t.Fatalf("InitConfig() error = %v", err)
	}

	// Caddyfile.site should exist
	if _, err := os.Stat(filepath.Join(dir, "config", "Caddyfile.site")); err != nil {
		t.Error("expected Caddyfile.site to be created")
	}

	// Caddyfile.global should exist
	if _, err := os.Stat(filepath.Join(dir, "config", "Caddyfile.global")); err != nil {
		t.Error("expected Caddyfile.global to be created")
	}

	// Caddyfile should exist and contain the site address
	content, err := os.ReadFile(filepath.Join(dir, "config", "Caddyfile"))
	if err != nil {
		t.Fatalf("expected Caddyfile to be created: %v", err)
	}
	if !strings.Contains(string(content), "example.com") {
		t.Error("Caddyfile should contain server name")
	}
}

func TestComposeUpdateConfig(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web\n", "wikis:\n  - id: main\n    url: example.com\n  - id: wiki2\n    url: wiki2.example.com\n")

	c := &ComposeOrchestrator{}
	if err := c.UpdateConfig(dir); err != nil {
		t.Fatalf("UpdateConfig() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "config", "Caddyfile"))
	if err != nil {
		t.Fatalf("expected Caddyfile to be created: %v", err)
	}
	caddy := string(content)
	if !strings.Contains(caddy, "example.com") {
		t.Error("Caddyfile should contain example.com")
	}
	if !strings.Contains(caddy, "wiki2.example.com") {
		t.Error("Caddyfile should contain wiki2.example.com")
	}
}

func TestComposeMigrateConfig_NothingToMigrate(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web\n", "wikis:\n  - id: main\n    url: example.com\n")

	// Create both Caddyfiles so no migration is needed
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.site"), []byte("# site"), 0644)
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.global"), []byte("# global"), 0644)

	c := &ComposeOrchestrator{}
	changed, err := c.MigrateConfig(dir, false)
	if err != nil {
		t.Fatalf("MigrateConfig() error = %v", err)
	}
	if changed {
		t.Error("expected no changes when files already exist")
	}
}

func TestComposeMigrateConfig_CreatesCaddyfiles(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web\n", "wikis:\n  - id: main\n    url: example.com\n")

	c := &ComposeOrchestrator{}
	changed, err := c.MigrateConfig(dir, false)
	if err != nil {
		t.Fatalf("MigrateConfig() error = %v", err)
	}
	if !changed {
		t.Error("expected changes when Caddyfiles are missing")
	}

	if _, err := os.Stat(filepath.Join(dir, "config", "Caddyfile.site")); err != nil {
		t.Error("expected Caddyfile.site to be created")
	}
	if _, err := os.Stat(filepath.Join(dir, "config", "Caddyfile.global")); err != nil {
		t.Error("expected Caddyfile.global to be created")
	}
}

func TestComposeMigrateConfig_DryRun(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web\n", "wikis:\n  - id: main\n    url: example.com\n")

	c := &ComposeOrchestrator{}
	changed, err := c.MigrateConfig(dir, true)
	if err != nil {
		t.Fatalf("MigrateConfig() error = %v", err)
	}
	if !changed {
		t.Error("expected changes to be reported in dry-run")
	}

	// Files should NOT be created in dry-run
	if _, err := os.Stat(filepath.Join(dir, "config", "Caddyfile.site")); err == nil {
		t.Error("Caddyfile.site should not be created in dry-run")
	}
}

func TestComposeInitConfig_Observable(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web,observable\n", "wikis:\n  - id: main\n    url: example.com\n")

	c := &ComposeOrchestrator{}
	if err := c.InitConfig(dir); err != nil {
		t.Fatalf("InitConfig() error = %v", err)
	}

	// Should have generated observability credentials
	envPath := filepath.Join(dir, ".env")
	content, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatalf("failed to read .env: %v", err)
	}
	env := string(content)
	if !strings.Contains(env, "OS_USER=") {
		t.Error("expected OS_USER to be set in .env")
	}
	if !strings.Contains(env, "OS_PASSWORD=") {
		t.Error("expected OS_PASSWORD to be set in .env")
	}
	if !strings.Contains(env, "OS_PASSWORD_HASH=") {
		t.Error("expected OS_PASSWORD_HASH to be set in .env")
	}

	// Caddyfile should contain observable block
	caddy, err := os.ReadFile(filepath.Join(dir, "config", "Caddyfile"))
	if err != nil {
		t.Fatalf("expected Caddyfile to be created: %v", err)
	}
	if !strings.Contains(string(caddy), "opensearch-dashboards:5601") {
		t.Error("Caddyfile should contain opensearch-dashboards proxy")
	}
}

func TestComposeMigrateConfig_Observable(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web,observable\n", "wikis:\n  - id: main\n    url: example.com\n")

	// Create Caddyfiles so caddy migration doesn't trigger
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.site"), []byte("# site"), 0644)
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.global"), []byte("# global"), 0644)

	c := &ComposeOrchestrator{}
	changed, err := c.MigrateConfig(dir, false)
	if err != nil {
		t.Fatalf("MigrateConfig() error = %v", err)
	}
	if !changed {
		t.Error("expected changes when observability credentials are missing")
	}

	// Credentials should now be in .env
	content, _ := os.ReadFile(filepath.Join(dir, ".env"))
	env := string(content)
	if !strings.Contains(env, "OS_USER=") {
		t.Error("expected OS_USER to be set after migration")
	}
}

func TestComposeMigrateConfig_ObservableDryRun(t *testing.T) {
	dir := setupTestInstall(t, "COMPOSE_PROFILES=web,observable\n", "wikis:\n  - id: main\n    url: example.com\n")

	// Create Caddyfiles so caddy migration doesn't trigger
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.site"), []byte("# site"), 0644)
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.global"), []byte("# global"), 0644)

	c := &ComposeOrchestrator{}
	changed, err := c.MigrateConfig(dir, true)
	if err != nil {
		t.Fatalf("MigrateConfig() error = %v", err)
	}
	if !changed {
		t.Error("expected changes to be reported in dry-run")
	}

	// Credentials should NOT be generated in dry-run
	content, _ := os.ReadFile(filepath.Join(dir, ".env"))
	if strings.Contains(string(content), "OS_USER=") {
		t.Error("credentials should not be generated in dry-run")
	}
}

func TestComposeMigrateConfig_ObservableAlreadyConfigured(t *testing.T) {
	env := "COMPOSE_PROFILES=web,observable\nOS_USER=admin\nOS_PASSWORD=secret\nOS_PASSWORD_HASH=$2a$10$hash\n"
	dir := setupTestInstall(t, env, "wikis:\n  - id: main\n    url: example.com\n")

	// Create Caddyfiles so caddy migration doesn't trigger
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.site"), []byte("# site"), 0644)
	os.WriteFile(filepath.Join(dir, "config", "Caddyfile.global"), []byte("# global"), 0644)

	c := &ComposeOrchestrator{}
	changed, err := c.MigrateConfig(dir, false)
	if err != nil {
		t.Fatalf("MigrateConfig() error = %v", err)
	}
	if changed {
		t.Error("expected no changes when observability is already configured")
	}
}
