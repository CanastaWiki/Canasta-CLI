package gitops

import (
	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

func newFixSubmodulesCmd(instance *config.Instance) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "fix-submodules",
		Short: "Convert extensions and skins to proper git submodules",
		Long: `Fix submodule registration in an existing gitops repository.

This handles two cases:
  1. Extensions or skins that were added after gitops init and need to be
     converted from standalone git repositories to submodules.
  2. Submodules listed in .gitmodules that were accidentally committed as
     regular directories instead of gitlinks.

After fixing, run "canasta gitops push" to push the changes.`,
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return runRepairSubmodules(instance.Path)
		},
	}
	return cmd
}
