package mediawiki

import (
	"fmt"
	"os"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func getEnvVariable(envPath string) (map[string]int, error) {

	EnvVariables := make(map[string]int)
	content, err := os.ReadFile(envPath)
	if err != nil {
		return EnvVariables, err
	}
	fmt.Println(string(content))

	return EnvVariables, nil
}

func Install(path, orchestrator, databasePath, localSettingsPath, envPath string) error {
	fmt.Println("Running install.php ")
	getEnvVariable(path + ".env")
	command := "/wait-for-it.sh -t 60 db:3306"
	err := orchestrators.Exec(path, orchestrator, "web", command)
	if err != nil {
		return err
	}

	command = "php maintenance/install.php --dbserver=db  --confpath=/mediawiki/config/ --dbuser='root' --dbpass='mediawiki' --pass='92SPc27Dgi$^ADk' 'My Wiki' 'Admin'"
	err = orchestrators.Exec(path, orchestrator, "web", command)

	return err
}
