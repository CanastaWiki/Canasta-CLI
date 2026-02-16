package backup

import (
	"os"
	"path/filepath"
	"testing"

	yaml "gopkg.in/yaml.v2"

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

func TestGetWikiIDs(t *testing.T) {
	dir := t.TempDir()
	wikis := []farmsettings.Wiki{
		{ID: "main", URL: "localhost", NAME: "main"},
		{ID: "wiki2", URL: "localhost/wiki2", NAME: "wiki2"},
	}
	writeWikisYaml(t, dir, wikis)

	t.Run("returns all wikis", func(t *testing.T) {
		ids, err := getWikiIDs(dir)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(ids) != 2 || ids[0] != "main" || ids[1] != "wiki2" {
			t.Errorf("got %v, want [main wiki2]", ids)
		}
	})

	t.Run("missing wikis.yaml", func(t *testing.T) {
		emptyDir := t.TempDir()
		_, err := getWikiIDs(emptyDir)
		if err == nil {
			t.Fatal("expected error for missing wikis.yaml")
		}
	})
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
	})

	t.Run("missing wikis.yaml returns error", func(t *testing.T) {
		emptyDir := t.TempDir()
		_, err := getWikiIDsForRestore(emptyDir)
		if err == nil {
			t.Fatal("expected error for missing wikis.yaml")
		}
	})
}
