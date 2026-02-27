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
	cmd := NewCmd()

	expected := []string{"update", "script", "extension", "exec"}
	for _, name := range expected {
		if findSubcommand(cmd, name) == nil {
			t.Errorf("expected subcommand %q not found", name)
		}
	}
}

func TestMaintenancePersistentFlags(t *testing.T) {
	cmd := NewCmd()

	flags := []struct {
		name      string
		shorthand string
	}{
		{"id", "i"},
		{"wiki", "w"},
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
	cmd := NewCmd()
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
	cmd := NewCmd()
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
	cmd := NewCmd()
	updateCmd := findSubcommand(cmd, "update")
	if updateCmd == nil {
		t.Fatal("update subcommand not found")
	}

	if err := updateCmd.ParseFlags([]string{"--skip-jobs", "--skip-smw"}); err != nil {
		t.Fatalf("ParseFlags error: %v", err)
	}

	skipJobs, err := updateCmd.Flags().GetBool("skip-jobs")
	if err != nil {
		t.Fatalf("GetBool skip-jobs error: %v", err)
	}
	if !skipJobs {
		t.Error("--skip-jobs should be true after parsing")
	}

	skipSMW, err := updateCmd.Flags().GetBool("skip-smw")
	if err != nil {
		t.Fatalf("GetBool skip-smw error: %v", err)
	}
	if !skipSMW {
		t.Error("--skip-smw should be true after parsing")
	}
}

