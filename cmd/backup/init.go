package backup

import (
	"fmt"

	"github.com/spf13/cobra"
)

var (
	tag      string
	snapshot string
)

func initCmdCreate() *cobra.Command {

	initCmd := &cobra.Command{
		Use:   "init",
		Short: "Initialize a backup repository",
		Long: `Initialize a new backup repository. This must be run once before
creating any backups. The repository location is read from the
RESTIC_REPOSITORY variable (or AWS S3 settings) in the installation's .env file.`,
		Example: `  canasta backup init -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return initBackup()
		},
	}
	return initCmd
}

func initBackup() error {
	fmt.Println("Initializing backup repository")
	output, err := runBackup(nil, "-r", repoURL, "init")
	if err != nil {
		return err
	}
	fmt.Print(output)
	return nil
}
