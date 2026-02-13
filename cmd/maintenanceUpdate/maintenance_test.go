package maintenance

import (
	"testing"

	"github.com/spf13/cobra"
)

// findSubcommand returns the named subcommand, or nil if not found.
func findSubcommand(parent interface{ Commands() []*cobra.Command }, name string) *cobra.Command {
	for _, c := range parent.Commands() {
		if c.Name() == name {
			return c
		}
	}
	return nil
}

func TestMaintenanceSubcommands(t *testing.T) {
	cmd := NewCmdCreate()

	expected := []string{"update", "script", "extension"}
	for _, name := range expected {
		if findSubcommand(cmd, name) == nil {
			t.Errorf("expected subcommand %q not found", name)
		}
	}
}

func TestMaintenancePersistentFlags(t *testing.T) {
	cmd := NewCmdCreate()

	flags := []struct {
		name      string
		shorthand string
	}{
		{"id", "i"},
		{"wiki", "w"},
		{"all", ""},
	}

	for _, f := range flags {
		pf := cmd.PersistentFlags().Lookup(f.name)
		if pf == nil {
			t.Errorf("persistent flag --%s not found", f.name)
			continue
		}
		if f.shorthand != "" && pf.Shorthand != f.shorthand {
			t.Errorf("flag --%s shorthand = %q, want %q", f.name, pf.Shorthand, f.shorthand)
		}
	}
}

func TestUpdateFlags(t *testing.T) {
	cmd := NewCmdCreate()
	updateCmd := findSubcommand(cmd, "update")
	if updateCmd == nil {
		t.Fatal("update subcommand not found")
	}

	for _, name := range []string{"skip-jobs", "skip-smw"} {
		if updateCmd.Flags().Lookup(name) == nil {
			t.Errorf("flag --%s not found on update subcommand", name)
		}
	}
}

func TestScriptAcceptsZeroArgs(t *testing.T) {
	cmd := NewCmdCreate()
	scriptCmd := findSubcommand(cmd, "script")
	if scriptCmd == nil {
		t.Fatal("script subcommand not found")
	}

	// script accepts 0 args (lists scripts) or 1 arg (runs a script)
	// override PreRunE/RunE to isolate arg validation
	scriptCmd.PreRunE = nil
	scriptCmd.RunE = func(cmd *cobra.Command, args []string) error { return nil }

	cmd.SetArgs([]string{"script"})
	if err := cmd.Execute(); err != nil {
		t.Errorf("expected no error with zero args, got: %v", err)
	}
}

func TestUpdateFlagParsing(t *testing.T) {
	cmd := NewCmdCreate()
	updateCmd := findSubcommand(cmd, "update")
	if updateCmd == nil {
		t.Fatal("update subcommand not found")
	}

	// Reset package-level variables
	skipJobs = false
	skipSMW = false

	if err := updateCmd.ParseFlags([]string{"--skip-jobs", "--skip-smw"}); err != nil {
		t.Fatalf("ParseFlags error: %v", err)
	}

	if !skipJobs {
		t.Error("--skip-jobs should set skipJobs to true")
	}
	if !skipSMW {
		t.Error("--skip-smw should set skipSMW to true")
	}
}

func TestWikiAndAllConflict(t *testing.T) {
	cmd := NewCmdCreate()
	updateCmd := findSubcommand(cmd, "update")
	if updateCmd == nil {
		t.Fatal("update subcommand not found")
	}

	// Override PreRunE to skip CheckCanastaId
	updateCmd.PreRunE = nil

	// Set wiki and all flags, then execute
	wiki = "docs"
	all = true
	defer func() {
		wiki = ""
		all = false
	}()

	cmd.SetArgs([]string{"update", "--wiki=docs", "--all"})
	err := cmd.Execute()
	if err == nil {
		t.Fatal("expected error when --wiki and --all are both set")
	}
	if err.Error() != "cannot use --wiki with --all" {
		t.Errorf("unexpected error: %v", err)
	}
}
