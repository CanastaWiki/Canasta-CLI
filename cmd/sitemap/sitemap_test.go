package sitemap

import (
	"testing"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func TestExtractScheme(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{"https URL", "https://example.com", "https"},
		{"http URL", "http://example.com", "http"},
		{"empty string", "", "https"},
		{"no scheme", "example.com", "https"},
		{"https with port", "https://localhost:8443", "https"},
		{"http with port", "http://localhost:8080", "http"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractScheme(tt.input)
			if got != tt.expected {
				t.Errorf("extractScheme(%q) = %q, want %q", tt.input, got, tt.expected)
			}
		})
	}
}

func TestNewCmd(t *testing.T) {
	cmd := NewCmd()
	if cmd.Use != "sitemap" {
		t.Errorf("expected Use='sitemap', got %q", cmd.Use)
	}

	// Verify subcommands exist
	subCmds := cmd.Commands()
	names := make(map[string]bool)
	for _, sub := range subCmds {
		names[sub.Use] = true
	}
	if !names["generate"] {
		t.Error("expected 'generate' subcommand")
	}
	if !names["remove"] {
		t.Error("expected 'remove' subcommand")
	}

	// Verify persistent flags
	idFlag := cmd.PersistentFlags().Lookup("id")
	if idFlag == nil {
		t.Error("expected persistent flag 'id'")
	}
}

func TestGenerateCmd(t *testing.T) {
	var instance config.Installation
	var orch orchestrators.Orchestrator
	cmd := newGenerateCmd(&instance, &orch)
	if cmd.Use != "generate" {
		t.Errorf("expected Use='generate', got %q", cmd.Use)
	}

	wikiFlag := cmd.Flags().Lookup("wiki")
	if wikiFlag == nil {
		t.Error("expected flag 'wiki'")
	}
	if wikiFlag.Shorthand != "w" {
		t.Errorf("expected wiki shorthand 'w', got %q", wikiFlag.Shorthand)
	}
}

func TestRemoveCmd(t *testing.T) {
	var instance config.Installation
	var orch orchestrators.Orchestrator
	cmd := newRemoveCmd(&instance, &orch)
	if cmd.Use != "remove" {
		t.Errorf("expected Use='remove', got %q", cmd.Use)
	}

	wikiFlag := cmd.Flags().Lookup("wiki")
	if wikiFlag == nil {
		t.Error("expected flag 'wiki'")
	}
	if wikiFlag.Shorthand != "w" {
		t.Errorf("expected wiki shorthand 'w', got %q", wikiFlag.Shorthand)
	}

	yesFlag := cmd.Flags().Lookup("yes")
	if yesFlag == nil {
		t.Error("expected flag 'yes'")
	}
	if yesFlag.Shorthand != "y" {
		t.Errorf("expected yes shorthand 'y', got %q", yesFlag.Shorthand)
	}
}
