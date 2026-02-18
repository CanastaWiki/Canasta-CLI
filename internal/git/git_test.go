package git

import (
	"testing"
)

func TestIsSkipped(t *testing.T) {
	tests := []struct {
		name   string
		file   string
		expect bool
	}{
		{"exact match my.cnf", "my.cnf", true},
		{"exact match override", "docker-compose.override.yml", true},
		{"exact match caddyfile site", "config/Caddyfile.site", true},
		{"exact match composer local", "config/composer.local.json", true},
		{"exact match default vcl", "config/default.vcl", true},
		{"directory prefix settings", "config/settings/global/Vector.php", true},
		{"directory prefix settings wiki", "config/settings/wikis/mywiki/Settings.php", true},
		{"not skipped docker-compose.yml", "docker-compose.yml", false},
		{"not skipped env", ".env", false},
		{"not skipped config yaml", "config/wikis.yaml", false},
		{"not skipped random file", "somefile.txt", false},
		{"partial match not skipped", "my.cnf.bak", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := isSkipped(tt.file)
			if got != tt.expect {
				t.Errorf("isSkipped(%q) = %v, want %v", tt.file, got, tt.expect)
			}
		})
	}
}
