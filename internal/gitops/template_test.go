package gitops

import (
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v2"
)

func TestRenderTemplate(t *testing.T) {
	tmpl := `MW_SITE_SERVER=https://{{domain}}
MW_SITE_FQDN={{domain}}
MYSQL_PASSWORD={{mysql_password}}
SOME_LITERAL=hello`

	vars := VarsMap{
		"domain":         "wiki.example.com",
		"mysql_password": "secret123",
	}

	result, err := RenderTemplate(tmpl, vars)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	expected := `MW_SITE_SERVER=https://wiki.example.com
MW_SITE_FQDN=wiki.example.com
MYSQL_PASSWORD=secret123
SOME_LITERAL=hello`

	if result != expected {
		t.Errorf("got:\n%s\nwant:\n%s", result, expected)
	}
}

func TestRenderTemplateMissingKeys(t *testing.T) {
	tmpl := `A={{foo}}
B={{bar}}
C={{baz}}`

	vars := VarsMap{
		"foo": "1",
	}

	_, err := RenderTemplate(tmpl, vars)
	if err == nil {
		t.Fatal("expected error for missing keys")
	}

	errMsg := err.Error()
	if !strings.Contains(errMsg, "bar") || !strings.Contains(errMsg, "baz") {
		t.Errorf("error should list missing keys bar and baz, got: %s", errMsg)
	}
}

func TestExtractTemplate(t *testing.T) {
	env := `MW_SITE_SERVER=https://wiki.example.com
MW_SITE_FQDN=wiki.example.com
MYSQL_PASSWORD=secret123
SOME_LITERAL=hello
# A comment

HTTPS_PORT=443`

	placeholderKeys := []string{"MW_SITE_SERVER", "MW_SITE_FQDN", "MYSQL_PASSWORD", "HTTPS_PORT"}

	tmpl, vars := ExtractTemplate(env, placeholderKeys)

	// Check that the template has placeholders
	if !strings.Contains(tmpl, "{{mw_site_server}}") {
		t.Error("template should contain {{mw_site_server}}")
	}
	if !strings.Contains(tmpl, "{{mysql_password}}") {
		t.Error("template should contain {{mysql_password}}")
	}
	if !strings.Contains(tmpl, "SOME_LITERAL=hello") {
		t.Error("template should preserve literal values")
	}
	if !strings.Contains(tmpl, "# A comment") {
		t.Error("template should preserve comments")
	}

	// Check vars
	if vars["mw_site_server"] != "https://wiki.example.com" {
		t.Errorf("mw_site_server = %q, want %q", vars["mw_site_server"], "https://wiki.example.com")
	}
	if vars["mysql_password"] != "secret123" {
		t.Errorf("mysql_password = %q, want %q", vars["mysql_password"], "secret123")
	}
	if vars["https_port"] != "443" {
		t.Errorf("https_port = %q, want %q", vars["https_port"], "443")
	}
}

func TestExtractTemplateSecretPrefixes(t *testing.T) {
	// Test credentials are fake/example values from AWS documentation.
	//nolint:gosec
	env := `RESTIC_REPOSITORY=rest:http://repo.example/repo
RESTIC_PASSWORD=hunter2
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
B2_ACCOUNT_ID=abc123
SOME_LITERAL=hello`

	// Use AllPlaceholderKeys (which includes RESTIC_* as built-in secrets)
	// with no custom keys. AWS_*/B2_* are caught by prefix detection.
	tmpl, vars := ExtractTemplate(env, AllPlaceholderKeys(nil))

	// All secret keys should be replaced with placeholders.
	for _, key := range []string{"restic_repository", "restic_password",
		"aws_access_key_id", "aws_secret_access_key", "b2_account_id"} {
		if _, ok := vars[key]; !ok {
			t.Errorf("expected %s in vars", key)
		}
	}

	// Literal should remain.
	if strings.Contains(tmpl, "{{some_literal}}") {
		t.Error("SOME_LITERAL should not be a placeholder")
	}
	if !strings.Contains(tmpl, "SOME_LITERAL=hello") {
		t.Error("SOME_LITERAL should remain as a literal")
	}

	// Verify round-trip.
	rendered, err := RenderTemplate(tmpl, vars)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if rendered != env {
		t.Errorf("round-trip failed.\ngot:\n%s\nwant:\n%s", rendered, env)
	}
}

func TestExtractAndRenderRoundTrip(t *testing.T) {
	env := `MW_SITE_SERVER=https://wiki.example.com
MYSQL_PASSWORD=secret123
SOME_LITERAL=hello`

	placeholderKeys := []string{"MW_SITE_SERVER", "MYSQL_PASSWORD"}

	tmpl, vars := ExtractTemplate(env, placeholderKeys)

	rendered, err := RenderTemplate(tmpl, vars)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if rendered != env {
		t.Errorf("round-trip failed.\ngot:\n%s\nwant:\n%s", rendered, env)
	}
}

func TestAllPlaceholderKeys(t *testing.T) {
	custom := []string{"MY_API_KEY", "SMTP_PASSWORD"}
	keys := AllPlaceholderKeys(custom)

	expected := len(builtinSecretKeys) + len(builtinHostKeys) + 2
	if len(keys) != expected {
		t.Errorf("got %d keys, want %d", len(keys), expected)
	}
}

func TestExtractWikisTemplate(t *testing.T) {
	input := `wikis:
- id: wiki1
  url: example.com
  name: Main Wiki
- id: wiki2
  url: example.com/docs
  name: Documentation
`
	tmpl, vars, err := ExtractWikisTemplate(input)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Check that URLs were replaced with placeholders.
	if vars["wiki_url_wiki1"] != "example.com" {
		t.Errorf("wiki_url_wiki1 = %q, want %q", vars["wiki_url_wiki1"], "example.com")
	}
	if vars["wiki_url_wiki2"] != "example.com/docs" {
		t.Errorf("wiki_url_wiki2 = %q, want %q", vars["wiki_url_wiki2"], "example.com/docs")
	}

	// Template should contain placeholders, not literal URLs.
	if strings.Contains(tmpl, "example.com") && !strings.Contains(tmpl, "{{wiki_url_") {
		t.Error("template should contain placeholders instead of literal URLs")
	}

	// Round-trip: render the template back with the vars.
	rendered, err := RenderWikisTemplate(tmpl, vars)
	if err != nil {
		t.Fatalf("render error: %v", err)
	}

	// Parse both to compare semantically (YAML formatting may differ).
	var original, roundTripped wikisYAML
	if err := yaml.Unmarshal([]byte(input), &original); err != nil {
		t.Fatalf("parse original: %v", err)
	}
	if err := yaml.Unmarshal([]byte(rendered), &roundTripped); err != nil {
		t.Fatalf("parse rendered: %v", err)
	}

	if len(roundTripped.Wikis) != len(original.Wikis) {
		t.Fatalf("got %d wikis, want %d", len(roundTripped.Wikis), len(original.Wikis))
	}
	for i, w := range roundTripped.Wikis {
		if w.URL != original.Wikis[i].URL {
			t.Errorf("wiki[%d].URL = %q, want %q", i, w.URL, original.Wikis[i].URL)
		}
		if w.ID != original.Wikis[i].ID {
			t.Errorf("wiki[%d].ID = %q, want %q", i, w.ID, original.Wikis[i].ID)
		}
	}
}

func TestExtractWikisTemplateEmpty(t *testing.T) {
	// Empty wikis list should still work.
	input := "wikis: []\n"
	tmpl, vars, err := ExtractWikisTemplate(input)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(vars) != 0 {
		t.Errorf("expected no vars, got %d", len(vars))
	}
	if tmpl == "" {
		t.Error("expected non-empty template")
	}
}

func TestSyncWikisTemplate_AddsNewWiki(t *testing.T) {
	dir := t.TempDir()

	// Set up an existing template with one wiki.
	oldTemplate := "wikis:\n- id: wiki1\n  url: '{{wiki_url_wiki1}}'\n  name: Main Wiki\n"
	if err := SaveWikisTemplate(dir, oldTemplate); err != nil {
		t.Fatal(err)
	}

	// Set up host config.
	if err := SaveLocalHost(dir, "server1"); err != nil {
		t.Fatal(err)
	}
	oldVars := VarsMap{"wiki_url_wiki1": "example.com", "mysql_password": "secret"}
	if err := SaveVars(dir, "server1", oldVars); err != nil {
		t.Fatal(err)
	}

	// Write an updated wikis.yaml with two wikis.
	wikisContent := "wikis:\n- id: wiki1\n  url: example.com\n  name: Main Wiki\n- id: wiki2\n  url: example.com/docs\n  name: Docs\n"
	if err := saveWikisYAML(dir, wikisContent); err != nil {
		t.Fatal(err)
	}

	if err := SyncWikisTemplate(dir); err != nil {
		t.Fatalf("SyncWikisTemplate: %v", err)
	}

	// Verify template was updated.
	tmpl, err := LoadWikisTemplate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(tmpl, "{{wiki_url_wiki2}}") {
		t.Error("template should contain {{wiki_url_wiki2}}")
	}

	// Verify vars were updated.
	vars, err := LoadVars(dir, "server1")
	if err != nil {
		t.Fatal(err)
	}
	if vars["wiki_url_wiki2"] != "example.com/docs" {
		t.Errorf("wiki_url_wiki2 = %q, want %q", vars["wiki_url_wiki2"], "example.com/docs")
	}
	// Existing non-wiki vars should be preserved.
	if vars["mysql_password"] != "secret" {
		t.Errorf("mysql_password = %q, want %q", vars["mysql_password"], "secret")
	}
}

func TestSyncWikisTemplate_RemovesWiki(t *testing.T) {
	dir := t.TempDir()

	// Set up template with two wikis.
	oldTemplate := "wikis:\n- id: wiki1\n  url: '{{wiki_url_wiki1}}'\n  name: Main\n- id: wiki2\n  url: '{{wiki_url_wiki2}}'\n  name: Docs\n"
	if err := SaveWikisTemplate(dir, oldTemplate); err != nil {
		t.Fatal(err)
	}

	if err := SaveLocalHost(dir, "server1"); err != nil {
		t.Fatal(err)
	}
	oldVars := VarsMap{"wiki_url_wiki1": "example.com", "wiki_url_wiki2": "example.com/docs", "mysql_password": "secret"}
	if err := SaveVars(dir, "server1", oldVars); err != nil {
		t.Fatal(err)
	}

	// Write wikis.yaml with only one wiki (wiki2 removed).
	wikisContent := "wikis:\n- id: wiki1\n  url: example.com\n  name: Main\n"
	if err := saveWikisYAML(dir, wikisContent); err != nil {
		t.Fatal(err)
	}

	if err := SyncWikisTemplate(dir); err != nil {
		t.Fatalf("SyncWikisTemplate: %v", err)
	}

	// Verify wiki2 var was removed.
	vars, err := LoadVars(dir, "server1")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := vars["wiki_url_wiki2"]; ok {
		t.Error("wiki_url_wiki2 should have been removed from vars")
	}
	if vars["wiki_url_wiki1"] != "example.com" {
		t.Errorf("wiki_url_wiki1 = %q, want %q", vars["wiki_url_wiki1"], "example.com")
	}
	if vars["mysql_password"] != "secret" {
		t.Errorf("mysql_password should be preserved")
	}
}

func TestSyncWikisTemplate_NoopWithoutGitops(t *testing.T) {
	dir := t.TempDir()

	// No wikis.yaml.template — SyncWikisTemplate should be a no-op.
	err := SyncWikisTemplate(dir)
	if err != nil {
		t.Fatalf("expected no error when gitops is not active, got: %v", err)
	}
}

// saveWikisYAML is a test helper that writes config/wikis.yaml.
func saveWikisYAML(installPath, content string) error {
	dir := installPath + "/config"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	return os.WriteFile(dir+"/wikis.yaml", []byte(content), 0o644)
}

func TestFindMissingCustomKeys(t *testing.T) {
	vars := VarsMap{"my_api_key": "val1", "smtp_password": "val2"}

	// All present.
	missing := FindMissingCustomKeys([]string{"MY_API_KEY", "SMTP_PASSWORD"}, vars)
	if len(missing) != 0 {
		t.Errorf("expected no missing keys, got %v", missing)
	}

	// One missing.
	missing = FindMissingCustomKeys([]string{"MY_API_KEY", "NONEXISTENT"}, vars)
	if len(missing) != 1 || missing[0] != "NONEXISTENT" {
		t.Errorf("expected [NONEXISTENT], got %v", missing)
	}

	// Empty custom keys.
	missing = FindMissingCustomKeys(nil, vars)
	if len(missing) != 0 {
		t.Errorf("expected no missing keys for nil input, got %v", missing)
	}
}
