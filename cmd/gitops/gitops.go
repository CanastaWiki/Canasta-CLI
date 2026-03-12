package gitops

import (
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

// NewCmd creates the "canasta gitops" parent command.
func NewCmd() *cobra.Command {
	var instance config.Installation

	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		logging.Fatal(wdErr)
	}
	instance.Path = workingDir

	gitopsCmd := &cobra.Command{
		Use:   "gitops",
		Short: "Git-based configuration management",
		Long: `Manage Canasta installation configuration through a Git repository.
Supports version-controlled configuration backup, encrypted secrets via
git-crypt, and multi-server deployments with push/pull workflows.`,
		PersistentPreRunE: func(_ *cobra.Command, _ []string) error {
			var err error
			instance, err = canasta.CheckCanastaID(instance)
			return err
		},
	}

	gitopsCmd.AddCommand(newInitCmd(&instance))
	gitopsCmd.AddCommand(newJoinCmd(&instance))
	gitopsCmd.AddCommand(newAddCmd(&instance))
	gitopsCmd.AddCommand(newRmCmd(&instance))
	gitopsCmd.AddCommand(newPushCmd(&instance))
	gitopsCmd.AddCommand(newPullCmd(&instance))
	gitopsCmd.AddCommand(newStatusCmd(&instance))
	gitopsCmd.AddCommand(newDiffCmd(&instance))

	gitopsCmd.PersistentFlags().StringVarP(&instance.ID, "id", "i", "", "Canasta instance ID")
	return gitopsCmd
}
