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

func PromptUser(canastaId string, userVariables map[string]string) (string, map[string]string, error) {
	var err error
	userVariables["wikiName"], err = prompt(userVariables["wikiName"], "Wiki Name")
	if err != nil {
		return canastaId, userVariables, err
	}
	canastaId, err = prompt(canastaId, "Canasta ID")
	if err != nil {
		return canastaId, userVariables, err
	}
	userVariables["adminName"], userVariables["adminPassword"], err = promtUserPassword(userVariables["adminName"], userVariables["adminPassword"], "admin name", "admin password")
	if err != nil {
		return canastaId, userVariables, err
	}
	return canastaId, userVariables, nil
}

func Install(path, orchestrator string, userVariables map[string]string) (map[string]string, error) {
	fmt.Println("Configuring Mediawiki Installation")
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

func prompt(value, prompt string) (string, error) {
	if value != "" {
		return value, nil
	}
	scanner := bufio.NewScanner(os.Stdin)
	fmt.Printf("Enter %s: ", prompt)
	scanner.Scan()
	input := scanner.Text()
	if input == "" {
		return input, fmt.Errorf("please enter a value")
	}
	return input, nil
}

func promtUserPassword(userValue, passwordValue, userPrompt, passwordPrompt string) (string, string, error) {
	username, err := prompt(userValue, userPrompt)
	if err != nil {
		return userValue, passwordValue, err
	}
	if passwordValue != "" {
		return userValue, passwordValue, nil
	}
	fmt.Printf("Enter the  %s (Press Enter to autogenerate the password): \n", passwordPrompt)
	pass, err := term.ReadPassword(0)
	password := string(pass)
	if err != nil {
		return username, password, err
	}

	if password == "" {
		return username, password, nil
	}
	err = passwordCheck(username, password)
	if err != nil {
		return username, password, err
	}

	fmt.Printf("Re-enter the  %s: \n", passwordPrompt)
	pass, err = term.ReadPassword(0)
	if err != nil {
		return username, password, err
	}
	reEnterPassword := string(pass)

	if password == reEnterPassword {
		return username, password, nil
	} else {
		return username, password, fmt.Errorf("please enter the same password")
	}
}

func passwordCheck(admin, password string) error {
	if len(password) <= 10 {
		return fmt.Errorf("password must be atleast 10 characters long ")
	} else if strings.Contains(password, admin) || strings.Contains(admin, password) {
		return fmt.Errorf("password should not be same as admin name")
	}

	return nil
}
