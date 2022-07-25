package extension

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func enableCmdCreate() *cobra.Command {

	enableCmd := &cobra.Command{
		Use:   "enable EXTENSION",
		Short: "Enable a canasta-extension",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			extensionName, err := checkExtension(args[0])
			if err != nil {
				return err
			}
			enableSkin(extensionName, instance)
			return err
		},
	}
	return enableCmd
}

func checkExtension(extensionName string) (string, error) {
	output := orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls $MW_HOME/canasta-extensions")
	if !contains(strings.Split(output, "\n"), extensionName) {
		return "", fmt.Errorf("%s canasta-extension doesn't exist", extensionName)
	}
	return extensionName, nil
}

func enableSkin(extension string, instance logging.Installation) {
	file := fmt.Sprintf("<?php\ncfLoadExtension( '%s' );", extension)
	command := fmt.Sprintf(`echo -e "%s" > /mediawiki/config/settings/%s.php`, file, extension)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", command)
	fmt.Printf("Extension %s enabled\n", extension)
}
