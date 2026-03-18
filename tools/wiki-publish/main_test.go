package main

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

func TestExtractDefault(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{`some description (default: foo)`, "foo"},
		{`some description (defaults to bar)`, "bar"},
		{`some description (optional, defaults to baz)`, "baz"},
		{`some description (default: "quoted")`, "quoted"},
		{`no default here`, ""},
		{`empty parens ()`, ""},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := extractDefault(tt.input)
			if got != tt.want {
				t.Errorf("extractDefault(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func newTestCmd() *cobra.Command {
	root := &cobra.Command{
		Use:   "canasta",
		Short: "Manage Canasta installations",
	}
	child := &cobra.Command{
		Use:     "create",
		Short:   "Create a new installation",
		Long:    "Create a new Canasta installation with all required services.",
		Example: "canasta create -i myinstance -w mywiki -n localhost",
		Run:     func(cmd *cobra.Command, args []string) {},
	}
	child.Flags().StringP("id", "i", "", "Installation ID")
	//nolint:errcheck
	child.MarkFlagRequired("id")
	child.Flags().String("orchestrator", "docker-compose", "Container orchestrator (default: docker-compose)")

	sub := &cobra.Command{
		Use:   "list",
		Short: "List items",
		Run:   func(cmd *cobra.Command, args []string) {},
	}
	child.AddCommand(sub)
	root.AddCommand(child)
	return root
}

func TestGenWikitext(t *testing.T) {
	root := newTestCmd()
	create, _, _ := root.Find([]string{"create"})

	text := genWikitext(create)

	// Breadcrumb
	if !strings.Contains(text, "[[CLI:canasta|canasta]]") {
		t.Error("expected breadcrumb link to parent")
	}
	if !strings.Contains(text, "> create") {
		t.Error("expected current command in breadcrumb")
	}

	// Heading
	if !strings.Contains(text, "== canasta create ==") {
		t.Error("expected command heading")
	}

	// Short description
	if !strings.Contains(text, "Create a new installation") {
		t.Error("expected short description")
	}

	// Synopsis
	if !strings.Contains(text, "=== Synopsis ===") {
		t.Error("expected synopsis section")
	}

	// Usage line
	if !strings.Contains(text, "canasta create") {
		t.Error("expected usage line")
	}

	// Subcommands
	if !strings.Contains(text, "=== Subcommands ===") {
		t.Error("expected subcommands section")
	}
	if !strings.Contains(text, "[[CLI:canasta_create_list|list]]") {
		t.Error("expected subcommand link")
	}

	// Examples
	if !strings.Contains(text, "=== Examples ===") {
		t.Error("expected examples section")
	}

	// Flags table
	if !strings.Contains(text, "{| class=\"wikitable\"") {
		t.Error("expected flags table")
	}
	if !strings.Contains(text, "<code>--id</code>") {
		t.Error("expected --id flag")
	}
	if !strings.Contains(text, "<code>-i</code>") {
		t.Error("expected -i shorthand")
	}
}

func TestGenWikitextRoot(t *testing.T) {
	root := newTestCmd()
	text := genWikitext(root)

	// Root should have no breadcrumb (no " > " separator)
	lines := strings.SplitN(text, "\n", 3)
	if strings.Contains(lines[0], " > ") {
		t.Error("root command should not have breadcrumb navigation")
	}

	// Should list subcommands
	if !strings.Contains(text, "[[CLI:canasta_create|create]]") {
		t.Error("expected subcommand link for create")
	}
}

func TestGenMenuCommands(t *testing.T) {
	root := newTestCmd()
	create, _, _ := root.Find([]string{"create"})

	var b strings.Builder
	genMenuCommands(&b, create, 4)
	menu := b.String()

	if !strings.Contains(menu, "**** CLI:canasta_create_list | canasta create list") {
		t.Errorf("expected menu entry for subcommand, got:\n%s", menu)
	}
}

func TestAllFlagsToWikiTable(t *testing.T) {
	root := newTestCmd()
	create, _, _ := root.Find([]string{"create"})

	table := allFlagsToWikiTable(create)

	// Required flag should have checkmark
	if !strings.Contains(table, "| <code>--id</code>") {
		t.Error("expected --id in table")
	}

	// Parenthetical default should be extracted
	if !strings.Contains(table, "docker-compose") {
		t.Error("expected extracted default value for orchestrator")
	}
}

func TestCollectPages(t *testing.T) {
	root := newTestCmd()
	var pages []wikiPage
	collectPages(root, &pages)

	titles := make(map[string]bool)
	for _, p := range pages {
		titles[p.Title] = true
	}

	expected := []string{
		"CLI:canasta",
		"CLI:canasta_create",
		"CLI:canasta_create_list",
	}
	for _, title := range expected {
		if !titles[title] {
			t.Errorf("expected page %q not found", title)
		}
	}
}
