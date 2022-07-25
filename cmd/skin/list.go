package skin

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func listCmdCreate() *cobra.Command {

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "Lists all the installed skins",
		RunE: func(cmd *cobra.Command, args []string) error {
			list(instance)
			return err
		},
	}

	return listCmd
}

func list(instance logging.Installation) {
	fmt.Printf("Available Canasta-skins:\n")
	fmt.Print(orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls $MW_HOME/canasta-skins"))
}
