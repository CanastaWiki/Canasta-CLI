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
		return canastaInfo, err
	}
	canastaInfo.Id, err = prompt(canastaInfo.Id, "Canasta ID")
	if err != nil {
		return canastaInfo, err
	}
	canastaInfo.AdminName, canastaInfo.AdminPassword, err = promptUserPassword(canastaInfo.AdminName, canastaInfo.AdminPassword)
	if err != nil {
		return canastaInfo, err
	}
	return canastaInfo, nil
}

func Install(path, orchestrator string, canastaInfo canasta.CanastaVariables) (canasta.CanastaVariables, error) {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	envVariables := canasta.GetEnvVariable(path + "/.env")

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return canastaInfo, fmt.Errorf(output)
	}
	if canastaInfo.AdminPassword == "" {
		canastaInfo.AdminPassword, err = password.Generate(12, 2, 4, false, true)
		if err != nil {
			return canastaInfo, err
		}
		// Save automatically generated password to .admin-password inside the configuration folder
		fmt.Printf("Saving password to %s/.admin-password\n", path)
		file, err := os.Create(path + "/.admin-password")
		if err != nil {
			return canastaInfo, err
		}
		defer file.Close()
		_, err = file.WriteString(canastaInfo.AdminPassword)
		if err != nil {
			return canastaInfo, err
		}
	}

	command = fmt.Sprintf("php maintenance/install.php --dbserver=%s  --confpath=%s --scriptpath=%s	--server='https://%s' --dbuser='%s' --dbpass='%s' --pass='%s' '%s' '%s'",
		dbServer, confPath, scriptPath, canastaInfo.DomainName, "root", envVariables["MYSQL_PASSWORD"], canastaInfo.AdminPassword, canastaInfo.WikiName, canastaInfo.AdminName)

	output, err = orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return canastaInfo, fmt.Errorf(output)
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

func promptUserPassword(userValue, passwordValue string) (string, string, error) {
	userPrompt, passwordPrompt := "admin name", "admin password"
	username, err := prompt(userValue, userPrompt)
	if err != nil {
		return "", "", err
	}
	if passwordValue != "" {
		return username, passwordValue, err
	}
	fmt.Printf("Enter the %s (Press Enter to autogenerate the password): \n", passwordPrompt)
	pass, err := term.ReadPassword(0)

	if err != nil {
		return "", "", err
	}
	password := string(pass)

	if password == "" {
		return username, password, nil
	}
	err = passwordCheck(username, password)
	if err != nil {
		return "", "", err
	}

	fmt.Printf("Re-enter the %s: \n", passwordPrompt)
	pass, err = term.ReadPassword(0)
	if err != nil {
		return "", "", err
	}
	reEnterPassword := string(pass)

	if password != reEnterPassword {
		return "", "", fmt.Errorf("Please enter the same password")
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
