package extensionsskins

import (
	"fmt"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

type Item struct {
	Name                     string
	RelativeInstallationPath string
	PhpCommand               string
}

func Contains(list []string, element string) bool {
	for _, item := range list {
		if item == element {
			return true
		}
	}
	return false
}

func List(instance logging.Installation, constants Item) {
	fmt.Printf("Available %s:\n", constants.Name)
	fmt.Print(orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls $MW_HOME/"+constants.RelativeInstallationPath))
}

func CheckInstalled(name string, instance logging.Installation, constants Item) (string, error) {
	output := orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls $MW_HOME/canasta-extensions")
	if !Contains(strings.Split(output, "\n"), name) {
		return "", fmt.Errorf("%s %s doesn't exist", name, constants.Name)
	}
	return name, nil
}

func Enable(extension string, instance logging.Installation, constants Item) {
	file := fmt.Sprintf("<?php\n%s( '%s' );", constants.PhpCommand, extension)
	command := fmt.Sprintf(`echo -e "%s" > /mediawiki/config/settings/%s.php`, file, extension)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", command)
	fmt.Printf("Extension %s enabled\n", extension)
}

func CheckEnabled(name string, instance logging.Installation, constants Item) (string, error) {
	output := orchestrators.Exec(instance.Path, instance.Orchestrator, "web", "ls /mediawiki/config/settings/")
	if !Contains(strings.Split(output, "\n"), name+".php") {
		return "", fmt.Errorf("%s %s is not enabled", name, constants.Name)
	}
	return name, nil
}

func Disable(name string, instance logging.Installation, constants Item) {
	command := fmt.Sprintf(`rm /mediawiki/config/settings/%s.php`, name)
	orchestrators.Exec(instance.Path, instance.Orchestrator, "web", command)
	fmt.Printf("%s %s disabled\n", constants.Name, name)
}
