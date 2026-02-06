package create

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/imagebuild"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
)

func NewCmdCreate() *cobra.Command {
	var (
		path          string
		orchestrator  string
		workingDir    string
		wikiID        string
		siteName      string
		domain        string
		yamlPath      string
		err           error
		keepConfig    bool
		canastaInfo   canasta.CanastaVariables
		override      string
		envFile       string
		devModeFlag   bool   // enable dev mode
		devTag        string // registry image tag for dev mode
		buildFromPath string // path to build Canasta from source
		databasePath       string // path to existing database dump
		wikiSettingsPath   string // path to existing per-wiki Settings.php
		globalSettingsPath string // path to existing global settings file
	)
	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta installation",
		Long: `Create a new Canasta MediaWiki installation. This clones the Docker Compose
stack, generates configuration files, starts the containers, and runs the
MediaWiki installer. You can optionally import an existing database dump
instead of running the installer, or enable development mode with Xdebug.`,
		Example: `  # Create a basic single-wiki installation
  canasta create -i myinstance -w main -a admin -n example.com

  # Create with an existing database dump
  canasta create -i myinstance -w main -d /path/to/dump.sql -n example.com

  # Create with development mode enabled
  canasta create -i myinstance -w main -a admin -n localhost -D`,
		RunE: func(cmd *cobra.Command, args []string) error {
			// Validate wiki ID if yamlPath not provided
			if yamlPath == "" {
				if wikiID == "" {
					log.Fatal(fmt.Errorf("Error: --wiki flag is required when --yamlfile is not provided"))
				}
				if err := farmsettings.ValidateWikiID(wikiID); err != nil {
					log.Fatal(err)
				}
			}

			// Validate Canasta instance ID format
			validString := regexp.MustCompile(`^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$`)
			if !validString.MatchString(canastaInfo.Id) {
				log.Fatal(fmt.Errorf("Error: Canasta instance ID should not contain spaces or non-ASCII characters, only alphanumeric characters are allowed"))
			}

			// Validate --dev-tag and --build-from are mutually exclusive
			if devTag != "latest" && buildFromPath != "" {
				log.Fatal(fmt.Errorf("Error: --dev-tag and --build-from are mutually exclusive"))
			}

			// Validate database path if provided
			if databasePath != "" {
				if err := canasta.ValidateDatabasePath(databasePath); err != nil {
					log.Fatal(err)
				}
			}

			// Resolve relative database path to absolute (relative to working directory)
			if databasePath != "" && !filepath.IsAbs(databasePath) {
				databasePath = filepath.Join(workingDir, databasePath)
			}

			// Validate --admin is required when --database is not provided
			if databasePath == "" && canastaInfo.AdminName == "" {
				log.Fatal(fmt.Errorf("Error: --admin flag is required when --database is not provided"))
			}

			// Always generate database passwords
			if canastaInfo, err = canasta.GenerateDBPasswords(canastaInfo); err != nil {
				log.Fatal(err)
			}

			// Generate admin password only if not importing (when importing, we skip install.php)
			if databasePath == "" {
				if canastaInfo, err = canasta.GenerateAdminPassword(canastaInfo); err != nil {
					log.Fatal(err)
				}
			}

			// If no domain was explicitly provided and an env file specifies a
			// non-standard HTTPS port, append the port to the default domain
			// so that wikis.yaml is generated with the correct URL.
			if !cmd.Flags().Changed("domain-name") && envFile != "" {
				envFilePath := envFile
				if !filepath.IsAbs(envFilePath) {
					envFilePath = filepath.Join(workingDir, envFilePath)
				}
				envVars := canasta.GetEnvVariable(envFilePath)
				if port, ok := envVars["HTTPS_PORT"]; ok && port != "443" && port != "" {
					domain = domain + ":" + port
				}
			}

			description := "Creating Canasta installation '" + canastaInfo.Id + "'..."
			if devModeFlag {
				description = "Creating Canasta installation '" + canastaInfo.Id + "' with dev mode..."
			}
			_, done := spinner.New(description)

			if err = createCanasta(canastaInfo, workingDir, path, wikiID, siteName, domain, yamlPath, orchestrator, override, envFile, devModeFlag, devTag, buildFromPath, databasePath, wikiSettingsPath, globalSettingsPath, done); err != nil {
				fmt.Print(err.Error(), "\n")
				if !keepConfig {
					canasta.DeleteConfigAndContainers(keepConfig, path+"/"+canastaInfo.Id, orchestrator)
					log.Fatal(fmt.Errorf("Installation failed and files were cleaned up"))
				}
				log.Fatal(fmt.Errorf("Installation failed. Keeping all the containers and config files\nExiting"))
			}
			fmt.Println("Done.")
			return nil
		},
	}

	if workingDir, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}

	createCmd.Flags().StringVarP(&path, "path", "p", workingDir, "Canasta directory")
	createCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "compose", "Orchestrator to use for installation")
	createCmd.Flags().StringVarP(&canastaInfo.Id, "id", "i", "", "Canasta instance ID")
	createCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the wiki")
	createCmd.Flags().StringVarP(&siteName, "site-name", "t", "", "Display name of the wiki (optional, defaults to wiki ID)")
	createCmd.Flags().StringVarP(&domain, "domain-name", "n", "localhost", "Domain name")
	createCmd.Flags().StringVarP(&canastaInfo.AdminName, "admin", "a", "", "Initial wiki admin username")
	createCmd.Flags().StringVarP(&canastaInfo.AdminPassword, "password", "s", "", "Initial wiki admin password (if not provided, auto-generates and saves to config/admin-password_{wikiid})")
	createCmd.Flags().StringVarP(&yamlPath, "yamlfile", "f", "", "Initial wiki yaml file")
	createCmd.Flags().BoolVarP(&keepConfig, "keep-config", "k", false, "Keep the config files on installation failure")
	createCmd.Flags().StringVarP(&override, "override", "r", "", "Name of a file to copy to docker-compose.override.yml")
	createCmd.Flags().StringVar(&canastaInfo.RootDBPassword, "rootdbpass", "", "Root database password (if not provided, auto-generates and saves to .env). Tip: Use --rootdbpass \"$ROOT_DB_PASS\" to avoid exposing password in shell history")
	createCmd.Flags().StringVar(&canastaInfo.WikiDBUsername, "wikidbuser", "root", "The username of the wiki database user (default: \"root\")")
	createCmd.Flags().StringVar(&canastaInfo.WikiDBPassword, "wikidbpass", "", "Wiki database password (if not provided, auto-generates and saves to .env). Tip: Use --wikidbpass \"$WIKI_DB_PASS\" to avoid exposing password in shell history")
	createCmd.Flags().StringVarP(&envFile, "envfile", "e", "", "Path to .env file with password overrides (merged with .env.example)")
	createCmd.Flags().BoolVarP(&devModeFlag, "dev", "D", false, "Enable development mode with Xdebug and code extraction")
	createCmd.Flags().StringVar(&devTag, "dev-tag", "latest", "Canasta image tag to use (e.g., latest, dev-branch)")
	createCmd.Flags().StringVar(&buildFromPath, "build-from", "", "Build Canasta image from local source directory (expects Canasta/, optionally CanastaBase/)")
	createCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to existing database dump (.sql or .sql.gz) to import instead of running install.php")
	createCmd.Flags().StringVarP(&wikiSettingsPath, "wiki-settings", "l", "", "Path to per-wiki settings file to copy to config/settings/wikis/<wiki_id>/ (filename preserved)")
	createCmd.Flags().StringVarP(&globalSettingsPath, "global-settings", "g", "", "Path to global settings file to copy to config/settings/global/ (filename preserved)")

	// Mark required flags
	createCmd.MarkFlagRequired("id")

	return createCmd
}

// createCanasta accepts all the keyword arguments and creates an installation of the latest Canasta.
func createCanasta(canastaInfo canasta.CanastaVariables, workingDir, path, wikiID, siteName, domain, yamlPath, orchestrator, override, envFile string, devModeEnabled bool, devTag, buildFromPath, databasePath, wikiSettingsPath, globalSettingsPath string, done chan struct{}) error {
	// Pass a message to the "done" channel indicating the completion of createCanasta function.
	// This signals the spinner to stop printing progress, regardless of success or failure.
	defer func() {
		done <- struct{}{}
	}()
	if _, err := config.GetDetails(canastaInfo.Id); err == nil {
		log.Fatal(fmt.Errorf("Canasta installation with the ID already exist!"))
	}

	// Determine the base image to use
	var baseImage string
	if buildFromPath != "" {
		// Build Canasta (and optionally CanastaBase) from source
		logging.Print("Building Canasta from local source...\n")
		builtImage, err := imagebuild.BuildFromSource(buildFromPath)
		if err != nil {
			return fmt.Errorf("failed to build from source: %w", err)
		}
		baseImage = builtImage
	} else {
		// Use registry image with specified tag
		baseImage = canasta.GetImageWithTag(devTag)
	}

	// Clone the stack repository first to create the installation directory
	if err := canasta.CloneStackRepo(orchestrator, canastaInfo.Id, &path, buildFromPath); err != nil {
		return err
	}

	// If user provided a custom yaml file, copy it; otherwise generate it directly in the installation
	if yamlPath != "" {
		// User provided custom yaml file via --yamlfile flag
		if err := canasta.CopyYaml(yamlPath, path); err != nil {
			return err
		}
	} else {
		// Generate wikis.yaml directly in the installation directory
		yamlPath = filepath.Join(path, "config", "wikis.yaml")
		if _, err := farmsettings.GenerateWikisYaml(yamlPath, wikiID, domain, siteName); err != nil {
			return err
		}
	}
	if err := canasta.CreateEnvFile(envFile, path, workingDir, canastaInfo.RootDBPassword, canastaInfo.WikiDBPassword); err != nil {
		return err
	}
	// Set CANASTA_IMAGE in .env for local builds so docker-compose uses the locally built image
	if buildFromPath != "" {
		if err := canasta.SaveEnvVariable(path+"/.env", "CANASTA_IMAGE", baseImage); err != nil {
			return err
		}
	}
	if err := canasta.CopySettings(path); err != nil {
		return err
	}
	// If custom per-wiki settings file provided, overwrite the Settings.php for this wiki
	if wikiSettingsPath != "" && wikiID != "" {
		if err := canasta.CopyWikiSettingFile(path, wikiID, wikiSettingsPath, workingDir); err != nil {
			return err
		}
	}
	// If custom global settings file provided, copy to config/settings/
	if globalSettingsPath != "" {
		if err := canasta.CopyGlobalSettingFile(path, globalSettingsPath, workingDir); err != nil {
			return err
		}
	}
	if err := canasta.RewriteCaddy(path); err != nil {
		return err
	}
	if err := canasta.CreateCaddyfileCustom(path); err != nil {
		return err
	}
	if err := orchestrators.CopyOverrideFile(path, orchestrator, override, workingDir); err != nil {
		return err
	}

	// Dev mode: extract code and build xdebug image before starting
	if devModeEnabled {
		if err := devmode.SetupFullDevMode(path, orchestrator, baseImage); err != nil {
			return err
		}
	}

	// Always start without dev mode for installation to avoid xdebug interference
	// (xdebug can cause hangs if a debugger is listening during install.php)
	tempInstance := config.Installation{Path: path, Orchestrator: orchestrator, DevMode: false}
	if err := orchestrators.Start(tempInstance); err != nil {
		return err
	}

	// If database path is provided, import the database instead of running install.php
	if databasePath != "" {
		logging.Print("Importing database instead of running install.php\n")

		// Wait for database to be ready
		command := "/wait-for-it.sh -t 60 db:3306"
		if _, err := orchestrators.ExecWithError(path, orchestrator, "web", command); err != nil {
			return fmt.Errorf("database not ready: %w", err)
		}

		envVariables := canasta.GetEnvVariable(path + "/.env")
		dbPassword := envVariables["MYSQL_PASSWORD"]
		if err := orchestrators.ImportDatabase(wikiID, databasePath, dbPassword, tempInstance); err != nil {
			return err
		}
		// Generate secret key and save to .env (DB password already in .env)
		if err := canasta.GenerateAndSaveSecretKey(path); err != nil {
			return err
		}
	} else {
		// Run MediaWiki installer
		if _, err := mediawiki.Install(path, yamlPath, orchestrator, canastaInfo); err != nil {
			return err
		}
	}

	instance := config.Installation{Id: canastaInfo.Id, Path: path, Orchestrator: orchestrator, DevMode: devModeEnabled}
	if err := config.Add(instance); err != nil {
		return err
	}

	// Restart to apply all settings
	// Stop containers (started without dev mode)
	if err := orchestrators.Stop(tempInstance); err != nil {
		log.Fatal(err)
	}

	// Start with appropriate mode (orchestrators.Start handles dev mode automatically)
	if err := orchestrators.Start(instance); err != nil {
		log.Fatal(err)
	}

	if devModeEnabled {
		fmt.Println("\033[32mDevelopment mode enabled. Edit files in mediawiki-code/ - changes appear immediately.\033[0m")
		fmt.Println("\033[32mVSCode: Open the installation directory, install PHP Debug extension, and start 'Listen for Xdebug'.\033[0m")
	}

	fmt.Println("\033[32mIf you need email enabled for this wiki, please set $wgSMTP; email will not work otherwise. See https://mediawiki.org/wiki/Manual:$wgSMTP for options.\033[0m")
	return nil
}
