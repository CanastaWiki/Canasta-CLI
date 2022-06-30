package mediawiki

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/sethvargo/go-password/password"
	"golang.org/x/term"
)

func PromptUser(canastaId string, userVariables map[string]string) (string, map[string]string, error) {
	var err error
	userVariables["wikiName"], err = prompt(userVariables["wikiName"], "Wiki Name")
	if err != nil {
		logging.Fatal(err)
	}
	canastaId, err = prompt(canastaId, "Canasta ID")
	if err != nil {
		logging.Fatal(err)
	}
	userVariables["adminName"], userVariables["adminPassword"], err = promtUserPassword(userVariables["adminName"], userVariables["adminPassword"], "admin name", "admin password")
	if err != nil {
		logging.Fatal(err)
	}
	return canastaId, userVariables, nil
}

func Install(path, orchestrator string, userVariables map[string]string) (map[string]string, error) {
	logging.Print("Configuring Mediawiki Installation\n")
	logging.Print("Running install.php\n")
	infoCanasta := make(map[string]string)
	envVariables, err := canasta.GetEnvVariable(path + "/.env")
	if err != nil {
		logging.Fatal(err)
	}

	command := "/wait-for-it.sh -t 60 db:3306"
	if err = orchestrators.Exec(path, orchestrator, "web", command); err != nil {
		logging.Fatal(err)
	}

	if userVariables["adminPassword"] == "" {
		userVariables["adminPassword"], err = password.Generate(12, 2, 4, false, true)
		if err != nil {
			logging.Fatal(err)
		}
	}

	command = fmt.Sprintf("php maintenance/install.php --dbserver=db  --confpath=/mediawiki/config/ --scriptpath=/w	--dbuser='%s' --dbpass='%s' --pass='%s' '%s' '%s'",
		userVariables["dbUser"], envVariables["MYSQL_PASSWORD"], userVariables["adminPassword"], userVariables["wikiName"], userVariables["adminName"])

	if err = orchestrators.Exec(path, orchestrator, "web", command); err != nil {
		logging.Fatal(err)
	}

	return infoCanasta, err
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
		logging.Fatal(fmt.Errorf("please enter a value"))
	}
	return input, nil
}

func promtUserPassword(userValue, passwordValue, userPrompt, passwordPrompt string) (string, string, error) {
	username, err := prompt(userValue, userPrompt)
	if err != nil {
		logging.Fatal(err)
	}
	if passwordValue != "" {
		logging.Fatal(err)
	}
	fmt.Printf("Enter the  %s (Press Enter to autogenerate the password): \n", passwordPrompt)
	pass, err := term.ReadPassword(0)
	password := string(pass)
	if err != nil {
		logging.Fatal(err)
	}

	if password == "" {
		return username, password, nil
	}
	err = passwordCheck(username, password)
	if err != nil {
		logging.Fatal(err)
	}

	fmt.Printf("Re-enter the  %s: \n", passwordPrompt)
	pass, err = term.ReadPassword(0)
	if err != nil {
		logging.Fatal(err)
	}
	reEnterPassword := string(pass)

	if password != reEnterPassword {
		logging.Fatal(fmt.Errorf("please enter the same password"))
	}
	return username, password, nil
}

func passwordCheck(admin, password string) error {
	if len(password) <= 10 {
		logging.Fatal(fmt.Errorf("password must be atleast 10 characters long "))
	} else if strings.Contains(password, admin) || strings.Contains(admin, password) {
		logging.Fatal(fmt.Errorf("password should not be same as admin name"))
	}

	return nil
}
