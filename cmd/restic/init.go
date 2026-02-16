package restic

import (
	"fmt"

	"github.com/spf13/cobra"
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
			return initRestic()
		},
	}
	return initCmd
}

func initRestic() error {
	fmt.Println("Initializing Restic repo")
	output, err := runRestic(nil, "-r", repoURL, "init")
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
