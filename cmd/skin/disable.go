package skin

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/spf13/cobra"
)

func disableCmdCreate() *cobra.Command {

	disableCmd := &cobra.Command{
		Use:   "disable SKIN_NAME",
		Short: "Disable a canasta-skin",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			skinName, err := checkInstalledSkin(args[0])
			if err != nil {
				return err
			}
			disableSkin(skinName, instance)
			return err
		},
	}
	return disableCmd
}

func checkInstalledSkin(skinName string) (string, error) {
	output := orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls /mediawiki/config/settings/")
	if !contains(strings.Split(output, "\n"), skinName+".php") {
		return "", fmt.Errorf("%s canasta-skin is not enabled", skinName)
	}
	return skinName, nil
}

func disableSkin(skin string, instance logging.Installation) {
	command := fmt.Sprintf(`rm /mediawiki/config/settings/%s.php`, skin)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", command)
	fmt.Printf("Skin %s disabled\n", skin)
}
