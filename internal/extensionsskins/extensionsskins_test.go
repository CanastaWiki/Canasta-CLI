package extensionsskins

import (
	"slices"
	"testing"
)

func TestValidateName(t *testing.T) {
	constants := Item{CmdName: "extension"}

	tests := []struct {
		name    string
		input   string
		wantErr bool
	}{
		{"simple name", "VisualEditor", false},
		{"underscore", "Semantic_MediaWiki", false},
		{"hyphen", "My-Extension", false},
		{"dot", "Auth.v2", false},
		{"digits", "Extension123", false},
		{"starts with digit", "3DAlloy", false},
		{"empty", "", true},
		{"shell metachar backtick", "ext`id`", true},
		{"shell metachar dollar", "ext$(cmd)", true},
		{"semicolon", "ext;rm -rf /", true},
		{"space", "Visual Editor", true},
		{"single quote", "ext'name", true},
		{"double quote", `ext"name`, true},
		{"slash", "ext/name", true},
		{"starts with hyphen", "-extension", true},
		{"starts with dot", ".hidden", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateName(tt.input, constants)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateName(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
			}
		})
	}
}

func TestSlicesContains(t *testing.T) {
	list := []string{"VisualEditor", "Cite", "ParserFunctions"}

	if !slices.Contains(list, "Cite") {
		t.Error("expected slices.Contains to return true for 'Cite'")
	}
	if slices.Contains(list, "Missing") {
		t.Error("expected slices.Contains to return false for 'Missing'")
	}
}
