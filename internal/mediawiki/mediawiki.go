package mediawiki

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
	"bufio"
	"regexp"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

const dbServer = "db"
const confPath = "/mediawiki/config/"
const scriptPath = "/w"

func updatePHPArrayVariables(filePath, variableName string, updates map[string] interface{}) error {
	file, err := os.Open(filePath)
	if err != nil {
		return fmt.Errorf("failed to open file: %v", err)
	}
	defer file.Close()

	var lines []string
	insideArray := false
	arrayFound := false
	updatedKeys := make(map[string]bool)
	arrayStartRegex := regexp.MustCompile(`^\s*\$` + regexp.QuoteMeta(variableName) + `\s*=\s*\[`)
	arrayKeyRegex := func(key string) *regexp.Regexp {
		return regexp.MustCompile(`^\s*['"]?` + regexp.QuoteMeta(key) + `['"]?\s*=>`)
	}

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()

		if arrayStartRegex.MatchString(line) {
			insideArray = true
			arrayFound = true
		}

		if insideArray {
			for key, newValue := range updates {
				if arrayKeyRegex(key).MatchString(line) {
					line = fmt.Sprintf("    '%s' => %s,", key, formatPHPValue(newValue))
					updatedKeys[key] = true
				}
			}

			if strings.TrimSpace(line) == "];" {
				insideArray = false
				for key, newValue := range updates {
					if !updatedKeys[key] {
						if len(lines) > 0 && !strings.HasSuffix(strings.TrimSpace(lines[len(lines)-1]), ",") {
							lines[len(lines)-1] += ","
						}
						lines = append(lines, fmt.Sprintf("    '%s' => %s,", key, formatPHPValue(newValue)))
						updatedKeys[key] = true
					}
				}
				line = "];"
			}
		}

		lines = append(lines, line)
	}

	if err := scanner.Err(); err != nil {
		return fmt.Errorf("failed to read file: %v", err)
	}

	if !arrayFound {
		lines = append(lines, fmt.Sprintf("$%s = [", variableName))
		for key, newValue := range updates {
			lines = append(lines, fmt.Sprintf("    '%s' => %s,", key, formatPHPValue(newValue)))
		}
		lines[len(lines)-1] = strings.TrimSuffix(lines[len(lines)-1], ",") + "\n];"
	}

	file, err = os.Create(filePath)
	if err != nil {
		return fmt.Errorf("failed to open file for writing: %v", err)
	}
	defer file.Close()

	writer := bufio.NewWriter(file)
	for _, line := range lines {
		_, err := writer.WriteString(line + "\n")
		if err != nil {
			return fmt.Errorf("failed to write to file: %v", err)
		}
	}
	return writer.Flush()
}

func formatPHPValue(value interface{}) string {
	switch v := value.(type) {
	case string:
		return fmt.Sprintf("'%s'", v)
	case bool:
		if v {
			return "true"
		}
		return "false"
	default:
		return fmt.Sprintf("%v", v)
	}
}

func Install(path, yamlPath, orchestrator string, canastaInfo canasta.CanastaVariables, wgsmtp map[string]interface{}) (canasta.CanastaVariables, error) {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	settingsName := "CommonSettings.php"

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return canastaInfo, fmt.Errorf(output)
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

		command := fmt.Sprintf("php maintenance/install.php --skins='Vector' --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='https://%s' --installdbuser='%s' --installdbpass='%s' --dbuser='%s' --dbpass='%s' --pass='%s' '%s' '%s'",
			dbServer, wikiName, confPath, scriptPath, domainName, "root", canastaInfo.RootDBPassword, canastaInfo.WikiDBUsername, canastaInfo.WikiDBPassword, canastaInfo.AdminPassword, wikiName, canastaInfo.AdminName)

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

	if len(WikiNames) == 1 {
		settingsName = "LocalSettings.php"
	}

	err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettingsBackup.php"), filepath.Join(path, "config", settingsName))
	if err != nil {
		return canastaInfo, err
	}
	if wgsmtp["mailer"] == true {
		err := updatePHPArrayVariables(filepath.Join(path, "config", settingsName), "wgSMTP", wgsmtp)
		if err != nil {
			fmt.Printf("Error: %v\n", err)
		} else {
			fmt.Printf("Mailer details updated successfully.\n")
		}
	}
	return canastaInfo, err
}

func InstallOne(path, name, domain, admin, dbuser, orchestrator string) error {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	envVariables := canasta.GetEnvVariable(path + "/.env")

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}

	localExists, _ := fileExists(filepath.Join(path, "config", "LocalSettings.php"))
	commonExists, _ := fileExists(filepath.Join(path, "config", "CommonSettings.php"))

	if !localExists && !commonExists {
		return fmt.Errorf("Neither LocalSettings.php nor CommonSettings.php exist in the path")
	}

	if commonExists {
		err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "CommonSettings.php"), filepath.Join(path, "config", "CommonSettingsBackup.php"))
		if err != nil {
			return err
		}
	}

	if localExists {
		err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettings.php"), filepath.Join(path, "config", "LocalSettingsBackup.php"))
		if err != nil {
			return err
		}
	}

	installdbuser := "root"
	installdbpass := envVariables["MYSQL_PASSWORD"]
	var dbpass string
	if dbuser != installdbuser {
		dbpass, err = canasta.GetPasswordFromFile(path, ".wiki-db-password")
		if err != nil {
			return err
		}
	} else {
		dbpass = installdbpass
	}

	AdminPassword, err := canasta.GetPasswordFromFile(path, ".admin-password")
	if err != nil {
		return err
	}

	command = fmt.Sprintf("php maintenance/install.php --skins='Vector' --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='https://%s' --installdbuser='%s' --installdbpass='%s' --dbuser='%s' --dbpass='%s'  --pass='%s' '%s' '%s'",
		dbServer, name, confPath, scriptPath, domain, installdbuser, installdbpass, dbuser, dbpass, AdminPassword, name, admin)
	output, err = orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}

	if localExists {
		err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettingsBackup.php"), filepath.Join(path, "config", "CommonSettings.php"))
		if err != nil {
			return err
		}
	}

	if commonExists {
		err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "CommonSettingsBackup.php"), filepath.Join(path, "config", "CommonSettings.php"))
		if err != nil {
			return err
		}
	}

	err, _ = execute.Run(path, "rm", filepath.Join(path, "config", "LocalSettings.php"))
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

func fileExists(path string) (bool, error) {
	_, err := os.Stat(path)
	if err == nil {
		return true, nil
	}
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return false, err
}
