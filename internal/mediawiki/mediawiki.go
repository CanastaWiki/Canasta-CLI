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
	confPath             = "/tmp/canasta-install/"
	scriptPath           = "/w"
	localSettingsFile    = "LocalSettings.php"
	commonSettingsFile   = "CommonSettings.php"
	localSettingsBackup  = "LocalSettingsBackup.php"
	commonSettingsBackup = "CommonSettingsBackup.php"
)

// WaitForDB waits for the database to be reachable inside the web container.
// The orchestrator's health checks handle the real wait; this is a short
// safety-net check that runs after the container is already started.
func WaitForDB(path string, orch orchestrators.Orchestrator) error {
	output, err := orch.ExecWithError(path, orchestrators.ServiceWeb, "/wait-for-it.sh -t 10 db:3306")
	if err != nil {
		return fmt.Errorf("database not ready: %s", output)
	}
	return nil
}

func Install(path, yamlPath string, orch orchestrators.Orchestrator, canastaInfo canasta.CanastaVariables) (canasta.CanastaVariables, error) {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")

	if err := WaitForDB(path, orch); err != nil {
		return canastaInfo, err
	}

	WikiIDs, domainNames, _, err := farmsettings.ReadWikisYaml(yamlPath)
	if err != nil {
		return canastaInfo, err
	}

	// Create writable temp directory for install.php output (needed because
	// /mediawiki/config/ may be a read-only ConfigMap mount in Kubernetes)
	if _, err := orch.ExecWithError(path, orchestrators.ServiceWeb, "mkdir -p "+confPath); err != nil {
		return canastaInfo, fmt.Errorf("failed to create install temp directory: %w", err)
	}

	for i := 0; i < len(WikiIDs); i++ {
		wikiID := WikiIDs[i]
		domainName := domainNames[i]

		// Unset MW_SECRET_KEY so CanastaDefaultSettings.php doesn't think wiki is already configured
		installCmd := fmt.Sprintf("env -u MW_SECRET_KEY php maintenance/install.php --skins='Vector' --dbserver=%s --dbname=%s --confpath=%s --scriptpath=%s --server=%s --installdbuser='%s' --installdbpass=%s --dbuser='%s' --dbpass=%s --pass=%s %s %s",
			orchestrators.ServiceDB, orchestrators.ShellQuote(wikiID), confPath, scriptPath, orchestrators.ShellQuote("https://"+domainName), "root", orchestrators.ShellQuote(canastaInfo.RootDBPassword), canastaInfo.WikiDBUsername, orchestrators.ShellQuote(canastaInfo.WikiDBPassword), orchestrators.ShellQuote(canastaInfo.AdminPassword), orchestrators.ShellQuote(wikiID), orchestrators.ShellQuote(canastaInfo.AdminName))

		output, err := orch.ExecWithError(path, orchestrators.ServiceWeb, installCmd)
		if err != nil {
			return canastaInfo, fmt.Errorf("failed to run install.php: %s", output)
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
			envVars, envErr := canasta.GetEnvVariable(envPath)
			if envErr != nil {
				return canastaInfo, envErr
			}
			if val, ok := envVars["MW_SECRET_KEY"]; ok && val != "" {
				logging.Print("MW_SECRET_KEY already set in .env, skipping extraction\n")
			} else {
				// Read LocalSettings.php from inside the container (works for
				// both bind-mount and ConfigMap-based orchestrators)
				catCmd := fmt.Sprintf("cat %s%s", confPath, localSettingsFile)
				content, catErr := orch.ExecWithError(path, orchestrators.ServiceWeb, catCmd)
				if catErr != nil {
					return canastaInfo, fmt.Errorf("failed to read LocalSettings.php: %w", catErr)
				}

				secretKeyRegex := regexp.MustCompile(`\$wgSecretKey\s*=\s*["']([0-9a-fA-F]+)["']`)
				matches := secretKeyRegex.FindStringSubmatch(content)
				if matches == nil {
					return canastaInfo, fmt.Errorf("could not find $wgSecretKey in LocalSettings.php")
				}
				secretKey := matches[1]

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
		rmCmd := fmt.Sprintf("rm -f %s%s", confPath, localSettingsFile)
		if _, rmErr := orch.ExecWithError(path, orchestrators.ServiceWeb, rmCmd); rmErr != nil {
			return canastaInfo, fmt.Errorf("failed to remove LocalSettings.php: %w", rmErr)
		}

		time.Sleep(time.Second)
	}

	// Clean up the temporary install directory
	rmCmd := fmt.Sprintf("rm -rf %s", confPath)
	if _, err := orch.ExecWithError(path, orchestrators.ServiceWeb, rmCmd); err != nil {
		return canastaInfo, fmt.Errorf("failed to remove install temp directory: %w", err)
	}

	return canastaInfo, nil
}

func InstallOne(installPath, id, domain, admin, adminPassword, dbuser, workingDir string, orch orchestrators.Orchestrator) error {
	var err error
	logging.Print("Configuring MediaWiki Installation\n")
	logging.Print("Running install.php\n")
	envVariables, err := canasta.GetEnvVariable(filepath.Join(installPath, ".env"))
	if err != nil {
		return err
	}

	if err := WaitForDB(installPath, orch); err != nil {
		return err
	}

	// Create writable temp directory for install.php output (needed because
	// /mediawiki/config/ may be a read-only ConfigMap mount in Kubernetes)
	if _, err := orch.ExecWithError(installPath, orchestrators.ServiceWeb, "mkdir -p "+confPath); err != nil {
		return fmt.Errorf("failed to create install temp directory: %w", err)
	}

	useNewArchitecture, originalSettingsFile, err := backupLegacySettings(installPath)
	if err != nil {
		return err
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
	command := fmt.Sprintf("%s --skins='Vector' --dbserver=%s --dbname=%s --confpath=%s --scriptpath=%s --server=%s --installdbuser='%s' --installdbpass=%s --dbuser='%s' --dbpass=%s --pass=%s %s %s",
		installCmd, orchestrators.ServiceDB, orchestrators.ShellQuote(id), confPath, scriptPath, orchestrators.ShellQuote("https://"+domain), installdbuser, orchestrators.ShellQuote(installdbpass), dbuser, orchestrators.ShellQuote(dbpass), orchestrators.ShellQuote(adminPassword), orchestrators.ShellQuote(id), orchestrators.ShellQuote(admin))
	output, err := orch.ExecWithError(installPath, orchestrators.ServiceWeb, command)
	if err != nil {
		return fmt.Errorf("failed to run install.php for wiki %q: %s", id, output)
	}

	// Save admin password to config/admin-password_{wikiid}
	configDir := filepath.Join(installPath, "config")
	passwordFile := fmt.Sprintf("admin-password_%s", id)
	if err := canasta.SavePasswordToFile(configDir, passwordFile, adminPassword); err != nil {
		return err
	}

	if err := restoreLegacySettings(installPath, useNewArchitecture, originalSettingsFile); err != nil {
		return err
	}

	// Clean up the temporary install directory
	rmCmd := fmt.Sprintf("rm -rf %s", confPath)
	if _, rmErr := orch.ExecWithError(installPath, orchestrators.ServiceWeb, rmCmd); rmErr != nil {
		return fmt.Errorf("failed to remove install temp directory: %w", rmErr)
	}

	return nil
}

// backupLegacySettings detects the configuration architecture and, for legacy
// setups (LocalSettings.php or CommonSettings.php), backs up the settings file
// and removes the original so the installer doesn't see it.
// Returns whether the new architecture is in use and which original file was backed up.
func backupLegacySettings(installPath string) (useNewArchitecture bool, originalSettingsFile string, err error) {
	localExists, _ := fileExists(filepath.Join(installPath, "config", localSettingsFile))
	commonExists, _ := fileExists(filepath.Join(installPath, "config", commonSettingsFile))
	wikisYamlExists, _ := fileExists(filepath.Join(installPath, "config", "wikis.yaml"))

	if !localExists && !commonExists && !wikisYamlExists {
		return false, "", fmt.Errorf("No valid configuration found (wikis.yaml, LocalSettings.php, or CommonSettings.php)")
	}

	useNewArchitecture = wikisYamlExists && !localExists && !commonExists
	if useNewArchitecture {
		return true, "", nil
	}

	if commonExists {
		originalSettingsFile = commonSettingsFile
	} else if localExists {
		originalSettingsFile = localSettingsFile
	}

	configDir := filepath.Join(installPath, "config")
	var backupFile string
	if originalSettingsFile == commonSettingsFile {
		backupFile = commonSettingsBackup
	} else {
		backupFile = localSettingsBackup
	}

	err, _ = execute.Run(installPath, "cp", filepath.Join(configDir, originalSettingsFile), filepath.Join(configDir, backupFile))
	if err != nil {
		return false, "", err
	}
	err, _ = execute.Run(installPath, "rm", filepath.Join(configDir, originalSettingsFile))
	if err != nil {
		return false, "", err
	}

	return false, originalSettingsFile, nil
}

// restoreLegacySettings restores the backed-up settings file as
// CommonSettings.php after install.php has run.
func restoreLegacySettings(installPath string, useNewArchitecture bool, originalSettingsFile string) error {
	if useNewArchitecture || originalSettingsFile == "" {
		return nil
	}

	configDir := filepath.Join(installPath, "config")
	var backupFile string
	if originalSettingsFile == commonSettingsFile {
		backupFile = commonSettingsBackup
	} else {
		backupFile = localSettingsBackup
	}

	// Restore as CommonSettings.php (both cases: existing farm or single→farm conversion)
	err, _ := execute.Run(installPath, "mv", filepath.Join(configDir, backupFile), filepath.Join(configDir, commonSettingsFile))
	return err
}

func RemoveDatabase(installPath, id string, orch orchestrators.Orchestrator) error {
	envVariables, err := canasta.GetEnvVariable(filepath.Join(installPath, ".env"))
	if err != nil {
		return err
	}
	command := fmt.Sprintf("echo 'DROP DATABASE IF EXISTS %s;' | mysql -h db -u root -p%s", orchestrators.ShellQuote(id), orchestrators.ShellQuote(envVariables["MYSQL_PASSWORD"]))
	output, err := orch.ExecWithError(installPath, orchestrators.ServiceDB, command)
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
