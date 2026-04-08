package backup

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	yaml "gopkg.in/yaml.v2"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
)

func writeWikisYaml(t *testing.T, dir string, wikis []farmsettings.Wiki) {
	t.Helper()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	data, err := yaml.Marshal(farmsettings.Wikis{Wikis: wikis})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "wikis.yaml"), data, 0644); err != nil {
		t.Fatal(err)
	}
}

func TestDumpPath(t *testing.T) {
	got := dumpPath("main")
	want := "/mediawiki/config/backup/db_main.sql"
	if got != want {
		t.Errorf("dumpPath(\"main\") = %q, want %q", got, want)
	}

	got = dumpPath("wiki2")
	want = "/mediawiki/config/backup/db_wiki2.sql"
	if got != want {
		t.Errorf("dumpPath(\"wiki2\") = %q, want %q", got, want)
	}
}

func TestGetWikiIDsForRestore(t *testing.T) {
	dir := t.TempDir()
	wikis := []farmsettings.Wiki{
		{ID: "main", URL: "localhost", NAME: "main"},
		{ID: "wiki2", URL: "localhost/wiki2", NAME: "wiki2"},
	}
	writeWikisYaml(t, dir, wikis)

	t.Run("all dumps present", func(t *testing.T) {
		// Create per-wiki dump files in config/backup/
		backupDir := filepath.Join(dir, "config", "backup")
		if err := os.MkdirAll(backupDir, 0755); err != nil {
			t.Fatal(err)
		}
		for _, id := range []string{"main", "wiki2"} {
			f := filepath.Join(backupDir, "db_"+id+".sql")
			if err := os.WriteFile(f, []byte("-- dump"), 0644); err != nil {
				t.Fatal(err)
			}
		}

		ids, err := getWikiIDsForRestore(dir)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(ids) != 2 {
			t.Errorf("got %d wiki IDs, want 2", len(ids))
		}
	})

	t.Run("no dump files returns error", func(t *testing.T) {
		noDumpsDir := t.TempDir()
		writeWikisYaml(t, noDumpsDir, wikis)

		_, err := getWikiIDsForRestore(noDumpsDir)
		if err == nil {
			t.Fatal("expected error when no dump files found")
		}
		if !strings.Contains(err.Error(), "no database dump files") {
			t.Errorf("expected 'no database dump files' error, got: %v", err)
		}
	})

	t.Run("single wiki dump present", func(t *testing.T) {
		singleDir := t.TempDir()
		writeWikisYaml(t, singleDir, wikis)

		backupDir := filepath.Join(singleDir, "config", "backup")
		if err := os.MkdirAll(backupDir, 0755); err != nil {
			t.Fatal(err)
		}
		// Only create dump for wiki2, not main
		if err := os.WriteFile(filepath.Join(backupDir, "db_wiki2.sql"), []byte("-- dump"), 0644); err != nil {
			t.Fatal(err)
		}

		ids, err := getWikiIDsForRestore(singleDir)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(ids) != 1 || ids[0] != "wiki2" {
			t.Errorf("got %v, want [wiki2]", ids)
		}
	})

	t.Run("missing wikis.yaml returns error", func(t *testing.T) {
		emptyDir := t.TempDir()
		_, err := getWikiIDsForRestore(emptyDir)
		if err == nil {
			t.Fatal("expected error for missing wikis.yaml")
		}
		if !strings.Contains(err.Error(), "failed to read wikis.yaml") {
			t.Errorf("expected 'failed to read wikis.yaml' error, got: %v", err)
		}
	})

	t.Run("single wiki instance", func(t *testing.T) {
		singleWikiDir := t.TempDir()
		singleWikis := []farmsettings.Wiki{
			{ID: "main", URL: "localhost", NAME: "Main Wiki"},
		}
		writeWikisYaml(t, singleWikiDir, singleWikis)

		backupDir := filepath.Join(singleWikiDir, "config", "backup")
		if err := os.MkdirAll(backupDir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(backupDir, "db_main.sql"), []byte("-- dump"), 0644); err != nil {
			t.Fatal(err)
		}

		ids, err := getWikiIDsForRestore(singleWikiDir)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(ids) != 1 || ids[0] != "main" {
			t.Errorf("got %v, want [main]", ids)
		}
	})

	t.Run("empty wikis list", func(t *testing.T) {
		emptyWikisDir := t.TempDir()
		emptyWikisList := []farmsettings.Wiki{}
		writeWikisYaml(t, emptyWikisDir, emptyWikisList)

		// ReadWikisYaml returns an error for empty wikis, which propagates
		_, err := getWikiIDsForRestore(emptyWikisDir)
		if err == nil {
			t.Fatal("expected error for empty wikis list")
		}
		if !strings.Contains(err.Error(), "failed to read wikis.yaml") {
			t.Errorf("expected 'failed to read wikis.yaml' error, got: %v", err)
		}
	})

	t.Run("dump file exists but wiki not in yaml", func(t *testing.T) {
		mismatchDir := t.TempDir()
		mismatchWikis := []farmsettings.Wiki{
			{ID: "main", URL: "localhost", NAME: "Main"},
		}
		writeWikisYaml(t, mismatchDir, mismatchWikis)

		backupDir := filepath.Join(mismatchDir, "config", "backup")
		if err := os.MkdirAll(backupDir, 0755); err != nil {
			t.Fatal(err)
		}
		// Create a dump for a wiki not in wikis.yaml
		if err := os.WriteFile(filepath.Join(backupDir, "db_otherwiki.sql"), []byte("-- dump"), 0644); err != nil {
			t.Fatal(err)
		}

		// Only wikis in wikis.yaml are checked, so this should fail
		_, err := getWikiIDsForRestore(mismatchDir)
		if err == nil {
			t.Fatal("expected error when dump exists for wiki not in wikis.yaml")
		}
	})
}

func TestRestorePreservesDBPasswords(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")

	// Write an initial .env with known passwords.
	original := "MYSQL_PASSWORD=original_root_pw\nWIKI_DB_PASSWORD=original_wiki_pw\nMW_SITE_SERVER=https://example.com\n"
	if err := os.WriteFile(envPath, []byte(original), 0644); err != nil {
		t.Fatal(err)
	}

	// Simulate the backup overwriting .env with different passwords.
	overwritten := "MYSQL_PASSWORD=backup_root_pw\nWIKI_DB_PASSWORD=backup_wiki_pw\nMW_SITE_SERVER=https://backup.example.com\n"
	if err := os.WriteFile(envPath, []byte(overwritten), 0644); err != nil {
		t.Fatal(err)
	}

	// Preserve the original passwords using SaveEnvVariable, matching
	// the logic in restoreFull.
	for _, kv := range []struct{ key, val string }{
		{"MYSQL_PASSWORD", "original_root_pw"},
		{"WIKI_DB_PASSWORD", "original_wiki_pw"},
	} {
		if err := canasta.SaveEnvVariable(envPath, kv.key, kv.val); err != nil {
			t.Fatalf("SaveEnvVariable(%s) error: %v", kv.key, err)
		}
	}

	// Verify the .env now has the original passwords restored.
	data, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatal(err)
	}
	content := string(data)

	if !strings.Contains(content, "MYSQL_PASSWORD=original_root_pw") {
		t.Errorf("expected MYSQL_PASSWORD=original_root_pw, got:\n%s", content)
	}
	if !strings.Contains(content, "WIKI_DB_PASSWORD=original_wiki_pw") {
		t.Errorf("expected WIKI_DB_PASSWORD=original_wiki_pw, got:\n%s", content)
	}
	// Non-password variables should retain the backup's values.
	if !strings.Contains(content, "MW_SITE_SERVER=https://backup.example.com") {
		t.Errorf("expected MW_SITE_SERVER from backup to be preserved, got:\n%s", content)
	}
}

func TestGetRepoURL(t *testing.T) {
	for _, tc := range []struct {
		name    string
		env     map[string]string
		want    string
		wantErr bool
	}{
		{
			name: "RESTIC_REPOSITORY is set",
			env: map[string]string{
				"RESTIC_REPOSITORY": "rest:http://repo.example/repo",
				"RESTIC_REPO":       "rest:http://repo.example/other",
				"AWS_S3_API":        "https://s3.example",
				"AWS_S3_BUCKET":     "bucket",
			},
			want: "rest:http://repo.example/repo",
		},
		{
			name: "RESTIC_REPO is set",
			env: map[string]string{
				"RESTIC_REPOSITORY": "",
				"RESTIC_REPO":       "rest:http://repo.example/repo",
				"AWS_S3_API":        "https://s3.example",
				"AWS_S3_BUCKET":     "bucket",
			},
			want: "rest:http://repo.example/repo",
		},
		{
			name: "AWS S3 settings are set",
			env: map[string]string{
				"AWS_S3_API":    "https://s3.example",
				"AWS_S3_BUCKET": "bucket",
			},
			want: "s3:https://s3.example/bucket",
		},
		{
			name: "only AWS_S3_API is set",
			env: map[string]string{
				"AWS_S3_API": "https://s3.example",
			},
			want: "s3:https://s3.example/",
		},
		{
			name: "only AWS_S3_BUCKET is set",
			env: map[string]string{
				"AWS_S3_BUCKET": "bucket",
			},
			want: "s3:/bucket",
		},
		{
			name: "all vars empty strings",
			env: map[string]string{
				"RESTIC_REPOSITORY": "",
				"RESTIC_REPO":       "",
				"AWS_S3_API":        "",
				"AWS_S3_BUCKET":     "",
			},
			wantErr: true,
		},
		{
			name:    "all vars absent",
			env:     map[string]string{},
			wantErr: true,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := getRepoURL(tc.env)
			if tc.wantErr {
				if err == nil {
					t.Error("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Errorf("getRepoURL() = %q, want %q", got, tc.want)
			}
		})
	}
}
