package extension

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func disableCmdCreate() *cobra.Command {

	disableCmd := &cobra.Command{
		Use:   "disable EXTENSION",
		Short: "Disable a canasta-extension",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			extensionName, err := checkInstalledExtension(args[0])
			if err != nil {
				return err
			}
			disableExtension(extensionName, instance)
			return err
		},
	}
	return disableCmd
}

func checkInstalledExtension(skinName string) (string, error) {
	output := orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls /mediawiki/config/settings/")
	if !contains(strings.Split(output, "\n"), skinName+".php") {
		return "", fmt.Errorf("%s canasta-extension is not enabled", skinName)
	}
	return skinName, nil
}

func disableExtension(extension string, instance logging.Installation) {
	command := fmt.Sprintf(`rm /mediawiki/config/settings/%s.php`, extension)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", command)
	fmt.Printf("Extension %s disabled\n", extension)
}
