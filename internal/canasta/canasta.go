package canasta

import (
	"fmt"
	"os/exec"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/git"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/orchestrators"
)

// CloneStackRepo accept the orchestrator from the cli and pass the corresponding reopository link
// and clones the repo to a new folder in the specified path
func CloneStackRepo(orchestrator string, path *string) error {
	fmt.Printf("Cloning the %s stack repo to %s \n", orchestrator, *path)

	*path += "/canasta-" + orchestrator
	repo, err := orchestrators.GetRepoLink(orchestrator)
	if err != nil {
		return err
	}
	err = git.Clone(repo, *path)
	if err != nil {
		return err
	}

	return nil
}

func CopyEnv(envPath, path, pwd string) error {
	var err error
	if envPath == "" {
		envPath = path + "/.env.example"
	} else {
		envPath = pwd + "/" + envPath
	}
	fmt.Printf("Copying %s to %s/.env\n", envPath, path)
	err = exec.Command("cp", envPath, path+"/.env").Run()
	if err != nil {
		return err
	}
	return nil
}

func CopyLocalSettings(localSettingsPath, path, pwd string) error {
	var err error
	if localSettingsPath != "" {
		localSettingsPath = pwd + "/" + localSettingsPath
		fmt.Printf("Copying %s to %s/config/LocalSettings.php\n", localSettingsPath, path)
		err = exec.Command("cp", localSettingsPath, path+"/config/LocalSettings.php").Run()
		if err != nil {
			return err
		}
	}
	return nil
}

func CopyDatabase(databasePath, path, pwd string) error {
	var err error
	if databasePath != "" {
		databasePath = pwd + "/" + databasePath
		fmt.Printf("Copying %s to %s/_initdb\n", databasePath, path)
		err = exec.Command("cp", databasePath, path+"/_initdb/").Run()
		if err != nil {
			return err
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
