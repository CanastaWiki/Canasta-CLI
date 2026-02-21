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
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/imagebuild"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/system"
)

// CreateOptions holds parameters shared between all orchestrator subcommands.
type CreateOptions struct {
	CanastaInfo        canasta.CanastaVariables
	WorkingDir         string
	Path               string
	WikiID             string
	SiteName           string
	Domain             string
	YamlPath           string
	EnvFile            string
	ComposerFile       string
	DevTag             string
	BuildFromPath      string
	DatabasePath       string
	WikiSettingsPath   string
	GlobalSettingsPath string
	KeepConfig         bool
}

func NewCmdCreate() *cobra.Command {
	var opts CreateOptions
	var err error

	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta installation",
		Long: `Create a new Canasta MediaWiki installation. Use a subcommand to select
the orchestrator:

  canasta create compose  — Docker Compose (recommended)
  canasta create k8s      — Kubernetes`,
	}

	if opts.WorkingDir, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}

	// Shared flags (persistent so they apply to subcommands)
	pf := createCmd.PersistentFlags()
	pf.StringVarP(&opts.Path, "path", "p", opts.WorkingDir, "Canasta directory")
	pf.StringVarP(&opts.CanastaInfo.Id, "id", "i", "", "Canasta instance ID")
	pf.StringVarP(&opts.WikiID, "wiki", "w", "", "ID of the wiki")
	pf.StringVarP(&opts.SiteName, "site-name", "t", "", "Display name of the wiki (optional, defaults to wiki ID)")
	pf.StringVarP(&opts.Domain, "domain-name", "n", "localhost", "Domain name")
	pf.StringVarP(&opts.CanastaInfo.AdminName, "admin", "a", "", "Initial wiki admin username")
	pf.StringVarP(&opts.CanastaInfo.AdminPassword, "password", "s", "", "Initial wiki admin password (if not provided, auto-generates and saves to config/admin-password_{wikiid})")
	pf.StringVarP(&opts.YamlPath, "yamlfile", "f", "", "Initial wiki yaml file")
	pf.BoolVarP(&opts.KeepConfig, "keep-config", "k", false, "Keep the config files on installation failure")
	pf.StringVar(&opts.CanastaInfo.RootDBPassword, "rootdbpass", "", "Root database password (if not provided, auto-generates and saves to .env). Tip: Use --rootdbpass \"$ROOT_DB_PASS\" to avoid exposing password in shell history")
	pf.StringVar(&opts.CanastaInfo.WikiDBUsername, "wikidbuser", "root", "The username of the wiki database user (default: \"root\")")
	pf.StringVar(&opts.CanastaInfo.WikiDBPassword, "wikidbpass", "", "Wiki database password (if not provided, auto-generates and saves to .env). Tip: Use --wikidbpass \"$WIKI_DB_PASS\" to avoid exposing password in shell history")
	pf.StringVarP(&opts.EnvFile, "envfile", "e", "", "Path to .env file with password overrides (merged with default .env)")
	pf.StringVar(&opts.DevTag, "dev-tag", "latest", "Canasta image tag to use (e.g., latest, dev-branch)")
	pf.StringVar(&opts.BuildFromPath, "build-from", "", "Build Canasta image from local source directory (expects Canasta/, optionally CanastaBase/)")
	pf.StringVarP(&opts.DatabasePath, "database", "d", "", "Path to existing database dump (.sql or .sql.gz) to import instead of running install.php")
	pf.StringVarP(&opts.WikiSettingsPath, "wiki-settings", "l", "", "Path to per-wiki settings file to copy to config/settings/wikis/<wiki_id>/ (filename preserved)")
	pf.StringVarP(&opts.GlobalSettingsPath, "global-settings", "g", "", "Path to global settings file to copy to config/settings/global/ (filename preserved)")
	pf.StringVar(&opts.ComposerFile, "composer", "", "Path to custom composer.local.json to copy to config/")

	// Mark required flags
	_ = createCmd.MarkPersistentFlagRequired("id")

	// Add subcommands
	createCmd.AddCommand(newComposeCmd(&opts))
	createCmd.AddCommand(newK8sCmd(&opts))

	return createCmd
}

// validateOpts performs shared validation that applies to all orchestrators.
// The cmd parameter is used to check whether flags were explicitly set.
func validateOpts(cmd *cobra.Command, opts *CreateOptions) error {
	// Check if the system has at least 2GB of memory
	if err := system.CheckMemoryInGB(2); err != nil {
		return err
	}

	// Validate wiki ID if yamlPath not provided
	if opts.YamlPath == "" {
		if opts.WikiID == "" {
			return fmt.Errorf("--wiki flag is required when --yamlfile is not provided")
		}
		if err := farmsettings.ValidateWikiID(opts.WikiID); err != nil {
			return err
		}
	}

	// Validate Canasta instance ID format
	validString := regexp.MustCompile(`^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$`)
	if !validString.MatchString(opts.CanastaInfo.Id) {
		return fmt.Errorf("Canasta instance ID should not contain spaces or non-ASCII characters, only alphanumeric characters are allowed")
	}

	// Check for duplicate ID before doing any work
	if _, err := config.GetDetails(opts.CanastaInfo.Id); err == nil {
		return fmt.Errorf("Canasta installation with ID '%s' already exists", opts.CanastaInfo.Id)
	}

	// Validate --dev-tag and --build-from are mutually exclusive
	if opts.DevTag != "latest" && opts.BuildFromPath != "" {
		return fmt.Errorf("--dev-tag and --build-from are mutually exclusive")
	}

	// Validate database path if provided
	if opts.DatabasePath != "" {
		if err := canasta.ValidateDatabasePath(opts.DatabasePath); err != nil {
			return err
		}
	}

	// Resolve relative database path to absolute (relative to working directory)
	if opts.DatabasePath != "" && !filepath.IsAbs(opts.DatabasePath) {
		opts.DatabasePath = filepath.Join(opts.WorkingDir, opts.DatabasePath)
	}

	// Validate --admin is required when --database is not provided
	if opts.DatabasePath == "" && opts.CanastaInfo.AdminName == "" {
		return fmt.Errorf("--admin flag is required when --database is not provided")
	}

	// Always generate database passwords
	var err error
	if opts.CanastaInfo, err = canasta.GenerateDBPasswords(opts.CanastaInfo); err != nil {
		return err
	}

	// Generate admin password only if not importing (when importing, we skip install.php)
	if opts.DatabasePath == "" {
		if opts.CanastaInfo, err = canasta.GenerateAdminPassword(opts.CanastaInfo); err != nil {
			return err
		}
	}

	// If no domain was explicitly provided and an env file specifies a
	// non-standard HTTPS port, append the port to the default domain
	// so that wikis.yaml is generated with the correct URL.
	if !cmd.Flags().Changed("domain-name") && opts.EnvFile != "" {
		envFilePath := opts.EnvFile
		if !filepath.IsAbs(envFilePath) {
			envFilePath = filepath.Join(opts.WorkingDir, envFilePath)
		}
		envVars, envErr := canasta.GetEnvVariable(envFilePath)
		if envErr != nil {
			return envErr
		}
		if port, ok := envVars["HTTPS_PORT"]; ok && port != "443" && port != "" {
			opts.Domain = opts.Domain + ":" + port
		}
	}

	return nil
}

// determineBaseImage resolves the Docker image to use.
// Returns the image tag and whether it was locally built.
func determineBaseImage(buildFromPath, devTag string) (string, bool, error) {
	if buildFromPath != "" {
		canastaPath := filepath.Join(buildFromPath, "Canasta")
		if _, err := os.Stat(canastaPath); err == nil {
			logging.Print("Building Canasta from local source...\n")
			builtImage, err := imagebuild.BuildFromSource(buildFromPath)
			if err != nil {
				return "", false, fmt.Errorf("failed to build from source: %w", err)
			}
			return builtImage, true, nil
		}
		logging.Print("No local Canasta repo found, using registry image\n")
	}
	return canasta.GetImageWithTag(devTag), false, nil
}

// setupInstallation creates the directory, writes stack files,
// copies templates, configures .env and wikis.yaml.
// Returns the resolved installation path (opts.Path + "/" + opts.CanastaInfo.Id).
func setupInstallation(opts *CreateOptions, orch orchestrators.Orchestrator, baseImage string) (string, error) {
	path := filepath.Join(opts.Path, opts.CanastaInfo.Id)
	if err := os.MkdirAll(path, 0755); err != nil {
		return "", fmt.Errorf("failed to create installation directory: %w", err)
	}
	if err := orch.WriteStackFiles(path); err != nil {
		return "", fmt.Errorf("failed to write stack files: %w", err)
	}
	if err := canasta.CopyInstallationTemplate(path); err != nil {
		return "", err
	}

	// If user provided a custom yaml file, copy it; otherwise generate it
	if opts.YamlPath != "" {
		if err := canasta.CopyYaml(opts.YamlPath, path); err != nil {
			return "", err
		}
	} else {
		opts.YamlPath = filepath.Join(path, "config", "wikis.yaml")
		if _, err := farmsettings.GenerateWikisYaml(opts.YamlPath, opts.WikiID, opts.Domain, opts.SiteName); err != nil {
			return "", err
		}
	}
	if err := canasta.UpdateEnvFile(opts.EnvFile, path, opts.WorkingDir, opts.CanastaInfo.RootDBPassword, opts.CanastaInfo.WikiDBPassword); err != nil {
		return "", err
	}
	// Set CANASTA_IMAGE in .env for local builds
	if opts.BuildFromPath != "" {
		if err := canasta.SaveEnvVariable(path+"/.env", "CANASTA_IMAGE", baseImage); err != nil {
			return "", err
		}
	}
	if err := canasta.CopySettings(path); err != nil {
		return "", err
	}
	if opts.WikiSettingsPath != "" && opts.WikiID != "" {
		if err := canasta.CopyWikiSettingFile(path, opts.WikiID, opts.WikiSettingsPath, opts.WorkingDir); err != nil {
			return "", err
		}
	}
	if opts.GlobalSettingsPath != "" {
		if err := canasta.CopyGlobalSettingFile(path, opts.GlobalSettingsPath, opts.WorkingDir); err != nil {
			return "", err
		}
	}
	if opts.ComposerFile != "" {
		if err := canasta.CopyComposerFile(path, opts.ComposerFile, opts.WorkingDir); err != nil {
			return "", err
		}
	}

	return path, nil
}

// installAndRegister starts containers, runs install.php or imports DB,
// registers the installation in config, and restarts.
func installAndRegister(opts *CreateOptions, path string, orch orchestrators.Orchestrator, instance config.Installation) error {
	// Always start without dev mode for installation to avoid xdebug interference
	tempInstance := instance
	tempInstance.DevMode = false
	if err := orch.Start(tempInstance); err != nil {
		return err
	}

	// If database path is provided, import the database instead of running install.php
	if opts.DatabasePath != "" {
		logging.Print("Importing database instead of running install.php\n")

		// Wait for database to be ready
		if err := mediawiki.WaitForDB(path, orch); err != nil {
			return err
		}

		envVariables, envErr := canasta.GetEnvVariable(path + "/.env")
		if envErr != nil {
			return envErr
		}
		dbPassword := envVariables["MYSQL_PASSWORD"]
		if err := orchestrators.ImportDatabase(orch, opts.WikiID, opts.DatabasePath, dbPassword, tempInstance); err != nil {
			return err
		}
		if err := canasta.GenerateAndSaveSecretKey(path); err != nil {
			return err
		}
	} else {
		if _, err := mediawiki.Install(path, opts.YamlPath, orch, opts.CanastaInfo); err != nil {
			return err
		}
	}

	if err := config.Add(instance); err != nil {
		return err
	}

	// Restart to apply all settings
	if err := orch.Stop(tempInstance); err != nil {
		return err
	}
	return orch.Start(instance)
}

// deleteConfigAndContainers removes containers, an optional kind cluster, and config files.
func deleteConfigAndContainers(installDir string, orch orchestrators.Orchestrator, kindClusterName string) {
	fmt.Println("Removing containers")
	_, _ = orch.Destroy(installDir)
	if kindClusterName != "" {
		_ = orchestrators.DeleteKindCluster(kindClusterName)
	}
	fmt.Println("Deleting config files")
	_, _ = orchestrators.DeleteConfig(installDir)
	fmt.Println("Deleted all containers and config files")
}
