package canasta

import (
	"bytes"
	"encoding/hex"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"golang.org/x/crypto/bcrypt"
)

func TestGetEnvVariable(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")

	content := "KEY1=value1\n# this is a comment\n# COMMENTED_OUT=hidden\nKEY2=value2\n\nEMPTY=\nKEY_WITH_EQUALS=abc=def\nKEY=a=b\nQUOTED=\"hello world\"\n"
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
		{"EMPTY", ""},
		{"KEY_WITH_EQUALS", "abc=def"},
		{"KEY", "a=b"},
		{"QUOTED", "hello world"},
	}

	for _, tt := range tests {
		if got := vars[tt.key]; got != tt.want {
			t.Errorf("GetEnvVariable()[%s] = %q, want %q", tt.key, got, tt.want)
		}
	}

	// Comments with = signs should not be parsed as key-value pairs
	if _, ok := vars["# COMMENTED_OUT"]; ok {
		t.Error("comment line with = sign should not be parsed as key-value pair")
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

func TestDeleteEnvVariable(t *testing.T) {
	tests := []struct {
		name      string
		content   string
		key       string
		wantErr   bool
		assertMap func(t *testing.T, vars map[string]string)
	}{
		{
			name:    "remove existing key from multi-key file",
			content: "KEY1=value1\nKEY2=value2\nKEY3=value3\n",
			key:     "KEY2",
			wantErr: false,
			assertMap: func(t *testing.T, vars map[string]string) {
				if _, exists := vars["KEY2"]; exists {
					t.Errorf("expected KEY2 to be deleted, but still present")
				}
				if vars["KEY1"] != "value1" {
					t.Errorf("KEY1 = %q, want \"value1\"", vars["KEY1"])
				}
				if vars["KEY3"] != "value3" {
					t.Errorf("KEY3 = %q, want \"value3\"", vars["KEY3"])
				}
			},
		},
		{
			name:    "remove non-existent key returns error",
			content: "KEY1=value1\n",
			key:     "MISSING",
			wantErr: true,
			assertMap: func(t *testing.T, vars map[string]string) {
				if vars["KEY1"] != "value1" {
					t.Errorf("KEY1 = %q, want \"value1\"", vars["KEY1"])
				}
			},
		},
		{
			name:    "remove only key leaves empty file",
			content: "KEY1=value1\n",
			key:     "KEY1",
			wantErr: false,
			assertMap: func(t *testing.T, vars map[string]string) {
				if len(vars) != 0 {
					t.Errorf("expected empty map after deleting only key, got %v", vars)
				}
			},
		},
		{
			name:    "remove key does not match key with same prefix",
			content: "KEY=value\nKEY1=value1\nKEY2=value2\n",
			key:     "KEY",
			wantErr: false,
			assertMap: func(t *testing.T, vars map[string]string) {
				if _, exists := vars["KEY"]; exists {
					t.Errorf("expected KEY to be deleted, but still present")
				}
				if vars["KEY1"] != "value1" {
					t.Errorf("KEY1 = %q, want \"value1\"", vars["KEY1"])
				}
				if vars["KEY2"] != "value2" {
					t.Errorf("KEY2 = %q, want \"value2\"", vars["KEY2"])
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			envPath := filepath.Join(dir, ".env")

			if err := os.WriteFile(envPath, []byte(tt.content), 0644); err != nil {
				t.Fatal(err)
			}

			err := DeleteEnvVariable(envPath, tt.key)
			if (err != nil) != tt.wantErr {
				t.Fatalf("DeleteEnvVariable() error = %v, wantErr %v", err, tt.wantErr)
			}

			vars, err := GetEnvVariable(envPath)
			if err != nil {
				t.Fatalf("GetEnvVariable() error = %v", err)
			}

			tt.assertMap(t, vars)
		})
	}
}

func TestGenerateAdminAndDBPasswords(t *testing.T) {
	info := CanastaVariables{
		ID:        "test",
		AdminName: "admin",
	}

	result, err := GenerateAdminPassword(info)
	if err != nil {
		t.Fatalf("GenerateAdminPassword() error = %v", err)
	}

	result, err = GenerateDBPasswords(result)
	if err != nil {
		t.Fatalf("GenerateDBPasswords() error = %v", err)
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

		digits := 0
		for _, c := range pw {
			if c >= '0' && c <= '9' {
				digits++
			}
		}

		symbols := 0
		for _, c := range pw {
			if strings.ContainsRune("@%^-_+.,:", c) {
				symbols++
			}
		}

		if digits < 4 {
			t.Errorf("password has %d digits, want >= 4: %s", digits, pw)
		}
		if symbols < 6 {
			t.Errorf("password has %d symbols, want >= 6: %s", symbols, pw)
		}
	}
}

func TestGeneratePasswordsPreserveExisting(t *testing.T) {
	info := CanastaVariables{
		ID:             "test",
		AdminName:      "admin",
		AdminPassword:  "myadminpass",
		RootDBPassword: "myrootpass",
		WikiDBPassword: "mywikipass",
	}

	result, err := GenerateAdminPassword(info)
	if err != nil {
		t.Fatalf("GenerateAdminPassword() error = %v", err)
	}

	result, err = GenerateDBPasswords(result)
	if err != nil {
		t.Fatalf("GenerateDBPasswords() error = %v", err)
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

func TestGenerateAndSaveSecretKey_AlreadySet(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	existingKey := "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
	content := []byte("KEY1=value1\nMW_SECRET_KEY=" + existingKey + "\n")
	if err := os.WriteFile(envPath, content, 0644); err != nil {
		t.Fatal(err)
	}

	err := GenerateAndSaveSecretKey(dir)
	if err != nil {
		t.Fatalf("GenerateAndSaveSecretKey() error = %v", err)
	}

	newContent, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatalf("Failed to read .env file: %v", err)
	}

	if !bytes.Equal(content, newContent) {
		t.Errorf("expected .env file to be unmodified. Got:\n%s\nWant:\n%s", newContent, content)
	}
}

func TestGenerateAndSaveSecretKey_GeneratesNew(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	content := "KEY1=value1\n"
	if err := os.WriteFile(envPath, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	err := GenerateAndSaveSecretKey(dir)
	if err != nil {
		t.Fatalf("GenerateAndSaveSecretKey() error = %v", err)
	}

	vars, err := GetEnvVariable(envPath)
	if err != nil {
		t.Fatalf("GetEnvVariable() error = %v", err)
	}

	key := vars["MW_SECRET_KEY"]
	if key == "" {
		t.Fatal("expected MW_SECRET_KEY to be set in .env")
	}
	if len(key) != 64 {
		t.Errorf("expected 64-character MW_SECRET_KEY, got length %d", len(key))
	}
	if _, err := hex.DecodeString(key); err != nil {
		t.Errorf("expected MW_SECRET_KEY to be valid hex, got error: %v", err)
	}
}

func TestGenerateAndSaveSecretKey_MissingEnv(t *testing.T) {
	dir := t.TempDir()

	err := GenerateAndSaveSecretKey(dir)
	if err == nil {
		t.Fatal("expected error when .env file does not exist")
	}
}

func TestContainsProfile(t *testing.T) {
	tests := []struct {
		profiles string
		target   string
		want     bool
	}{
		{"web", "web", true},
		{"web,observable", "observable", true},
		{"web, observable", "observable", true},
		{"web", "observable", false},
		{"", "observable", false},
		{"observable", "observable", true},
	}
	for _, tt := range tests {
		if got := ContainsProfile(tt.profiles, tt.target); got != tt.want {
			t.Errorf("ContainsProfile(%q, %q) = %v, want %v", tt.profiles, tt.target, got, tt.want)
		}
	}
}

func TestRemoveDuplicates(t *testing.T) {
	tests := []struct {
		input []string
		want  []string
	}{
		{[]string{"a", "b", "a", "c"}, []string{"a", "b", "c"}},
		{[]string{"a", "b", "c"}, []string{"a", "b", "c"}},
		{[]string{}, []string{}},
		{[]string{"a"}, []string{"a"}},
		{[]string{"x", "x", "x"}, []string{"x"}},
	}
	for _, tt := range tests {
		if got := removeDuplicates(tt.input); !reflect.DeepEqual(got, tt.want) {
			t.Errorf("removeDuplicates(%v) = %v, want %v", tt.input, got, tt.want)
		}
	}
}

func TestIsObservabilityEnabled(t *testing.T) {
	tests := []struct {
		name    string
		envVars map[string]string
		want    bool
	}{
		{"enabled", map[string]string{"CANASTA_ENABLE_OBSERVABILITY": "true"}, true},
		{"enabled uppercase", map[string]string{"CANASTA_ENABLE_OBSERVABILITY": "TRUE"}, true},
		{"enabled mixed case", map[string]string{"CANASTA_ENABLE_OBSERVABILITY": "True"}, true},
		{"disabled false", map[string]string{"CANASTA_ENABLE_OBSERVABILITY": "false"}, false},
		{"disabled empty", map[string]string{"CANASTA_ENABLE_OBSERVABILITY": ""}, false},
		{"missing key", map[string]string{}, false},
		{"other value", map[string]string{"CANASTA_ENABLE_OBSERVABILITY": "yes"}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsObservabilityEnabled(tt.envVars); got != tt.want {
				t.Errorf("IsObservabilityEnabled() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestEnsureObservabilityCredentials_NotObservable(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("COMPOSE_PROFILES=web\n"), 0644); err != nil {
		t.Fatal(err)
	}

	active, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if active {
		t.Error("expected false when observability is not enabled")
	}
}

func TestEnsureObservabilityCredentials_GeneratesCredentials(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("CANASTA_ENABLE_OBSERVABILITY=true\n"), 0644); err != nil {
		t.Fatal(err)
	}

	active, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !active {
		t.Error("expected true when observability is enabled")
	}

	vars, _ := GetEnvVariable(envPath)
	if vars["OS_USER"] != "admin" {
		t.Errorf("OS_USER = %q, want \"admin\"", vars["OS_USER"])
	}
	if vars["OS_PASSWORD"] == "" {
		t.Error("expected OS_PASSWORD to be set")
	}
	if vars["OS_PASSWORD_HASH"] == "" {
		t.Error("expected OS_PASSWORD_HASH to be set")
	}
}

func TestEnsureObservabilityCredentials_PreservesExisting(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("CANASTA_ENABLE_OBSERVABILITY=true\nOS_USER=myuser\nOS_PASSWORD=mypass\nOS_PASSWORD_HASH=$2a$10$fakehash\n"), 0644); err != nil {
		t.Fatal(err)
	}

	active, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !active {
		t.Error("expected true when observability is enabled")
	}

	vars, _ := GetEnvVariable(envPath)
	if vars["OS_USER"] != "myuser" {
		t.Errorf("OS_USER = %q, want \"myuser\"", vars["OS_USER"])
	}
	if vars["OS_PASSWORD"] != "mypass" {
		t.Errorf("OS_PASSWORD = %q, want \"mypass\"", vars["OS_PASSWORD"])
	}
	if vars["OS_PASSWORD_HASH"] != "$2a$10$fakehash" {
		t.Errorf("OS_PASSWORD_HASH = %q, want \"$2a$10$fakehash\"", vars["OS_PASSWORD_HASH"])
	}
}

func TestRewriteCaddy_Observable(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte("wikis:\n  - id: main\n    url: example.com\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("CANASTA_ENABLE_OBSERVABILITY=true\nOS_USER=admin\nOS_PASSWORD_HASH=$2a$10$testhash\n"), 0644); err != nil {
		t.Fatal(err)
	}

	if err := RewriteCaddy(dir); err != nil {
		t.Fatalf("RewriteCaddy() error = %v", err)
	}

	content, err := os.ReadFile(filepath.Join(configDir, "Caddyfile"))
	if err != nil {
		t.Fatalf("expected Caddyfile to be created: %v", err)
	}
	caddy := string(content)

	if !strings.Contains(caddy, "opensearch-dashboards:5601") {
		t.Error("Caddyfile should contain opensearch-dashboards reverse proxy")
	}
	if !strings.Contains(caddy, "basicauth") {
		t.Error("Caddyfile should contain basicauth block")
	}
	if !strings.Contains(caddy, "admin $2a$10$testhash") {
		t.Error("Caddyfile should contain OS credentials")
	}
	if !strings.Contains(caddy, "@opensearch path /opensearch /opensearch/*") {
		t.Error("Caddyfile should use @opensearch named path matcher")
	}
}

func TestRewriteCaddy_ObservableMissingCredentials(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), []byte("wikis:\n  - id: main\n    url: example.com\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("CANASTA_ENABLE_OBSERVABILITY=true\n"), 0644); err != nil {
		t.Fatal(err)
	}

	err := RewriteCaddy(dir)
	if err == nil {
		t.Fatal("expected error when observable credentials are missing")
	}
	if !strings.Contains(err.Error(), "observability is enabled but OS_USER or OS_PASSWORD_HASH is missing") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestEnsureObservabilityCredentials_ValidBcryptHash(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("CANASTA_ENABLE_OBSERVABILITY=true\n"), 0644); err != nil {
		t.Fatal(err)
	}

	_, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	vars, _ := GetEnvVariable(envPath)
	err = bcrypt.CompareHashAndPassword([]byte(vars["OS_PASSWORD_HASH"]), []byte(vars["OS_PASSWORD"]))
	if err != nil {
		t.Error("OS_PASSWORD_HASH should be a valid bcrypt hash of OS_PASSWORD")
	}
}

func TestEnsureObservabilityCredentials_PartialCredentials(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	// OS_USER set but OS_PASSWORD and OS_PASSWORD_HASH missing
	if err := os.WriteFile(envPath, []byte("CANASTA_ENABLE_OBSERVABILITY=true\nOS_USER=customuser\n"), 0644); err != nil {
		t.Fatal(err)
	}

	active, err := EnsureObservabilityCredentials(dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !active {
		t.Error("expected true when observability is enabled")
	}

	vars, _ := GetEnvVariable(envPath)
	if vars["OS_USER"] != "customuser" {
		t.Errorf("OS_USER = %q, want \"customuser\" (should preserve existing)", vars["OS_USER"])
	}
	if vars["OS_PASSWORD"] == "" {
		t.Error("expected OS_PASSWORD to be generated")
	}
	if vars["OS_PASSWORD_HASH"] == "" {
		t.Error("expected OS_PASSWORD_HASH to be generated")
	}
}

func TestNormalizeWikiID(t *testing.T) {
	tests := []struct {
		name string
		id   string
		want string
	}{
		{name: "spaces to underscores", id: "my wiki", want: "my_wiki"},
		{name: "strip non alphanumeric", id: "my@wiki!", want: "mywiki"},
		{name: "already normalized", id: "my_wiki", want: "my_wiki"},
		{name: "empty", id: "", want: ""},
		{name: "multiple spaces", id: "my  wiki  name", want: "my__wiki__name"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := NormalizeWikiID(tc.id); got != tc.want {
				t.Errorf("NormalizeWikiID(%q) = %q, want %q", tc.id, got, tc.want)
			}
		})
	}
}
