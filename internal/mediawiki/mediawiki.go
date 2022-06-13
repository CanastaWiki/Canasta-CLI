package mediawiki

import (
	"fmt"
	"os"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

func getEnvVariable(envPath string) (map[string]string, error) {

	EnvVariables := make(map[string]string)
	file_data, err := os.ReadFile(envPath)
	if err != nil {
		return EnvVariables, err
	}
	data := strings.TrimSuffix(string(file_data), "\n")
	variable_list := strings.Split(data, "\n")
	for _, variable := range variable_list {
		list := strings.Split(variable, "=")
		EnvVariables[list[0]] = list[1]
	}

	return EnvVariables, nil
}

func Install(path, orchestrator, databasePath, localSettingsPath, envPath string) error {
	fmt.Println("Running install.php ")
	envVariables, err := getEnvVariable(path + "/.env")
	if err != nil {
		return err
	}
	command := "/wait-for-it.sh -t 60 db:3306"
	err = orchestrators.Exec(path, orchestrator, "web", command)
	if err != nil {
		return err
	}

	wiki_name := "My Wiki"
	admin_password := "92SPc27Dgi$^ADk"
	command = fmt.Sprintf("php maintenance/install.php --dbserver=db  --confpath=/mediawiki/config/ --scriptpath=/w	--dbuser='root' --dbpass='%s' --pass='%s' '%s' 'Admin'", envVariables["MYSQL_PASSWORD"], admin_password, wiki_name)
	//For Debugging
	fmt.Println(command)
	err = orchestrators.Exec(path, orchestrator, "web", command)

	return err
}
