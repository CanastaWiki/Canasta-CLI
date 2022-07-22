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

const dbServer = "db"
const confPath = "/mediawiki/config/"
const scriptPath = "/w"

func PromptUser(canastaInfo canasta.CanastaVariables) (canasta.CanastaVariables, error) {
	var err error
	canastaInfo.WikiName, err = prompt(canastaInfo.WikiName, "Wiki Name")
	if err != nil {
		logging.Fatal(err)
	}
	canastaInfo.Id, err = prompt(canastaInfo.Id, "Canasta ID")
	if err != nil {
		logging.Fatal(err)
	}
	canastaInfo.AdminName, canastaInfo.AdminPassword, err = promptUserPassword(canastaInfo.AdminName, canastaInfo.AdminPassword, "admin name", "admin password")
	if err != nil {
		logging.Fatal(err)
	}
	return canastaInfo, nil
}

func Install(path, orchestrator string, canastaInfo canasta.CanastaVariables) (canasta.CanastaVariables, error) {
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	envVariables, err := canasta.GetEnvVariable(path + "/.env")
	if err != nil {
		logging.Fatal(err)
	}

	command := "/wait-for-it.sh -t 60 db:3306"
	if err = orchestrators.Exec(path, orchestrator, "web", command); err != nil {
		logging.Fatal(err)
	}

	if canastaInfo.AdminPassword == "" {
		canastaInfo.AdminPassword, err = password.Generate(12, 2, 4, false, true)
		if err != nil {
			logging.Fatal(err)
		}
	}

	command = fmt.Sprintf("php maintenance/install.php --dbserver=%s  --confpath=%s --scriptpath=%s	--server='%s' --dbuser='%s' --dbpass='%s' --pass='%s' '%s' '%s'",
		dbServer, confPath, scriptPath, canastaInfo.DomainName, "root", envVariables["MYSQL_PASSWORD"], canastaInfo.AdminPassword, canastaInfo.WikiName, canastaInfo.AdminName)

	if err = orchestrators.Exec(path, orchestrator, "web", command); err != nil {
		logging.Fatal(err)
	}

	return canastaInfo, err
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

func promptUserPassword(userValue, passwordValue, userPrompt, passwordPrompt string) (string, string, error) {
	username, err := prompt(userValue, userPrompt)
	if err != nil {
		logging.Fatal(err)
	}
	if passwordValue != "" {
		logging.Fatal(err)
	}
	fmt.Printf("Enter the %s (Press Enter to autogenerate the password): \n", passwordPrompt)
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

	fmt.Printf("Re-enter the %s: \n", passwordPrompt)
	pass, err = term.ReadPassword(0)
	if err != nil {
		logging.Fatal(err)
	}
	reEnterPassword := string(pass)

	if password != reEnterPassword {
		logging.Fatal(fmt.Errorf("Please enter the same password"))
	}
	return username, password, nil
}

func passwordCheck(admin, password string) error {
	if len(password) <= 10 {
		logging.Fatal(fmt.Errorf("Password must be at least 10 characters long "))
	} else if strings.Contains(password, admin) || strings.Contains(admin, password) {
		logging.Fatal(fmt.Errorf("Password should not be same as admin name"))
	}

	return nil
}
