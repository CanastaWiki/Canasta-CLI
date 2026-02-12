package restic

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

var (
	tag string
)

func initCmdCreate() *cobra.Command {

	initCmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize a restic repo",
		Long: `Initialize a new Restic backup repository. This must be run once before
taking any snapshots. The repository location is read from the
RESTIC_REPOSITORY variable (or AWS S3 settings) in the installation's .env file.`,
		Example: `  canasta restic init -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			initRestic()
			return nil
		},
	}
	return initCmd
}

func initRestic() {
	fmt.Println("Initializing Restic repo")
	commandArgs = append(commandArgs, "init")
	err, output := execute.Run(instance.Path, commandArgs[0], commandArgs[1:]...)
	if err != nil {
		logging.Fatal(fmt.Errorf("%s", output))
	} else {
		fmt.Print(output)
	}
}
