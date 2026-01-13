package mediawiki

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

const dbServer = "db"
const confPath = "/mediawiki/config/"
const scriptPath = "/w"

func Install(path, yamlPath, orchestrator string, canastaInfo canasta.CanastaVariables) (canasta.CanastaVariables, error) {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	settingsName := "CommonSettings.php"

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orchestrators.ExecWithError(path, orchestrator, "web", command)
	if err != nil {
		return canastaInfo, fmt.Errorf(output)
	}

	WikiIDs, domainNames, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return canastaInfo, err
	}
	for i := 0; i < len(WikiIDs); i++ {
		wikiID := WikiIDs[i]
		domainName := domainNames[i]

		command := fmt.Sprintf("php maintenance/install.php --skins='Vector' --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='https://%s' --installdbuser='%s' --installdbpass='%s' --dbuser='%s' --dbpass='%s' --pass='%s' '%s' '%s'",
			dbServer, wikiID, confPath, scriptPath, domainName, "root", canastaInfo.RootDBPassword, canastaInfo.WikiDBUsername, canastaInfo.WikiDBPassword, canastaInfo.AdminPassword, wikiID, canastaInfo.AdminName)

		output, err = orchestrators.ExecWithError(path, orchestrator, "web", command)
		if err != nil {
			return canastaInfo, fmt.Errorf(output)
		}

		// Save admin password to config/admin-password_{wikiid}
		configDir := filepath.Join(path, "config")
		passwordFile := fmt.Sprintf("admin-password_%s", wikiID)
		if err := canasta.SavePasswordToFile(configDir, passwordFile, canastaInfo.AdminPassword); err != nil {
			return canastaInfo, err
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

	if len(WikiIDs) == 1 {
		settingsName = "LocalSettings.php"
	}

	err, _ = execute.Run(path, "mv", filepath.Join(path, "config", "LocalSettingsBackup.php"), filepath.Join(path, "config", settingsName))
	if err != nil {
		return canastaInfo, err
	}
	return canastaInfo, err
}

func InstallOne(installPath, id, domain, admin, adminPassword, dbuser, workingDir, orchestrator string) error {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	envVariables := canasta.GetEnvVariable(installPath + "/.env")

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orchestrators.ExecWithError(installPath, orchestrator, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}

	localExists, _ := fileExists(filepath.Join(installPath, "config", "LocalSettings.php"))
	commonExists, _ := fileExists(filepath.Join(installPath, "config", "CommonSettings.php"))

	if !localExists && !commonExists {
		return fmt.Errorf("Neither LocalSettings.php nor CommonSettings.php exist in the path")
	}

	// Track which settings file we're preserving
	var originalSettingsFile string
	if commonExists {
		// Farm already exists with CommonSettings.php - preserve it
		originalSettingsFile = "CommonSettings.php"
		// Backup the file
		err, _ = execute.Run(installPath, "cp", filepath.Join(installPath, "config", "CommonSettings.php"), filepath.Join(installPath, "config", "CommonSettingsBackup.php"))
		if err != nil {
			return err
		}
		// Remove the original so installer doesn't see it
		err, _ = execute.Run(installPath, "rm", filepath.Join(installPath, "config", "CommonSettings.php"))
		if err != nil {
			return err
		}
	} else if localExists {
		// Converting from single wiki (LocalSettings.php) to farm
		originalSettingsFile = "LocalSettings.php"
		// Backup the file
		err, _ = execute.Run(installPath, "cp", filepath.Join(installPath, "config", "LocalSettings.php"), filepath.Join(installPath, "config", "LocalSettingsBackup.php"))
		if err != nil {
			return err
		}
		// Remove the original so installer doesn't see it
		err, _ = execute.Run(installPath, "rm", filepath.Join(installPath, "config", "LocalSettings.php"))
		if err != nil {
			return err
		}
	}

	installdbuser := "root"
	installdbpass := envVariables["MYSQL_PASSWORD"]
	var dbpass string
	if dbuser != installdbuser {
		// Read wiki DB password from .env (same source as root password)
		dbpass = envVariables["WIKI_DB_PASSWORD"]
		if dbpass == "" {
			return fmt.Errorf("WIKI_DB_PASSWORD not found in .env file")
		}
	} else {
		dbpass = installdbpass
	}

	// Use admin password (should have been generated/provided by caller)
	command = fmt.Sprintf("php maintenance/install.php --skins='Vector' --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='https://%s' --installdbuser='%s' --installdbpass='%s' --dbuser='%s' --dbpass='%s'  --pass='%s' '%s' '%s'",
		dbServer, id, confPath, scriptPath, domain, installdbuser, installdbpass, dbuser, dbpass, adminPassword, id, admin)
	output, err = orchestrators.ExecWithError(installPath, orchestrator, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}

	// Save admin password to config/admin-password_{wikiid}
	configDir := filepath.Join(installPath, "config")
	passwordFile := fmt.Sprintf("admin-password_%s", id)
	if err := canasta.SavePasswordToFile(configDir, passwordFile, adminPassword); err != nil {
		return err
	}

	// Restore the original settings file as CommonSettings.php
	if originalSettingsFile == "CommonSettings.php" {
		// Farm already existed, restore CommonSettings.php from backup
		err, _ = execute.Run(installPath, "mv", filepath.Join(installPath, "config", "CommonSettingsBackup.php"), filepath.Join(installPath, "config", "CommonSettings.php"))
		if err != nil {
			return err
		}
	} else if originalSettingsFile == "LocalSettings.php" {
		// Converting single wiki to farm: rename backup to CommonSettings.php
		err, _ = execute.Run(installPath, "mv", filepath.Join(installPath, "config", "LocalSettingsBackup.php"), filepath.Join(installPath, "config", "CommonSettings.php"))
		if err != nil {
			return err
		}
	}

	// Always remove the newly generated LocalSettings.php (not needed in farm)
	err, _ = execute.Run(installPath, "rm", filepath.Join(installPath, "config", "LocalSettings.php"))
	if err != nil {
		return err
	}

	return err
}

func RemoveDatabase(installPath, id, orchestrator string) error {
	envVariables := canasta.GetEnvVariable(installPath + "/.env")
	command := fmt.Sprintf("echo 'DROP DATABASE IF EXISTS %s;' | mysql -h db -u root -p'%s'", id, envVariables["MYSQL_PASSWORD"])
	output, err := orchestrators.ExecWithError(installPath, orchestrator, "db", command)
	if err != nil {
		return fmt.Errorf("Error while dropping database '%s': %v. Output: %s", id, err, output)
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
