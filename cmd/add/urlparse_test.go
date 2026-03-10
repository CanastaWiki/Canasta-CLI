package add

import (
	"path/filepath"
	"testing"
)

func TestParseWikiURL(t *testing.T) {
	tests := []struct {
		name       string
		rawURL     string
		wantDomain string
		wantPath   string
		wantErr    bool
	}{
		{
			name:       "domain-only",
			rawURL:     "example.com",
			wantDomain: "example.com",
			wantPath:   "",
		},
		{
			name:       "domain-with-path",
			rawURL:     "localhost/wiki2",
			wantDomain: "localhost",
			wantPath:   "wiki2",
		},
		{
			name:       "domain-with-deep-path",
			rawURL:     "example.com/a/b/c",
			wantDomain: "example.com",
			wantPath:   "a/b/c",
		},
		{
			name:       "with-https-scheme",
			rawURL:     "https://example.com/docs",
			wantDomain: "example.com",
			wantPath:   "docs",
		},
		{
			name:       "with-http-scheme",
			rawURL:     "http://example.com/docs",
			wantDomain: "example.com",
			wantPath:   "docs",
		},
		{
			name:       "domain-with-port",
			rawURL:     "localhost:8443/wiki",
			wantDomain: "localhost:8443",
			wantPath:   "wiki",
		},
		{
			name:       "trailing-slash-stripped",
			rawURL:     "example.com/wiki/",
			wantDomain: "example.com",
			wantPath:   "wiki",
		},
		{
			name:       "subdomain",
			rawURL:     "docs.example.com",
			wantDomain: "docs.example.com",
			wantPath:   "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := ParseWikiURL(tt.rawURL)
			if (err != nil) != tt.wantErr {
				t.Errorf("ParseWikiURL(%q) error = %v, wantErr %v", tt.rawURL, err, tt.wantErr)
				return
			}
			if got.Domain != tt.wantDomain {
				t.Errorf("ParseWikiURL(%q).Domain = %q, want %q", tt.rawURL, got.Domain, tt.wantDomain)
			}
			if got.Path != tt.wantPath {
				t.Errorf("ParseWikiURL(%q).Path = %q, want %q", tt.rawURL, got.Path, tt.wantPath)
			}
		})
	}
}

func TestResolveFilePaths(t *testing.T) {
	base := "/home/user"

	t.Run("relative-becomes-absolute", func(t *testing.T) {
		p := "settings.php"
		resolveFilePaths(base, &p)
		want := filepath.Join(base, "settings.php")
		if p != want {
			t.Errorf("got %q, want %q", p, want)
		}
	})

	t.Run("absolute-unchanged", func(t *testing.T) {
		p := "/tmp/settings.php"
		resolveFilePaths(base, &p)
		if p != "/tmp/settings.php" {
			t.Errorf("got %q, want /tmp/settings.php", p)
		}
	})

	t.Run("empty-unchanged", func(t *testing.T) {
		p := ""
		resolveFilePaths(base, &p)
		if p != "" {
			t.Errorf("got %q, want empty", p)
		}
	})
}
