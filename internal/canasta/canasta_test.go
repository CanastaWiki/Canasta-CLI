package canasta

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
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
