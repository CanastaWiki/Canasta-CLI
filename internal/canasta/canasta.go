package canasta

import (
	"fmt"
	"io/ioutil"
	"log"
	"os"
	"os/exec"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/git"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

// CloneStackRepo accept the orchestrator from the cli and pass the corresponding reopository link
// and clones the repo to a new folder in the specified path
func CloneStackRepo(orchestrator string, path *string) error {
	logging.Print(fmt.Sprintf("Cloning the %s stack repo to %s \n", orchestrator, *path))
	*path += "/canasta-" + orchestrator
	repo, err := orchestrators.GetRepoLink(orchestrator)
	if err != nil {
		logging.Fatal(err)
	}
	err = git.Clone(repo, *path)
	if err != nil {
		return err
	}

	return nil
}

func CopyEnv(envPath, domainName, path, pwd string) error {
	var err error
	if envPath == "" {
		envPath = path + "/.env.example"
	} else {
		envPath = pwd + "/" + envPath
	}
	logging.Print(fmt.Sprintf("Copying %s to %s/.env\n", envPath, path))
	err = exec.Command("cp", envPath, path+"/.env").Run()
	if err != nil {
		logging.Fatal(err)
	}
	if err = SaveEnvVariable(path+"/.env", "MW_SITE_SERVER", "https://"+domainName); err != nil {
		logging.Fatal(err)
	}
	if err = SaveEnvVariable(path+"/.env", "MW_SITE_FQDN", domainName); err != nil {
		logging.Fatal(err)
	}
	return nil
}

func CopyLocalSettings(localSettingsPath, path, pwd string) error {
	var err error
	if localSettingsPath != "" {
		localSettingsPath = pwd + "/" + localSettingsPath
		logging.Print(fmt.Sprintf("Copying %s to %s/config/LocalSettings.php\n", localSettingsPath, path))
		err = exec.Command("cp", localSettingsPath, path+"/config/LocalSettings.php").Run()
		if err != nil {
			logging.Fatal(err)
		}
	}
	return nil
}

func CopyDatabase(databasePath, path, pwd string) error {
	var err error
	if databasePath != "" {
		databasePath = pwd + "/" + databasePath
		logging.Print(fmt.Sprintf("Copying %s to %s/_initdb\n", databasePath, path))
		err = exec.Command("cp", databasePath, path+"/_initdb/").Run()
		if err != nil {
			logging.Fatal(err)
		}
	}
	return nil
}

//sanity checks database dump file
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
	err = ioutil.WriteFile(envPath, []byte(lines), 0644)
	if err != nil {
		log.Fatalln(err)
	}

	return nil
}

func GetEnvVariable(envPath string) (map[string]string, error) {

	EnvVariables := make(map[string]string)
	file_data, err := os.ReadFile(envPath)
	if err != nil {
		logging.Fatal(err)
	}
	data := strings.TrimSuffix(string(file_data), "\n")
	variable_list := strings.Split(data, "\n")
	for _, variable := range variable_list {
		list := strings.Split(variable, "=")
		EnvVariables[list[0]] = list[1]
	}

	return EnvVariables, nil
}
