package mediawiki

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/sethvargo/go-password/password"
	"golang.org/x/term"
)

func PromptUser(userVariables map[string]string) (map[string]string, error) {
	for index, value := range userVariables {

		scanner := bufio.NewScanner(os.Stdin)
		if strings.Contains(index, "password") || strings.Contains(index, "Password") {

			password, err := promtPassword(index)
			if err != nil {
				return userVariables, err
			}
			userVariables[index] = password

		} else if value == "" {

			fmt.Printf("Enter %s\n", index)
			scanner.Scan()
			input := scanner.Text()
			userVariables[index] = input
		}
	}

	return userVariables, nil
}

func Install(path, orchestrator, databasePath, localSettingsPath, envPath string, userVariables map[string]string) (map[string]string, error) {
	fmt.Println("Running install.php ")

	infoCanasta := make(map[string]string)
	envVariables, err := getEnvVariable(path + "/.env")
	if err != nil {
		return infoCanasta, err
	}

	command := "/wait-for-it.sh -t 60 db:3306"
	err = orchestrators.Exec(path, orchestrator, "web", command)
	if err != nil {
		return infoCanasta, err
	}

	if userVariables["adminPassword"] == "" {
		userVariables["adminPassword"], err = password.Generate(12, 2, 4, false, true)
		if err != nil {
			return infoCanasta, err
		}
	}

	command = fmt.Sprintf("php maintenance/install.php --dbserver=db  --confpath=/mediawiki/config/ --scriptpath=/w	--dbuser='%s' --dbpass='%s' --pass='%s' '%s' '%s'",
		userVariables["dbUser"], envVariables["MYSQL_PASSWORD"], userVariables["adminPassword"], userVariables["wikiName"], userVariables["adminName"])

	err = orchestrators.Exec(path, orchestrator, "web", command)

	return infoCanasta, err
}

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

func promtPassword(name string) (string, error) {
	fmt.Printf("Enter the  %s (Press Enter to autogenerate the password)\n", name)
	pass, err := term.ReadPassword(0)
	if err != nil {
		return "", err
	}
	password := string(pass)

	if password == "" {
		return password, nil
	}

	fmt.Printf("Re-enter the  %s\n", name)
	pass, err = term.ReadPassword(0)
	if err != nil {
		return "", err
	}
	reEnterPassword := string(pass)

	if password == reEnterPassword {
		return password, nil
	} else {
		return "", fmt.Errorf("please enter the same password")
	}
}
