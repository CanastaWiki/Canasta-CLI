package orchestrators

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGetNamespaceFromPath(t *testing.T) {
	tests := []struct {
		name      string
		content   string
		wantNS    string
		wantErr   bool
	}{
		{
			name:    "standard namespace",
			content: "namespace: my-wiki\nresources:\n  - kubernetes/\n",
			wantNS:  "my-wiki",
			wantErr: false,
		},
		{
			name:    "namespace with extra spaces",
			content: "namespace:   test-ns  \nresources:\n",
			wantNS:  "test-ns",
			wantErr: false,
		},
		{
			name:    "namespace not first line",
			content: "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nnamespace: wiki-prod\n",
			wantNS:  "wiki-prod",
			wantErr: false,
		},
		{
			name:    "quoted namespace",
			content: "namespace: \"my-wiki\"\nresources:\n  - kubernetes/\n",
			wantNS:  "my-wiki",
			wantErr: false,
		},
		{
			name:    "missing namespace",
			content: "resources:\n  - kubernetes/\n",
			wantNS:  "",
			wantErr: true,
		},
		{
			name:    "empty namespace value",
			content: "namespace:\nresources:\n",
			wantNS:  "",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			err := os.WriteFile(filepath.Join(dir, "kustomization.yaml"), []byte(tt.content), 0644)
			if err != nil {
				t.Fatalf("failed to write test file: %v", err)
			}

			ns, err := getNamespaceFromPath(dir)
			if (err != nil) != tt.wantErr {
				t.Errorf("getNamespaceFromPath() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if ns != tt.wantNS {
				t.Errorf("getNamespaceFromPath() = %q, want %q", ns, tt.wantNS)
			}
		})
	}
}

func TestGetNamespaceFromPathMissingFile(t *testing.T) {
	dir := t.TempDir()
	_, err := getNamespaceFromPath(dir)
	if err == nil {
		t.Fatal("expected error for missing kustomization.yaml")
	}
}

func TestNewKubernetesReturnsOrchestrator(t *testing.T) {
	for _, id := range []string{"kubernetes", "k8s"} {
		orch, err := New(id)
		if err != nil {
			t.Fatalf("New(%q) unexpected error: %v", id, err)
		}
		if orch == nil {
			t.Fatalf("New(%q) returned nil orchestrator", id)
		}
		if _, ok := orch.(*KubernetesOrchestrator); !ok {
			t.Errorf("New(%q) returned %T, want *KubernetesOrchestrator", id, orch)
		}
	}
}

// expectedK8sManifests lists the manifest files that should be written by WriteStackFiles.
var expectedK8sManifests = []string{
	"namespace.yaml",
	"web.yaml",
	"db.yaml",
	"caddy.yaml",
	"varnish.yaml",
	"elasticsearch.yaml",
}

func TestWriteStackFilesCreatesManifests(t *testing.T) {
	dir := t.TempDir()
	k := &KubernetesOrchestrator{}
	if err := k.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles() error = %v", err)
	}

	for _, name := range expectedK8sManifests {
		path := filepath.Join(dir, "kubernetes", name)
		info, err := os.Stat(path)
		if err != nil {
			t.Errorf("expected %s to exist: %v", name, err)
			continue
		}
		if info.Size() == 0 {
			t.Errorf("%s is empty", name)
		}
	}
}

func TestWriteStackFilesNoClobber(t *testing.T) {
	dir := t.TempDir()
	k := &KubernetesOrchestrator{}

	// First write
	if err := k.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles() first call error = %v", err)
	}

	// Overwrite one file with custom content
	customPath := filepath.Join(dir, "kubernetes", "namespace.yaml")
	customContent := []byte("# custom content\n")
	if err := os.WriteFile(customPath, customContent, 0644); err != nil {
		t.Fatalf("failed to write custom content: %v", err)
	}

	// Second write should not clobber
	if err := k.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles() second call error = %v", err)
	}

	got, err := os.ReadFile(customPath)
	if err != nil {
		t.Fatalf("failed to read file: %v", err)
	}
	if string(got) != string(customContent) {
		t.Error("WriteStackFiles() should not overwrite existing files")
	}
}

func TestUpdateStackFilesDetectsChanges(t *testing.T) {
	dir := t.TempDir()
	k := &KubernetesOrchestrator{}

	// First write
	if err := k.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles() error = %v", err)
	}

	// No changes yet
	changed, err := k.UpdateStackFiles(dir, false)
	if err != nil {
		t.Fatalf("UpdateStackFiles() error = %v", err)
	}
	if changed {
		t.Error("UpdateStackFiles() returned changed=true when files are identical")
	}

	// Modify a file
	modifiedPath := filepath.Join(dir, "kubernetes", "namespace.yaml")
	if err := os.WriteFile(modifiedPath, []byte("# modified\n"), 0644); err != nil {
		t.Fatalf("failed to modify file: %v", err)
	}

	// Should detect change and update
	changed, err = k.UpdateStackFiles(dir, false)
	if err != nil {
		t.Fatalf("UpdateStackFiles() error = %v", err)
	}
	if !changed {
		t.Error("UpdateStackFiles() returned changed=false after modifying a file")
	}
}

func TestUpdateStackFilesDryRun(t *testing.T) {
	dir := t.TempDir()
	k := &KubernetesOrchestrator{}

	// First write
	if err := k.WriteStackFiles(dir); err != nil {
		t.Fatalf("WriteStackFiles() error = %v", err)
	}

	// Modify a file
	modifiedPath := filepath.Join(dir, "kubernetes", "namespace.yaml")
	if err := os.WriteFile(modifiedPath, []byte("# modified\n"), 0644); err != nil {
		t.Fatalf("failed to modify file: %v", err)
	}

	// Dry run should report changed but not modify
	changed, err := k.UpdateStackFiles(dir, true)
	if err != nil {
		t.Fatalf("UpdateStackFiles() error = %v", err)
	}
	if !changed {
		t.Error("UpdateStackFiles() dry run returned changed=false")
	}

	// File should still have modified content
	got, err := os.ReadFile(modifiedPath)
	if err != nil {
		t.Fatalf("failed to read file: %v", err)
	}
	if string(got) != "# modified\n" {
		t.Error("UpdateStackFiles() dry run should not modify files")
	}
}

func TestInitConfigGeneratesKustomization(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "my-wiki")
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatalf("failed to create dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("COMPOSE_PROFILES=web\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte("wikis:\n  - id: main\n    url: example.com\n"), 0644); err != nil {
		t.Fatal(err)
	}

	k := &KubernetesOrchestrator{}
	if err := k.InitConfig(dir); err != nil {
		t.Fatalf("InitConfig() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatalf("expected kustomization.yaml to be created: %v", err)
	}

	text := string(content)

	// Should contain the namespace from the directory name
	if !strings.Contains(text, "namespace: my-wiki") {
		t.Errorf("kustomization.yaml should contain 'namespace: my-wiki', got:\n%s", text)
	}

	// Should reference embedded manifest paths
	for _, resource := range []string{
		"kubernetes/namespace.yaml",
		"kubernetes/web.yaml",
		"kubernetes/db.yaml",
		"kubernetes/caddy.yaml",
		"kubernetes/varnish.yaml",
		"kubernetes/elasticsearch.yaml",
	} {
		if !strings.Contains(text, resource) {
			t.Errorf("kustomization.yaml should reference %s", resource)
		}
	}

	// Should reference .env (not .env.example)
	if !strings.Contains(text, "- .env") {
		t.Error("kustomization.yaml should reference .env")
	}
	if strings.Contains(text, ".env.example") {
		t.Error("kustomization.yaml should not reference .env.example")
	}

	// Should not contain backup references
	if strings.Contains(text, "backup") {
		t.Error("kustomization.yaml should not contain backup references")
	}
}

func TestInitConfigNamespaceFromPath(t *testing.T) {
	// Test that the namespace is derived from the directory name
	dir := filepath.Join(t.TempDir(), "production-wiki")
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatalf("failed to create dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("COMPOSE_PROFILES=web\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte("wikis:\n  - id: main\n    url: example.com\n"), 0644); err != nil {
		t.Fatal(err)
	}

	k := &KubernetesOrchestrator{}
	if err := k.InitConfig(dir); err != nil {
		t.Fatalf("InitConfig() error = %v", err)
	}

	// Verify the generated file can be read back by getNamespaceFromPath
	ns, err := getNamespaceFromPath(dir)
	if err != nil {
		t.Fatalf("getNamespaceFromPath() error = %v", err)
	}
	if ns != "production-wiki" {
		t.Errorf("namespace = %q, want %q", ns, "production-wiki")
	}
}

// setupTestInstallation creates a minimal installation directory for testing
// generateKustomization. Returns the install path.
func setupTestInstallation(t *testing.T, name string) string {
	t.Helper()
	dir := filepath.Join(t.TempDir(), name)
	configDir := filepath.Join(dir, "config")
	globalDir := filepath.Join(configDir, "settings", "global")
	if err := os.MkdirAll(globalDir, 0755); err != nil {
		t.Fatalf("failed to create dirs: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("MW_SITE_SERVER=https://example.com\nMW_SITE_FQDN=example.com\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte("wikis:\n  - id: main\n    url: example.com\n"), 0644); err != nil {
		t.Fatal(err)
	}
	// Create default global settings files
	if err := os.WriteFile(filepath.Join(globalDir, "Vector.php"), []byte("<?php\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(globalDir, "CanastaFooterIcon.php"), []byte("<?php\n"), 0644); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestGenerateKustomizationGlobalSettings(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, false); err != nil {
		t.Fatalf("generateKustomization() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatalf("failed to read kustomization.yaml: %v", err)
	}
	text := string(content)

	// Should have the auto-generated header
	if !strings.Contains(text, "Auto-generated by Canasta CLI") {
		t.Error("missing auto-generated header")
	}

	// Should contain canasta-settings-global ConfigMap with both files
	if !strings.Contains(text, "canasta-settings-global") {
		t.Error("missing canasta-settings-global ConfigMap")
	}
	if !strings.Contains(text, "Vector.php=config/settings/global/Vector.php") {
		t.Error("missing Vector.php in global settings")
	}
	if !strings.Contains(text, "CanastaFooterIcon.php=config/settings/global/CanastaFooterIcon.php") {
		t.Error("missing CanastaFooterIcon.php in global settings")
	}

	// Should be valid YAML parseable by getNamespaceFromPath
	ns, err := getNamespaceFromPath(dir)
	if err != nil {
		t.Fatalf("getNamespaceFromPath() error = %v", err)
	}
	if ns != "test-wiki" {
		t.Errorf("namespace = %q, want %q", ns, "test-wiki")
	}
}

func TestGenerateKustomizationCustomGlobalFile(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	// Add a custom PHP file
	globalDir := filepath.Join(dir, "config", "settings", "global")
	if err := os.WriteFile(filepath.Join(globalDir, "Custom.php"), []byte("<?php\n"), 0644); err != nil {
		t.Fatal(err)
	}
	// Add a README that should be excluded
	if err := os.WriteFile(filepath.Join(globalDir, "README"), []byte("readme\n"), 0644); err != nil {
		t.Fatal(err)
	}

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, false); err != nil {
		t.Fatalf("generateKustomization() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)

	if !strings.Contains(text, "Custom.php=config/settings/global/Custom.php") {
		t.Error("custom PHP file should be included in global settings")
	}
	if strings.Contains(text, "README=") {
		t.Error("README should be excluded from ConfigMap")
	}
}

func TestGenerateKustomizationPerWikiSettings(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	// Create per-wiki settings directory with a file
	wikiDir := filepath.Join(dir, "config", "settings", "wikis", "main")
	if err := os.MkdirAll(wikiDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(wikiDir, "Settings.php"), []byte("<?php\n"), 0644); err != nil {
		t.Fatal(err)
	}

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, false); err != nil {
		t.Fatalf("generateKustomization() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)

	// Should have per-wiki ConfigMap
	if !strings.Contains(text, "canasta-settings-wiki-main") {
		t.Error("missing per-wiki ConfigMap for 'main'")
	}
	if !strings.Contains(text, "Settings.php=config/settings/wikis/main/Settings.php") {
		t.Error("missing Settings.php in per-wiki ConfigMap")
	}

	// Should have a strategic merge patch for the wiki volume
	if !strings.Contains(text, "mountPath: /mediawiki/config/settings/wikis/main") {
		t.Error("missing volumeMount patch for per-wiki settings")
	}
}

func TestGenerateKustomizationEmptyWikiDir(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	// Create wiki settings directory with only a README (should be excluded)
	wikiDir := filepath.Join(dir, "config", "settings", "wikis", "main")
	if err := os.MkdirAll(wikiDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(wikiDir, "README"), []byte("readme\n"), 0644); err != nil {
		t.Fatal(err)
	}

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, false); err != nil {
		t.Fatalf("generateKustomization() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)

	// Should NOT have a per-wiki ConfigMap when only README exists
	if strings.Contains(text, "canasta-settings-wiki-main") {
		t.Error("empty wiki settings dir (only README) should not produce a ConfigMap")
	}
}

func TestGenerateKustomizationLocalSettings(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	// Create LocalSettings.php
	if err := os.WriteFile(filepath.Join(dir, "config", "LocalSettings.php"), []byte("<?php\n"), 0644); err != nil {
		t.Fatal(err)
	}

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, false); err != nil {
		t.Fatalf("generateKustomization() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)

	// Should include LocalSettings.php in canasta-config
	if !strings.Contains(text, "config/LocalSettings.php") {
		t.Error("LocalSettings.php should be included in canasta-config")
	}

	// Should have a patch for the LocalSettings.php volumeMount
	if !strings.Contains(text, "mountPath: /mediawiki/config/LocalSettings.php") {
		t.Error("missing volumeMount patch for LocalSettings.php")
	}
}

func TestGenerateKustomizationNoLocalSettings(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, false); err != nil {
		t.Fatalf("generateKustomization() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)

	// Should NOT include LocalSettings.php when file doesn't exist
	if strings.Contains(text, "LocalSettings.php") {
		t.Error("LocalSettings.php should not be present when file doesn't exist")
	}
}

func TestGenerateKustomizationNodePort(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, true); err != nil {
		t.Fatalf("generateKustomization(localCluster=true) error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)

	if !strings.Contains(text, "type: NodePort") {
		t.Error("NodePort patch should be present when localCluster=true")
	}
	if !strings.Contains(text, "nodePort: 30080") {
		t.Error("NodePort 30080 should be configured")
	}
}

func TestGenerateKustomizationNoNodePort(t *testing.T) {
	dir := setupTestInstallation(t, "test-wiki")

	k := &KubernetesOrchestrator{}
	if err := k.generateKustomization(dir, false); err != nil {
		t.Fatalf("generateKustomization(localCluster=false) error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, "kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)

	if strings.Contains(text, "NodePort") {
		t.Error("NodePort patch should not be present when localCluster=false")
	}
}

func TestRunBackupNotSupported(t *testing.T) {
	k := &KubernetesOrchestrator{}
	_, err := k.RunBackup("/tmp", "/tmp/.env", nil)
	if err == nil {
		t.Fatal("expected error from RunBackup")
	}
	if !strings.Contains(err.Error(), "not yet supported") {
		t.Errorf("RunBackup() error = %q, want 'not yet supported'", err.Error())
	}
}

func TestRestoreFromBackupVolumeNotSupported(t *testing.T) {
	k := &KubernetesOrchestrator{}
	err := k.RestoreFromBackupVolume("/tmp", nil)
	if err == nil {
		t.Fatal("expected error from RestoreFromBackupVolume")
	}
	if !strings.Contains(err.Error(), "not yet supported") {
		t.Errorf("RestoreFromBackupVolume() error = %q, want 'not yet supported'", err.Error())
	}
}
