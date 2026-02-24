package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

func TestResolveKey(t *testing.T) {
	envVars := map[string]string{
		"HTTPS_PORT":  "443",
		"HTTP_PORT":   "80",
		"MYSQL_PASSWORD": "secret",
	}

	tests := []struct {
		input string
		want  string
	}{
		{"https_port", "HTTPS_PORT"},
		{"HTTPS_PORT", "HTTPS_PORT"},
		{"Https_Port", "HTTPS_PORT"},
		{"http_port", "HTTP_PORT"},
		{"MISSING_KEY", "MISSING_KEY"},
		{"missing_key", "MISSING_KEY"},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := resolveKey(envVars, tt.input)
			if got != tt.want {
				t.Errorf("resolveKey(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestUpdateURLPort(t *testing.T) {
	tests := []struct {
		name    string
		domain  string
		newPort string
		want    string
	}{
		{"standard port strips suffix", "example.com:8443", "443", "example.com"},
		{"non-standard adds port", "example.com", "8443", "example.com:8443"},
		{"replaces existing port", "example.com:9443", "8443", "example.com:8443"},
		{"preserves path", "example.com:8443/wiki", "443", "example.com/wiki"},
		{"preserves path with new port", "example.com/wiki", "8443", "example.com:8443/wiki"},
		{"no port to standard", "example.com", "443", "example.com"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := updateURLPort(tt.domain, tt.newPort)
			if got != tt.want {
				t.Errorf("updateURLPort(%q, %q) = %q, want %q", tt.domain, tt.newPort, got, tt.want)
			}
		})
	}
}

func TestUpdateSiteServerPort(t *testing.T) {
	tests := []struct {
		name       string
		siteServer string
		newPort    string
		want       string
	}{
		{"standard port", "https://example.com:8443", "443", "https://example.com"},
		{"non-standard port", "https://example.com", "8443", "https://example.com:8443"},
		{"replace port", "https://example.com:9443", "8443", "https://example.com:8443"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := updateSiteServerPort(tt.siteServer, tt.newPort)
			if got != tt.want {
				t.Errorf("updateSiteServerPort(%q, %q) = %q, want %q", tt.siteServer, tt.newPort, got, tt.want)
			}
		})
	}
}

func TestValidatePortChange_KindBlocked(t *testing.T) {
	inst := config.Installation{
		KindCluster: "test-cluster",
	}
	err := validatePortChange(inst, "8443")
	if err == nil {
		t.Fatal("expected error for kind-managed instance, got nil")
	}
	if !strings.Contains(err.Error(), "kind-managed") {
		t.Errorf("expected error about kind-managed, got: %s", err)
	}
}

func TestValidatePortChange_Allowed(t *testing.T) {
	inst := config.Installation{}
	err := validatePortChange(inst, "8443")
	if err != nil {
		t.Errorf("expected no error, got: %s", err)
	}
}

func TestGenerateTemplate(t *testing.T) {
	if envTemplate == "" {
		t.Fatal("embedded template is empty")
	}

	expectedSections := []string{
		"Network",
		"Database",
		"MediaWiki",
		"PHP",
		"Features",
		"Caddy",
		"Docker Image",
		"Backup",
	}

	for _, section := range expectedSections {
		if !strings.Contains(envTemplate, section) {
			t.Errorf("template missing expected section %q", section)
		}
	}

	// Should contain key settings
	for _, key := range []string{"HTTP_PORT", "HTTPS_PORT", "MYSQL_PASSWORD", "MW_SITE_SERVER"} {
		if !strings.Contains(envTemplate, key) {
			t.Errorf("template missing expected key %q", key)
		}
	}
}

func TestUpdateWikisYamlPorts(t *testing.T) {
	// Create a temp dir with a wikis.yaml and .env
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, "config")
	os.MkdirAll(configDir, 0755)

	wikisYaml := `wikis:
- id: main
  url: example.com:8443
  name: Main Wiki
- id: dev
  url: dev.example.com:8443/wiki
  name: Dev Wiki
`
	os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte(wikisYaml), 0644)

	envContent := "MW_SITE_SERVER=https://example.com:8443\nMW_SITE_FQDN=example.com:8443\n"
	os.WriteFile(filepath.Join(tmpDir, ".env"), []byte(envContent), 0644)

	inst := config.Installation{Path: tmpDir}

	// Change to standard port
	err := applyHTTPSPortChange(inst, "443")
	if err != nil {
		t.Fatalf("applyHTTPSPortChange failed: %v", err)
	}

	// Check wikis.yaml
	data, _ := os.ReadFile(filepath.Join(configDir, "wikis.yaml"))
	content := string(data)
	if strings.Contains(content, ":443") {
		t.Errorf("wikis.yaml should not contain :443 for standard port, got:\n%s", content)
	}
	if !strings.Contains(content, "url: example.com") {
		t.Errorf("expected 'url: example.com', got:\n%s", content)
	}
	if !strings.Contains(content, "url: dev.example.com/wiki") {
		t.Errorf("expected 'url: dev.example.com/wiki', got:\n%s", content)
	}

	// Check .env
	envData, _ := os.ReadFile(filepath.Join(tmpDir, ".env"))
	envStr := string(envData)
	if !strings.Contains(envStr, "MW_SITE_SERVER=https://example.com") {
		t.Errorf("expected MW_SITE_SERVER without port, got:\n%s", envStr)
	}
	if strings.Contains(envStr, "MW_SITE_SERVER=https://example.com:") {
		t.Errorf("MW_SITE_SERVER should not have port suffix for 443, got:\n%s", envStr)
	}
	if !strings.Contains(envStr, "MW_SITE_FQDN=example.com") {
		t.Errorf("expected MW_SITE_FQDN without port, got:\n%s", envStr)
	}

	// Now change to non-standard port
	err = applyHTTPSPortChange(inst, "9443")
	if err != nil {
		t.Fatalf("applyHTTPSPortChange failed: %v", err)
	}

	data, _ = os.ReadFile(filepath.Join(configDir, "wikis.yaml"))
	content = string(data)
	if !strings.Contains(content, "example.com:9443") {
		t.Errorf("expected port 9443 in wikis.yaml, got:\n%s", content)
	}
	if !strings.Contains(content, "dev.example.com:9443/wiki") {
		t.Errorf("expected dev.example.com:9443/wiki, got:\n%s", content)
	}

	envData, _ = os.ReadFile(filepath.Join(tmpDir, ".env"))
	envStr = string(envData)
	if !strings.Contains(envStr, "MW_SITE_SERVER=https://example.com:9443") {
		t.Errorf("expected MW_SITE_SERVER with port 9443, got:\n%s", envStr)
	}
	if !strings.Contains(envStr, "MW_SITE_FQDN=example.com:9443") {
		t.Errorf("expected MW_SITE_FQDN with port 9443, got:\n%s", envStr)
	}
}
