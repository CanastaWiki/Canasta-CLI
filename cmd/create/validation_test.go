package create

import (
	"os"
	"path/filepath"
	"testing"
)

func TestValidateInstanceID(t *testing.T) {
	tests := []struct {
		name    string
		id      string
		wantErr bool
	}{
		{"simple", "myinstance", false},
		{"with-hyphen", "my-instance", false},
		{"with-underscore", "my_instance", false},
		{"digits", "wiki123", false},
		{"single-char", "a", false},
		{"starts-with-digit", "1wiki", false},
		{"empty", "", true},
		{"has-space", "my instance", true},
		{"starts-with-hyphen", "-wiki", true},
		{"ends-with-hyphen", "wiki-", true},
		{"special-chars", "wiki@home", true},
		{"unicode", "wiki日本", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateInstanceID(tt.id)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateInstanceID(%q) error = %v, wantErr %v", tt.id, err, tt.wantErr)
			}
		})
	}
}

func TestValidateCreateFlags(t *testing.T) {
	tests := []struct {
		name          string
		wikiID        string
		yamlPath      string
		instanceID    string
		canastaImage  string
		buildFromPath string
		databasePath  string
		wantErr       bool
		errContains   string
	}{
		{
			name:       "valid-minimal",
			wikiID:     "main",
			instanceID: "test",
		},
		{
			name:       "valid-with-yaml",
			yamlPath:   "/some/path.yaml",
			instanceID: "test",
		},
		{
			name:        "missing-wiki-without-yaml",
			instanceID:  "test",
			wantErr:     true,
			errContains: "--wiki flag is required",
		},
		{
			name:        "invalid-instance-id",
			wikiID:      "main",
			instanceID:  "-bad",
			wantErr:     true,
			errContains: "alphanumeric",
		},
		{
			name:          "mutually-exclusive-image-flags",
			wikiID:        "main",
			instanceID:    "test",
			canastaImage:  "ghcr.io/example",
			buildFromPath: "/some/path",
			wantErr:       true,
			errContains:   "mutually exclusive",
		},
		{
			name:         "invalid-database-extension",
			wikiID:       "main",
			instanceID:   "test",
			databasePath: "/nonexistent/dump.txt",
			wantErr:      true,
			errContains:  ".sql",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateCreateFlags(tt.wikiID, tt.yamlPath, tt.instanceID, tt.canastaImage, tt.buildFromPath, tt.databasePath)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateCreateFlags() error = %v, wantErr %v", err, tt.wantErr)
			}
			if err != nil && tt.errContains != "" {
				if got := err.Error(); !contains(got, tt.errContains) {
					t.Errorf("error %q does not contain %q", got, tt.errContains)
				}
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

func TestBuildDomainWithPort(t *testing.T) {
	tests := []struct {
		name   string
		domain string
		env    map[string]string
		want   string
	}{
		{"no-port", "localhost", map[string]string{}, "localhost"},
		{"standard-port", "localhost", map[string]string{"HTTPS_PORT": "443"}, "localhost"},
		{"empty-port", "localhost", map[string]string{"HTTPS_PORT": ""}, "localhost"},
		{"custom-port", "localhost", map[string]string{"HTTPS_PORT": "8443"}, "localhost:8443"},
		{"custom-port-with-domain", "example.com", map[string]string{"HTTPS_PORT": "9443"}, "example.com:9443"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := BuildDomainWithPort(tt.domain, tt.env)
			if got != tt.want {
				t.Errorf("BuildDomainWithPort(%q, %v) = %q, want %q", tt.domain, tt.env, got, tt.want)
			}
		})
	}
}

func TestValidateCreateFlags_WithRealDatabase(t *testing.T) {
	// Create a temporary .sql file to test the valid database path case
	tmp := t.TempDir()
	dbPath := filepath.Join(tmp, "dump.sql")
	if err := os.WriteFile(dbPath, []byte("-- SQL dump"), 0o644); err != nil {
		t.Fatal(err)
	}

	err := ValidateCreateFlags("main", "", "test", "", "", dbPath)
	if err != nil {
		t.Errorf("expected no error for valid database path, got: %v", err)
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && searchString(s, substr)
}

func searchString(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
