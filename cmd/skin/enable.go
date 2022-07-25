package skin

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func enableCmdCreate() *cobra.Command {

	enableCmd := &cobra.Command{
		Use:   "enable SKIN_NAME",
		Short: "Enable a canasta-skin",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			skinName, err := checkSkin(args[0])
			if err != nil {
				return err
			}
			enableSkin(skinName, instance)
			return err
		},
	}
	return enableCmd
}

func checkSkin(skinName string) (string, error) {
	output := orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls $MW_HOME/canasta-skins")
	if !contains(strings.Split(output, "\n"), skinName) {
		return "", fmt.Errorf("%s canasta-skin doesn't exist", skinName)
	}
	return skinName, nil
}

func enableSkin(skin string, instance logging.Installation) {
	file := fmt.Sprintf("<?php\ncfLoadSkin( '%s' );", skin)
	command := fmt.Sprintf(`echo -e "%s" > /mediawiki/config/settings/%s.php`, file, skin)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", command)
	fmt.Printf("Skin %s enabled\n", skin)
}
