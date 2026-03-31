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

func TestSaveEnvVariableDeduplicates(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")

	// Start with a file that has duplicate keys (e.g., from prior raw appends)
	initial := "KEY1=first\nKEY2=keep\nKEY1=second\nKEY1=third\n"
	if err := os.WriteFile(envPath, []byte(initial), 0644); err != nil {
		t.Fatal(err)
	}

	// Save should update the first occurrence and remove duplicates
	if err := SaveEnvVariable(envPath, "KEY1", "updated"); err != nil {
		t.Fatalf("SaveEnvVariable() error = %v", err)
	}

	data, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatal(err)
	}
	count := strings.Count(string(data), "KEY1=")
	if count != 1 {
		t.Errorf("expected 1 occurrence of KEY1, got %d in:\n%s", count, data)
	}

	vars, err := GetEnvVariable(envPath)
	if err != nil {
		t.Fatalf("GetEnvVariable() error = %v", err)
	}
	if vars["KEY1"] != "updated" {
		t.Errorf("KEY1 = %q, want \"updated\"", vars["KEY1"])
	}
	if vars["KEY2"] != "keep" {
		t.Errorf("KEY2 = %q, want \"keep\"", vars["KEY2"])
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

func TestSavePasswordToFile(t *testing.T) {
	dir := t.TempDir()

	tests := []struct {
		name      string
		directory string
		filename  string
		password  string
		wantErr   bool
	}{
		{
			name:      "successful save",
			directory: dir,
			filename:  "success_pass.txt",
			password:  "secure123",
			wantErr:   false,
		},
		{
			name:      "nonexistent directory error",
			directory: filepath.Join(dir, "does_not_exist"),
			filename:  "fail_pass.txt",
			password:  "secure123",
			wantErr:   true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := SavePasswordToFile(tt.directory, tt.filename, tt.password)
			if (err != nil) != tt.wantErr {
				t.Fatalf("SavePasswordToFile() error = %v, wantErr %v", err, tt.wantErr)
			}

			if !tt.wantErr {
				filePath := filepath.Join(tt.directory, tt.filename)
				data, err := os.ReadFile(filePath)
				if err != nil {
					t.Fatalf("Failed to read saved password file: %v", err)
				}

				if string(data) != tt.password {
					t.Errorf("expected password file to contain %q, but got %q", tt.password, string(data))
				}

				info, err := os.Stat(filePath)
				if err != nil {
					t.Fatalf("Failed to stat saved password file: %v", err)
				}

				if info.Mode().Perm()&0002 != 0 {
					t.Errorf("expected file to not be world-writable, got permissions: %v", info.Mode().Perm())
				}
			}
		})
	}
}

func TestUpdateEnvFile(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}

	wikiYamlPath := filepath.Join(configDir, "wikis.yaml")
	wikiYamlContent := "wikis:\n  - id: main\n    url: example.com\n"
	if err := os.WriteFile(wikiYamlPath, []byte(wikiYamlContent), 0644); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name          string
		baseEnv       string
		customEnv     string
		customEnvName string
		argRootDB     string
		argWikiDB     string
		wantEnv       map[string]string
		wantErr       bool
	}{
		{
			name:          "merge custom env and arguments",
			baseEnv:       "EXISTING_KEY=base_value\nOVERRIDE_ME=old_value\n",
			customEnvName: "custom.env",
			customEnv:     "OVERRIDE_ME=new_value\nNEW_KEY=custom_value\nMYSQL_PASSWORD=custom_root_pass\nWIKI_DB_PASSWORD=custom_wiki_pass\n",
			argRootDB:     "arg_root_pass",
			argWikiDB:     "arg_wiki_pass",
			wantEnv: map[string]string{
				"EXISTING_KEY":   "base_value",
				"OVERRIDE_ME":    "new_value",
				"NEW_KEY":        "custom_value",
				"MW_SITE_SERVER": "https://example.com",
				"MW_SITE_FQDN":   "example.com",
				// Arguments have priority for database secrets
				"MYSQL_PASSWORD":   "arg_root_pass",
				"WIKI_DB_PASSWORD": "arg_wiki_pass",
			},
			wantErr: false,
		},
		{
			name:          "missing custom env file",
			baseEnv:       "EXISTING_KEY=base_value\n",
			customEnvName: "missing.env",
			customEnv:     "",
			argRootDB:     "root_pass",
			argWikiDB:     "wiki_pass",
			wantEnv:       nil,
			wantErr:       true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			subDir := t.TempDir()

			subConfig := filepath.Join(subDir, "config")
			if err := os.MkdirAll(subConfig, 0755); err != nil {
				t.Fatalf("Failed to create subConfig dir: %v", err)
			}
			if err := os.WriteFile(filepath.Join(subConfig, "wikis.yaml"), []byte(wikiYamlContent), 0644); err != nil {
				t.Fatalf("Failed to write wikis.yaml: %v", err)
			}

			baseEnvPath := filepath.Join(subDir, ".env")
			if err := os.WriteFile(baseEnvPath, []byte(tt.baseEnv), 0644); err != nil {
				t.Fatalf("Failed to write base .env: %v", err)
			}

			customPath := ""
			if tt.customEnvName != "" {
				customPath = filepath.Join(subDir, tt.customEnvName)
				if tt.customEnv != "" {
					if err := os.WriteFile(customPath, []byte(tt.customEnv), 0644); err != nil {
						t.Fatalf("Failed to write custom .env: %v", err)
					}
				}
			}

			err := UpdateEnvFile(customPath, subDir, tt.argRootDB, tt.argWikiDB)
			if (err != nil) != tt.wantErr {
				t.Fatalf("UpdateEnvFile() error = %v, wantErr %v", err, tt.wantErr)
			}

			if !tt.wantErr {
				vars, err := GetEnvVariable(baseEnvPath)
				if err != nil {
					t.Fatalf("GetEnvVariable() error: %v", err)
				}

				for k, wantV := range tt.wantEnv {
					if gotV := vars[k]; gotV != wantV {
						t.Errorf("env[%q] = %q, want %q", k, gotV, wantV)
					}
				}
			}
		})
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

func TestResolveFilePaths(t *testing.T) {
	base := "/home/user"

	t.Run("relative-becomes-absolute", func(t *testing.T) {
		p := "dump.sql"
		ResolveFilePaths(base, &p)
		want := filepath.Join(base, "dump.sql")
		if p != want {
			t.Errorf("got %q, want %q", p, want)
		}
	})

	t.Run("absolute-unchanged", func(t *testing.T) {
		p := "/tmp/dump.sql"
		ResolveFilePaths(base, &p)
		if p != "/tmp/dump.sql" {
			t.Errorf("got %q, want /tmp/dump.sql", p)
		}
	})

	t.Run("empty-unchanged", func(t *testing.T) {
		p := ""
		ResolveFilePaths(base, &p)
		if p != "" {
			t.Errorf("got %q, want empty", p)
		}
	})

	t.Run("multiple-paths", func(t *testing.T) {
		a, b, c := "file1.sql", "/abs/file2.sql", ""
		ResolveFilePaths(base, &a, &b, &c)
		if a != filepath.Join(base, "file1.sql") {
			t.Errorf("a: got %q", a)
		}
		if b != "/abs/file2.sql" {
			t.Errorf("b: got %q", b)
		}
		if c != "" {
			t.Errorf("c: got %q", c)
		}
	})
}

func TestUpdateInstanceTemplate_NoChanges(t *testing.T) {
	dir := t.TempDir()

	// Create a pristine instance
	if err := CopyInstanceTemplate(dir); err != nil {
		t.Fatalf("CopyInstanceTemplate() error = %v", err)
	}

	// Update should report no changes
	changed, err := UpdateInstanceTemplate(dir, false)
	if err != nil {
		t.Fatalf("UpdateInstanceTemplate() error = %v", err)
	}
	if changed {
		t.Error("expected no changes on a freshly created instance")
	}
}

func TestUpdateInstanceTemplate_DetectsChange(t *testing.T) {
	dir := t.TempDir()

	// Create a pristine instance
	if err := CopyInstanceTemplate(dir); err != nil {
		t.Fatalf("CopyInstanceTemplate() error = %v", err)
	}

	// Modify a non-user-editable file (default.vcl)
	vclPath := filepath.Join(dir, "config", "default.vcl")
	if err := os.WriteFile(vclPath, []byte("modified content"), 0644); err != nil {
		t.Fatal(err)
	}

	// Update should detect the change
	changed, err := UpdateInstanceTemplate(dir, false)
	if err != nil {
		t.Fatalf("UpdateInstanceTemplate() error = %v", err)
	}
	if !changed {
		t.Error("expected change to be detected after modifying default.vcl")
	}

	// Verify the file was restored to the template version
	got, err := os.ReadFile(vclPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) == "modified content" {
		t.Error("expected default.vcl to be restored to template version")
	}
}

func TestUpdateInstanceTemplate_DryRun(t *testing.T) {
	dir := t.TempDir()

	// Create a pristine instance
	if err := CopyInstanceTemplate(dir); err != nil {
		t.Fatalf("CopyInstanceTemplate() error = %v", err)
	}

	// Modify a non-user-editable file
	vclPath := filepath.Join(dir, "config", "default.vcl")
	original, err := os.ReadFile(vclPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(vclPath, []byte("modified content"), 0644); err != nil {
		t.Fatal(err)
	}

	// Dry run should report change but not modify the file
	changed, err := UpdateInstanceTemplate(dir, true)
	if err != nil {
		t.Fatalf("UpdateInstanceTemplate() error = %v", err)
	}
	if !changed {
		t.Error("dry run should report changed = true")
	}

	// File should still have the modified content
	got, err := os.ReadFile(vclPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "modified content" {
		t.Error("dry run should not modify files")
	}

	// Now apply for real
	changed, err = UpdateInstanceTemplate(dir, false)
	if err != nil {
		t.Fatalf("UpdateInstanceTemplate() error = %v", err)
	}
	if !changed {
		t.Error("expected change after dry run")
	}

	got, err = os.ReadFile(vclPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, original) {
		t.Error("expected file to be restored to template version after apply")
	}
}

func TestUpdateInstanceTemplate_SkipsUserEditable(t *testing.T) {
	dir := t.TempDir()

	// Create a pristine instance
	if err := CopyInstanceTemplate(dir); err != nil {
		t.Fatalf("CopyInstanceTemplate() error = %v", err)
	}

	// Modify a user-editable file (.env)
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("CUSTOM=value"), 0644); err != nil {
		t.Fatal(err)
	}

	// Update should not touch user-editable files
	_, err := UpdateInstanceTemplate(dir, false)
	if err != nil {
		t.Fatalf("UpdateInstanceTemplate() error = %v", err)
	}

	got, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "CUSTOM=value" {
		t.Error("expected user-editable .env to be preserved")
	}
}

func TestUpdateInstanceTemplate_CreatesNewFile(t *testing.T) {
	dir := t.TempDir()

	// Create a pristine instance
	if err := CopyInstanceTemplate(dir); err != nil {
		t.Fatalf("CopyInstanceTemplate() error = %v", err)
	}

	// Delete a non-user-editable, non-noClobber file
	vclPath := filepath.Join(dir, "config", "default.vcl")
	if err := os.Remove(vclPath); err != nil {
		t.Fatal(err)
	}

	// Update should recreate it
	changed, err := UpdateInstanceTemplate(dir, false)
	if err != nil {
		t.Fatalf("UpdateInstanceTemplate() error = %v", err)
	}
	if !changed {
		t.Error("expected change when file is missing")
	}

	if _, err := os.Stat(vclPath); err != nil {
		t.Error("expected default.vcl to be recreated")
	}
}

func TestUpdateInstanceTemplate_NoClobberSkipsDeletedFile(t *testing.T) {
	dir := t.TempDir()

	// Create a pristine instance
	if err := CopyInstanceTemplate(dir); err != nil {
		t.Fatalf("CopyInstanceTemplate() error = %v", err)
	}

	// Delete a noClobber file (informational README)
	readmePath := filepath.Join(dir, "config", "settings", "wikis", "README")
	if err := os.Remove(readmePath); err != nil {
		t.Fatal(err)
	}

	// Update should NOT recreate it
	changed, err := UpdateInstanceTemplate(dir, false)
	if err != nil {
		t.Fatalf("UpdateInstanceTemplate() error = %v", err)
	}
	if changed {
		t.Error("expected no change when a noClobber file is deleted")
	}

	if _, err := os.Stat(readmePath); !os.IsNotExist(err) {
		t.Error("expected deleted README to stay gone")
	}
}

func TestGetBaseImage(t *testing.T) {
	defaultImage := GetDefaultImage()

	t.Run("no-env-file", func(t *testing.T) {
		dir := t.TempDir()
		got := GetBaseImage(dir)
		if got != defaultImage {
			t.Errorf("got %q, want default %q", got, defaultImage)
		}
	})

	t.Run("env-without-canasta-image", func(t *testing.T) {
		dir := t.TempDir()
		if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("MYSQL_PASSWORD=secret\n"), 0644); err != nil {
			t.Fatal(err)
		}
		got := GetBaseImage(dir)
		if got != defaultImage {
			t.Errorf("got %q, want default %q", got, defaultImage)
		}
	})

	t.Run("env-with-empty-canasta-image", func(t *testing.T) {
		dir := t.TempDir()
		if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("CANASTA_IMAGE=\n"), 0644); err != nil {
			t.Fatal(err)
		}
		got := GetBaseImage(dir)
		if got != defaultImage {
			t.Errorf("got %q, want default %q", got, defaultImage)
		}
	})

	t.Run("env-with-custom-image", func(t *testing.T) {
		dir := t.TempDir()
		if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("CANASTA_IMAGE=ghcr.io/custom:dev\n"), 0644); err != nil {
			t.Fatal(err)
		}
		got := GetBaseImage(dir)
		if got != "ghcr.io/custom:dev" {
			t.Errorf("got %q, want %q", got, "ghcr.io/custom:dev")
		}
	})
}

// Tests for IsElasticsearchEnabled and IsVarnishEnabled.

func TestIsElasticsearchEnabled(t *testing.T) {
	tests := []struct {
		name    string
		envVars map[string]string
		want    bool
	}{
		{
			name:    "explicitly true lowercase",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": "true"},
			want:    true,
		},
		{
			name:    "explicitly true uppercase",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": "TRUE"},
			want:    true,
		},
		{
			name:    "explicitly true mixed case",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": "True"},
			want:    true,
		},
		{
			name:    "explicitly false lowercase",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": "false"},
			want:    false,
		},
		{
			name:    "explicitly false uppercase",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": "FALSE"},
			want:    false,
		},
		{
			name:    "key absent — defaults to disabled",
			envVars: map[string]string{},
			want:    false,
		},
		{
			name:    "key set to empty string — defaults to disabled",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": ""},
			want:    false,
		},
		{
			name:    "arbitrary non-true value",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": "yes"},
			want:    false,
		},
		{
			name:    "1 is not treated as true",
			envVars: map[string]string{"CANASTA_ENABLE_ELASTICSEARCH": "1"},
			want:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := IsElasticsearchEnabled(tt.envVars)
			if got != tt.want {
				t.Errorf("IsElasticsearchEnabled(%v) = %v, want %v", tt.envVars, got, tt.want)
			}
		})
	}
}

func TestIsVarnishEnabled(t *testing.T) {
	tests := []struct {
		name    string
		envVars map[string]string
		want    bool
	}{
		{
			name:    "explicitly true lowercase",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": "true"},
			want:    true,
		},
		{
			name:    "explicitly true uppercase",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": "TRUE"},
			want:    true,
		},
		{
			name:    "explicitly true mixed case",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": "True"},
			want:    true,
		},
		{
			name:    "explicitly false lowercase",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": "false"},
			want:    false,
		},
		{
			name:    "explicitly false uppercase",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": "FALSE"},
			want:    false,
		},
		{
			// IsVarnishEnabled defaults to TRUE when the key is absent,
			// unlike IsElasticsearchEnabled and IsObservabilityEnabled which
			// default to false. This is for backward compatibility with
			// existing deployments that ran Varnish before the flag existed.
			name:    "key absent — defaults to enabled (backward compat)",
			envVars: map[string]string{},
			want:    true,
		},
		{
			// An empty string value also triggers the default-true path
			// because the code checks: if !ok || v == "" { return true }
			name:    "key present but empty string — defaults to enabled",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": ""},
			want:    true,
		},
		{
			name:    "arbitrary non-true value is treated as false",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": "yes"},
			want:    false,
		},
		{
			name:    "1 is not treated as true",
			envVars: map[string]string{"CANASTA_ENABLE_VARNISH": "1"},
			want:    false,
		},
		{
			// Confirm Varnish and Elasticsearch can be independently
			// enabled — unrelated keys must not influence the result.
			name: "unrelated env keys do not affect result",
			envVars: map[string]string{
				"CANASTA_ENABLE_ELASTICSEARCH": "false",
				"CANASTA_ENABLE_OBSERVABILITY": "false",
			},
			want: true, // Varnish key absent → default true
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := IsVarnishEnabled(tt.envVars)
			if got != tt.want {
				t.Errorf("IsVarnishEnabled(%v) = %v, want %v", tt.envVars, got, tt.want)
			}
		})
	}
}