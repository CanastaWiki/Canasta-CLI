package mediawiki

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

const (
	dbServer             = "db"
	confPath             = "/mediawiki/config/"
	scriptPath           = "/w"
	localSettingsFile    = "LocalSettings.php"
	commonSettingsFile   = "CommonSettings.php"
	localSettingsBackup  = "LocalSettingsBackup.php"
	commonSettingsBackup = "CommonSettingsBackup.php"
)

func Install(path, yamlPath string, orch orchestrators.Orchestrator, canastaInfo canasta.CanastaVariables) (canasta.CanastaVariables, error) {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orch.ExecWithError(path, "web", command)
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

		// Unset MW_SECRET_KEY so CanastaDefaultSettings.php doesn't think wiki is already configured
		command := fmt.Sprintf("env -u MW_SECRET_KEY php maintenance/install.php --skins='Vector' --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='https://%s' --installdbuser='%s' --installdbpass='%s' --dbuser='%s' --dbpass='%s' --pass='%s' '%s' '%s'",
			dbServer, wikiID, confPath, scriptPath, domainName, "root", canastaInfo.RootDBPassword, canastaInfo.WikiDBUsername, canastaInfo.WikiDBPassword, canastaInfo.AdminPassword, wikiID, canastaInfo.AdminName)

		output, err = orch.ExecWithError(path, "web", command)
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

		// For the first wiki, ensure MW_SECRET_KEY is in .env
		if i == 0 {
			envPath := filepath.Join(path, ".env")
			envVars := canasta.GetEnvVariable(envPath)
			if val, ok := envVars["MW_SECRET_KEY"]; ok && val != "" {
				logging.Print("MW_SECRET_KEY already set in .env, skipping extraction\n")
			} else {
				localSettingsPath := filepath.Join(path, "config", localSettingsFile)
				content, err := os.ReadFile(localSettingsPath)
				if err != nil {
					return canastaInfo, fmt.Errorf("failed to read LocalSettings.php: %w", err)
				}

				secretKeyRegex := regexp.MustCompile(`\$wgSecretKey\s*=\s*["']([0-9a-fA-F]+)["']`)
				matches := secretKeyRegex.FindSubmatch(content)
				if matches == nil {
					return canastaInfo, fmt.Errorf("could not find $wgSecretKey in LocalSettings.php")
				}
				secretKey := string(matches[1])

				if err := canasta.SaveEnvVariable(envPath, "MW_SECRET_KEY", secretKey); err != nil {
					return canastaInfo, fmt.Errorf("failed to save MW_SECRET_KEY to .env: %w", err)
				}
				logging.Print("Extracted MW_SECRET_KEY from LocalSettings.php to .env\n")
			}
		}

		// Delete the installer-generated LocalSettings.php. The installer creates a LocalSettings.php
		// for each wiki, but they are identical except for $wgSecretKey. We only need to extract the
		// secret key from the first wiki's file (done above when i == 0), after which all these
		// generated files are unnecessary—Canasta uses its own LocalSettings.php that reads
		// MW_SECRET_KEY from the environment.
		var rmOutput string
		err, rmOutput = execute.Run(path, "rm", filepath.Join(path, "config", localSettingsFile))
		if err != nil {
			return canastaInfo, fmt.Errorf("failed to remove LocalSettings.php: %w: %s", err, rmOutput)
		}

		time.Sleep(time.Second)
	}

	return canastaInfo, nil
}

func InstallOne(installPath, id, domain, admin, adminPassword, dbuser, workingDir string, orch orchestrators.Orchestrator) error {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	envVariables := canasta.GetEnvVariable(installPath + "/.env")

	command := "/wait-for-it.sh -t 60 db:3306"
	output, err := orch.ExecWithError(installPath, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}

	localExists, _ := fileExists(filepath.Join(installPath, "config", localSettingsFile))
	commonExists, _ := fileExists(filepath.Join(installPath, "config", commonSettingsFile))
	wikisYamlExists, _ := fileExists(filepath.Join(installPath, "config", "wikis.yaml"))

	if !localExists && !commonExists && !wikisYamlExists {
		return fmt.Errorf("No valid configuration found (wikis.yaml, LocalSettings.php, or CommonSettings.php)")
	}

	// New architecture uses wikis.yaml without config/LocalSettings.php
	useNewArchitecture := wikisYamlExists && !localExists && !commonExists

	// Track which settings file we're preserving (legacy architecture only)
	var originalSettingsFile string
	if useNewArchitecture {
		// New architecture: no config files to backup/remove
		// We'll unset MW_SECRET_KEY when running install.php so CanastaDefaultSettings.php
		// doesn't think the wiki is already configured
	} else if commonExists {
		// Farm already exists with CommonSettings.php - preserve it
		originalSettingsFile = commonSettingsFile
		// Backup the file
		err, _ = execute.Run(installPath, "cp", filepath.Join(installPath, "config", commonSettingsFile), filepath.Join(installPath, "config", commonSettingsBackup))
		if err != nil {
			return err
		}
		// Remove the original so installer doesn't see it
		err, _ = execute.Run(installPath, "rm", filepath.Join(installPath, "config", commonSettingsFile))
		if err != nil {
			return err
		}
	} else if localExists {
		// Converting from single wiki (LocalSettings.php) to farm
		originalSettingsFile = localSettingsFile
		// Backup the file
		err, _ = execute.Run(installPath, "cp", filepath.Join(installPath, "config", localSettingsFile), filepath.Join(installPath, "config", localSettingsBackup))
		if err != nil {
			return err
		}
		// Remove the original so installer doesn't see it
		err, _ = execute.Run(installPath, "rm", filepath.Join(installPath, "config", localSettingsFile))
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
	// For new architecture, unset MW_SECRET_KEY so CanastaDefaultSettings.php doesn't think wiki is configured
	var installCmd string
	if useNewArchitecture {
		installCmd = "env -u MW_SECRET_KEY php maintenance/install.php"
	} else {
		installCmd = "php maintenance/install.php"
	}
	command = fmt.Sprintf("%s --skins='Vector' --dbserver=%s --dbname='%s' --confpath=%s --scriptpath=%s --server='https://%s' --installdbuser='%s' --installdbpass='%s' --dbuser='%s' --dbpass='%s'  --pass='%s' '%s' '%s'",
		installCmd, dbServer, id, confPath, scriptPath, domain, installdbuser, installdbpass, dbuser, dbpass, adminPassword, id, admin)
	output, err = orch.ExecWithError(installPath, "web", command)
	if err != nil {
		return fmt.Errorf(output)
	}

	// Save admin password to config/admin-password_{wikiid}
	configDir := filepath.Join(installPath, "config")
	passwordFile := fmt.Sprintf("admin-password_%s", id)
	if err := canasta.SavePasswordToFile(configDir, passwordFile, adminPassword); err != nil {
		return err
	}

	// Restore the original settings file as CommonSettings.php (legacy architecture only)
	if useNewArchitecture {
		// New architecture: nothing to restore
	} else if originalSettingsFile == commonSettingsFile {
		// Farm already existed, restore CommonSettings.php from backup
		err, _ = execute.Run(installPath, "mv", filepath.Join(installPath, "config", commonSettingsBackup), filepath.Join(installPath, "config", commonSettingsFile))
		if err != nil {
			return err
		}
	} else if originalSettingsFile == localSettingsFile {
		// Converting single wiki to farm: rename backup to CommonSettings.php
		err, _ = execute.Run(installPath, "mv", filepath.Join(installPath, "config", localSettingsBackup), filepath.Join(installPath, "config", commonSettingsFile))
		if err != nil {
			return err
		}
	}

	// Always remove the newly generated LocalSettings.php (not needed in farm)
	err, _ = execute.Run(installPath, "rm", filepath.Join(installPath, "config", localSettingsFile))
	if err != nil {
		return err
	}

	return err
}

func RemoveDatabase(installPath, id string, orch orchestrators.Orchestrator) error {
	envVariables := canasta.GetEnvVariable(installPath + "/.env")
	command := fmt.Sprintf("echo 'DROP DATABASE IF EXISTS %s;' | mysql -h db -u root -p'%s'", id, envVariables["MYSQL_PASSWORD"])
	output, err := orch.ExecWithError(installPath, "db", command)
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
