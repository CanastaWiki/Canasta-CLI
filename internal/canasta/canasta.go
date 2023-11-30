package canasta

import (
	"fmt"
	"io/ioutil"
	"os"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/config"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/git"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

type CanastaVariables struct {
	Id            string
	WikiName      string
	DomainName    string
	AdminPassword string
	AdminName     string
}

// CloneStackRepo accept the orchestrator from the cli and pass the corresponding repository link
// and clones the repo to a new folder in the specified path
func CloneStackRepo(orchestrator, canastaId string, path *string) error {
	*path += "/" + canastaId
	logging.Print(fmt.Sprintf("Cloning the %s stack repo to %s \n", orchestrator, *path))
	repo := orchestrators.GetRepoLink(orchestrator)
	err := git.Clone(repo, *path)
	return err
}

//if envPath is passed as argument copies the file located at envPath to the installation directory
//else copies .env.example to .env in the installation directory
func CopyEnv(envPath, domainName, path, pwd string) error {
	if envPath == "" {
		envPath = path + "/.env.example"
	} else if !strings.HasPrefix(envPath, "/") {
		envPath = pwd + "/" + envPath
	}
	logging.Print(fmt.Sprintf("Copying %s to %s/.env\n", envPath, path))
	err, output := execute.Run("", "cp", envPath, path+"/.env")
	if err != nil {
		return fmt.Errorf(output)
	}
	if err := SaveEnvVariable(path+"/.env", "MW_SITE_SERVER", "https://"+domainName); err != nil {
		return err
	}
	if err := SaveEnvVariable(path+"/.env", "MW_SITE_FQDN", domainName); err != nil {
		return err
	}
	return nil
}

//Copies the LocalSettings.php at localSettingsPath to /config at the installation directory
func CopyLocalSettings(localSettingsPath, path, pwd string) error {
	if localSettingsPath != "" {
		if !strings.HasPrefix(localSettingsPath, "/") {
			localSettingsPath = pwd + "/" + localSettingsPath
		}
		logging.Print(fmt.Sprintf("Copying %s to %s/config/LocalSettings.php\n", localSettingsPath, path))
		err, output := execute.Run("", "cp", localSettingsPath, path+"/config/LocalSettings.php")
		if err != nil {
			logging.Fatal(fmt.Errorf(output))
		}
	}
	return nil
}

//Copies database dump from databasePath to the /_initdb/ at the installation directory
func CopyDatabase(databasePath, path, pwd string) error {
	if databasePath != "" {
		databasePath = pwd + "/" + databasePath
		logging.Print(fmt.Sprintf("Copying %s to %s/_initdb\n", databasePath, path))
		err, output := execute.Run("", "cp", databasePath, path+"/_initdb/")
		if err != nil {
			logging.Fatal(fmt.Errorf(output))
		}
	}
	return nil
}

//Verifying file extension for database dump
func SanityChecks(databasePath, localSettingsPath string) error {
	if databasePath == "" {
		return fmt.Errorf("database dump path not mentioned")
	}
	if localSettingsPath == "" {
		return fmt.Errorf("localsettings.php path not mentioned")
	}
	if !strings.HasSuffix(databasePath, ".sql") && !strings.HasSuffix(databasePath, ".sql.gz") {
		return fmt.Errorf("mysqldump is of invalid file type")
	}
	if !strings.HasSuffix(localSettingsPath, ".php") {
		return fmt.Errorf("make sure correct LocalSettings.php is mentioned")
	}
	return nil
}

//Make changes to the .env file at the installation directory
func SaveEnvVariable(envPath, key, value string) error {
	file, err := os.ReadFile(envPath)
	if err != nil {
		return err
	}
	data := string(file)
	list := strings.Split(data, "\n")
	for index, line := range list {
		if strings.Contains(line, key) {
			list[index] = fmt.Sprintf("%s=%s", key, value)
		}
	}
	lines := strings.Join(list, "\n")
	if err := ioutil.WriteFile(envPath, []byte(lines), 0644); err != nil {
		return err
	}
	return nil
}

//Get values saved inside the .env at the envPath
func GetEnvVariable(envPath string) map[string]string {
	EnvVariables := make(map[string]string)
	file_data, err := os.ReadFile(envPath)
	if err != nil {
		logging.Fatal(err)
	}
	data := strings.TrimSuffix(string(file_data), "\n")
	variable_list := strings.Split(data, "\n")
	for _, variable := range variable_list {
		list := strings.Split(variable, "=")
		if len(list) < 2 {
			continue
		}
		EnvVariables[list[0]] = list[1]
	}
	return EnvVariables
}

//Checking Installation existence
func CheckCanastaId(instance config.Installation) (config.Installation, error) {
	var err error
	if instance.Id != "" {
		if instance, err = config.GetDetails(instance.Id); err != nil {
			return instance, err
		}
	} else {
		if instance.Id, err = config.GetCanastaId(instance.Path); err != nil {
			return instance, err
		}
		if instance, err = config.GetDetails(instance.Id); err != nil {
			return instance, err
		}
	}
	return instance, nil
}

func DeleteConfigAndContainers(keepConfig bool, installationDir, orchestrator string) {
	fmt.Println("Removing containers")
	orchestrators.DeleteContainers(installationDir, orchestrator)
	fmt.Println("Deleting config files")
	orchestrators.DeleteConfig(installationDir)
	fmt.Println("Deleted all containers and config files")
}
