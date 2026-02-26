package orchestrators

import "testing"

func TestShellQuote(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"simple", "'simple'"},
		{"with space", "'with space'"},
		{"has'quote", `'has'\''quote'`},
		{"$HOME", "'$HOME'"},
		{"`whoami`", "'`whoami`'"},
		{"$(id)", "'$(id)'"},
		{"back\\slash", "'back\\slash'"},
		{"semi;colon", "'semi;colon'"},
		{"", "''"},
		{"multi'ple'quotes", `'multi'\''ple'\''quotes'`},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := ShellQuote(tt.input)
			if got != tt.want {
				t.Errorf("ShellQuote(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}
