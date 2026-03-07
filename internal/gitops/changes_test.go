package gitops

import (
	"testing"
)

func TestAnalyzeChangesEmpty(t *testing.T) {
	restart, maintenance, submods := AnalyzeChanges(nil)
	if restart || maintenance || len(submods) > 0 {
		t.Errorf("expected no changes for nil input")
	}
}

func TestAnalyzeChangesRestart(t *testing.T) {
	restartFiles := []string{
		"env.template",
		".env",
		"config/wikis.yaml",
		"config/Caddyfile.site",
		"config/Caddyfile.global",
		"docker-compose.override.yml",
	}
	for _, f := range restartFiles {
		restart, _, _ := AnalyzeChanges([]string{f})
		if !restart {
			t.Errorf("expected restart=true for %q", f)
		}
	}
}

func TestAnalyzeChangesSubmodules(t *testing.T) {
	files := []string{
		"extensions/VisualEditor/foo.php",
		"extensions/VisualEditor/bar.php",
		"skins/Vector/skin.json",
	}
	restart, maintenance, submods := AnalyzeChanges(files)
	if restart {
		t.Error("expected restart=false for extension/skin changes")
	}
	if !maintenance {
		t.Error("expected maintenance=true for extension/skin changes")
	}
	if len(submods) != 2 {
		t.Errorf("expected 2 submodules, got %d", len(submods))
	}
}

func TestAnalyzeChangesMixed(t *testing.T) {
	files := []string{
		"env.template",
		"extensions/Cite/Cite.php",
	}
	restart, maintenance, submods := AnalyzeChanges(files)
	if !restart {
		t.Error("expected restart=true")
	}
	if !maintenance {
		t.Error("expected maintenance=true")
	}
	if len(submods) != 1 {
		t.Errorf("expected 1 submodule, got %d", len(submods))
	}
}

func TestAnalyzeChangesNoEffect(t *testing.T) {
	files := []string{"README.md", "hosts.yaml", "hosts/prod/vars.yaml"}
	restart, maintenance, submods := AnalyzeChanges(files)
	if restart || maintenance || len(submods) > 0 {
		t.Error("expected no effect for unrecognized files")
	}
}

func TestCanPush(t *testing.T) {
	tests := []struct {
		role string
		want bool
	}{
		{RoleSource, true},
		{RoleBoth, true},
		{RoleSink, false},
	}
	for _, tt := range tests {
		if got := CanPush(tt.role); got != tt.want {
			t.Errorf("CanPush(%q) = %v, want %v", tt.role, got, tt.want)
		}
	}
}

func TestCanPull(t *testing.T) {
	tests := []struct {
		role string
		want bool
	}{
		{RoleSink, true},
		{RoleBoth, true},
		{RoleSource, false},
	}
	for _, tt := range tests {
		if got := CanPull(tt.role); got != tt.want {
			t.Errorf("CanPull(%q) = %v, want %v", tt.role, got, tt.want)
		}
	}
}

func TestValidateRole(t *testing.T) {
	for _, role := range []string{RoleSource, RoleSink, RoleBoth} {
		if err := ValidateRole(role); err != nil {
			t.Errorf("ValidateRole(%q) = %v, want nil", role, err)
		}
	}
	if err := ValidateRole("primary"); err == nil {
		t.Error("ValidateRole(\"primary\") = nil, want error")
	}
}
