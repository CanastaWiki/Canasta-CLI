package mediawiki

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
	"github.com/sethvargo/go-password/password"
	"golang.org/x/term"
)

const dbServer = "db"
const confPath = "/mediawiki/config/"
const scriptPath = "/w"

func PromptUser(name, yamlPath string, canastaInfo canasta.CanastaVariables) (string, canasta.CanastaVariables, error) {
	var err error
	if yamlPath == "" {
		name, err = prompt(name, "Wiki Name")
		if err != nil {
			return name, canastaInfo, err
		}
	}
	canastaInfo.Id, err = prompt(canastaInfo.Id, "Canasta ID")
	if err != nil {
		return name, canastaInfo, err
	}
	canastaInfo.AdminName, canastaInfo.AdminPassword, err = promptUserPassword(canastaInfo.AdminName, canastaInfo.AdminPassword)
	if err != nil {
		return name, canastaInfo, err
	}
	return name, canastaInfo, nil
}

func Install(path, yamlPath, orchestrator string, canastaInfo canasta.CanastaVariables) (canasta.CanastaVariables, error) {
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

	fmt.Printf("Saving adminname to %s/.admin\n", path)
	file, err := os.Create(path + "/.admin")
	if err != nil {
		return canastaInfo, err
	}
	defer file.Close()
	_, err = file.WriteString(canastaInfo.AdminName)
	if err != nil {
		return canastaInfo, err
	}

	WikiNames, domainNames, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return canastaInfo, err
	}
	for i := 0; i < len(WikiNames); i++ {
		wikiName := WikiNames[i]
		domainName := domainNames[i]

		command := fmt.Sprintf("php maintenance/install.php --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='http://%s' --dbuser='%s' --dbpass='%s'  --pass='%s' '%s' '%s'",
			dbServer, wikiName, confPath, scriptPath, domainName, "root", envVariables["MYSQL_PASSWORD"], canastaInfo.AdminPassword, wikiName, canastaInfo.AdminName)

		output, err = orchestrators.ExecWithError(path, orchestrator, "web", command)
		if err != nil {
			return canastaInfo, fmt.Errorf(output)
		}
		time.Sleep(time.Second)
		if i == 0 {
			err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettings.php"), filepath.Join(path, "config", "LocalSettingsBackup.php"))
			if err != nil {
				return canastaInfo, err
			}
		} else {
			err, _ = execute.Run(path, "rm", filepath.Join(path, "config", "LocalSettings.php"))
			if err != nil {
				return canastaInfo, err
			}
		}
		time.Sleep(time.Second)
	}
	err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettingsBackup.php"), filepath.Join(path, "config", "LocalSettings.php"))
	if err != nil {
		return canastaInfo, err
	}
	return canastaInfo, err
}

func InstallOne(path, name, domain, wikipath, orchestrator string) error {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	envVariables := canasta.GetEnvVariable(path + "/.env")

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}

	err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettings.php"), filepath.Join(path, "config", "LocalSettingsBackup.php"))
	if err != nil {
		return err
	}

	file, err := os.Open(filepath.Join(path, ".admin-password"))
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	scanner.Scan() // get the first line
	AdminPassword := scanner.Text()

	file, err = os.Open(filepath.Join(path, ".admin"))
	if err != nil {
		return err
	}
	defer file.Close()

	scanner = bufio.NewScanner(file)
	scanner.Scan() // get the first line
	AdminName := scanner.Text()

	command = fmt.Sprintf("php maintenance/install.php --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='http://%s' --dbuser='%s' --dbpass='%s'  --pass='%s' '%s' '%s'",
		dbServer, name, confPath, scriptPath, domain, "root", envVariables["MYSQL_PASSWORD"], AdminPassword, name, AdminName)
	output, err = orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}
	err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettingsBackup.php"), filepath.Join(path, "config", "LocalSettings.php"))
	if err != nil {
		return err
	}

	return err
}

func RemoveDatabase(path, name, orchestrator string) error {
	envVariables := canasta.GetEnvVariable(path + "/.env")
	command := fmt.Sprintf("echo 'DROP DATABASE IF EXISTS %s;' | mysql -h db -u root -p'%s'", name, envVariables["MYSQL_PASSWORD"])
	output, err := orchestrators.ExecWithError(path, orchestrator, "db", command)
	if err != nil {
		return fmt.Errorf("Error while dropping database '%s': %v. Output: %s", name, err, output)
	}

	return nil
}

func PromptWiki(name, domain, path, id string) (string, string, string, string, error) {
	var err error
	// Prompt for CanastaID if not provided
	id, err = prompt(id, "CanastaID")
	if err != nil {
		return name, domain, path, id, err
	}

	// Prompt for name if not provided
	name, err = prompt(name, "wiki name")
	if err != nil {
		return name, domain, path, id, err
	}

	// Prompt for domain if not provided
	domain, err = promptwithnull(domain, "domain name")
	if err != nil {
		return name, domain, path, id, err
	}

	// Prompt for path if not provided
	path, err = promptwithnull(path, "wiki directory")
	if err != nil {
		return name, domain, path, id, err
	}

	if err != nil {
		return name, domain, path, id, err
	}

	return name, domain, path, id, nil
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

func promptwithnull(value, prompt string) (string, error) {
	if value != "" {
		return value, nil
	}
	scanner := bufio.NewScanner(os.Stdin)
	fmt.Printf("Enter %s: ", prompt)
	scanner.Scan()
	input := scanner.Text()
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
	if len(password) < 10 {
		logging.Fatal(fmt.Errorf("Password must be at least 10 characters long "))
	} else if strings.Contains(password, admin) || strings.Contains(admin, password) {
		logging.Fatal(fmt.Errorf("Password should not be same as admin name"))
	}

	return nil
}
