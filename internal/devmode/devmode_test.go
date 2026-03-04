package devmode

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIsDevModeSetup(t *testing.T) {
	gotests := []struct {
		name  string
		setup func(t *testing.T, dir string)
		want  bool
	}{
		{
			name: "both files present",
			setup: func(t *testing.T, dir string) {
				if err := os.WriteFile(filepath.Join(dir, "docker-compose.dev.yml"), []byte{}, 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(dir, "Dockerfile.xdebug"), []byte{}, 0600); err != nil {
					t.Fatal(err)
				}
			},
			want: true,
		},
		{
			name: "neither file present",
			setup: func(t *testing.T, dir string) {
			},
			want: false,
		},
		{
			name: "only docker-compose.dev.yml present",
			setup: func(t *testing.T, dir string) {
				if err := os.WriteFile(filepath.Join(dir, "docker-compose.dev.yml"), []byte{}, 0600); err != nil {
					t.Fatal(err)
				}
			},
			want: false,
		},
		{
			name: "only Dockerfile.xdebug present",
			setup: func(t *testing.T, dir string) {
				if err := os.WriteFile(filepath.Join(dir, "Dockerfile.xdebug"), []byte{}, 0600); err != nil {
					t.Fatal(err)
				}
			},
			want: false,
		},
	}

	for _, tt := range gotests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			tt.setup(t, dir)

			if got := IsDevModeSetup(dir); got != tt.want {
				t.Errorf("IsDevModeSetup(%q) = %v, want %v", dir, got, tt.want)
			}
		})
	}
}
