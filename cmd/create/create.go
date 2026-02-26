package create

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/permissions"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/farmsettings"
	"github.com/CanastaWiki/Canasta-CLI/internal/imagebuild"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
	"github.com/CanastaWiki/Canasta-CLI/internal/system"
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
		canastaImage  string // custom Canasta image reference
		buildFromPath string // path to build Canasta from source
		databasePath       string // path to existing database dump
		wikiSettingsPath   string // path to existing per-wiki Settings.php
		globalSettingsPath string // path to existing global settings file
		composerFile       string // path to custom composer.local.json
		registry           string // container registry for K8s image push
		createCluster      bool   // create and manage a K8s cluster
	)
	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta installation",
		Long: `Create a new Canasta MediaWiki installation. This generates configuration
files, starts the containers, and runs the MediaWiki installer. You can
optionally import an existing database dump instead of running the installer.
After creating, use 'canasta devmode enable' to enable development mode.`,
		Example: `  # Create a basic single-wiki installation
  canasta create -i myinstance -w main -n example.com

  # Create with an existing database dump
  canasta create -i myinstance -w main -d /path/to/dump.sql -n example.com`,
		RunE: func(cmd *cobra.Command, args []string) error {
			// Check if the system has at least 2GB of memory
			if err := system.CheckMemoryInGB(2); err != nil {
				return err
			}
			// Validate wiki ID if yamlPath not provided
			if yamlPath == "" {
				if wikiID == "" {
					return fmt.Errorf("--wiki flag is required when --yamlfile is not provided")
				}
				if err := farmsettings.ValidateWikiID(wikiID); err != nil {
					return err
				}
			}

			// Validate Canasta instance ID format
			validString := regexp.MustCompile(`^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$`)
			if !validString.MatchString(canastaInfo.Id) {
				return fmt.Errorf("Canasta instance ID should not contain spaces or non-ASCII characters, only alphanumeric characters are allowed")
			}

			// Check for duplicate ID before doing any work
			if _, err := config.GetDetails(canastaInfo.Id); err == nil {
				return fmt.Errorf("Canasta installation with ID '%s' already exists", canastaInfo.Id)
			}

			// Validate --canasta-image and --build-from are mutually exclusive
			if canastaImage != "" && buildFromPath != "" {
				return fmt.Errorf("--canasta-image and --build-from are mutually exclusive")
			}

			// Validate database path if provided
			if databasePath != "" {
				if err := canasta.ValidateDatabasePath(databasePath); err != nil {
					return err
				}
			}

			// Resolve relative database path to absolute (relative to working directory)
			if databasePath != "" && !filepath.IsAbs(databasePath) {
				databasePath = filepath.Join(workingDir, databasePath)
			}

			// Always generate database passwords
			if canastaInfo, err = canasta.GenerateDBPasswords(canastaInfo); err != nil {
				return err
			}

			// Generate admin password only if not importing (when importing, we skip install.php)
			if databasePath == "" {
				if canastaInfo, err = canasta.GenerateAdminPassword(canastaInfo); err != nil {
					return err
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
				envVars, envErr := canasta.GetEnvVariable(envFilePath)
				if envErr != nil {
					return envErr
				}
				if port, ok := envVars["HTTPS_PORT"]; ok && port != "443" && port != "" {
					domain = domain + ":" + port
				}
			}

			stopSpinner := spinner.New("Creating Canasta installation '" + canastaInfo.Id + "'...")

			orch, err := orchestrators.New(orchestrator)
			if err != nil {
				return err
			}
			if createCluster {
				if k8s, ok := orch.(*orchestrators.KubernetesOrchestrator); ok {
					k8s.ManagedCluster = true
				} else {
					return fmt.Errorf("--create-cluster is only supported with Kubernetes orchestrator")
				}
			}
			if err = orch.CheckDependencies(); err != nil {
				return err
			}
			if err = createCanasta(canastaInfo, workingDir, path, wikiID, siteName, domain, yamlPath, orch, orchestrator, override, envFile, composerFile, canastaImage, buildFromPath, registry, createCluster, databasePath, wikiSettingsPath, globalSettingsPath); err != nil {
				stopSpinner()
				fmt.Print(err.Error(), "\n")
				if !keepConfig {
					deleteConfigAndContainers(path+"/"+canastaInfo.Id, orch)
					return fmt.Errorf("Installation failed and files were cleaned up")
				}
				return fmt.Errorf("Installation failed. Keeping all the containers and config files")
			}
			stopSpinner()
			fmt.Println("\033[32mIf you need email enabled for this wiki, please set $wgSMTP; email will not work otherwise. See https://mediawiki.org/wiki/Manual:$wgSMTP for options.\033[0m")
			fmt.Println("Done.")
			return nil
		},
	}

	if workingDir, err = os.Getwd(); err != nil {
		log.Fatal(err)
	}

	createCmd.Flags().StringVarP(&path, "path", "p", workingDir, "Canasta directory")
	createCmd.Flags().StringVarP(&orchestrator, "orchestrator", "o", "compose", "Orchestrator to use (compose or kubernetes)")
	createCmd.Flags().StringVarP(&canastaInfo.Id, "id", "i", "", "Canasta instance ID")
	createCmd.Flags().StringVarP(&wikiID, "wiki", "w", "", "ID of the wiki")
	createCmd.Flags().StringVarP(&siteName, "site-name", "t", "", "Display name of the wiki (optional, defaults to wiki ID)")
	createCmd.Flags().StringVarP(&domain, "domain-name", "n", "localhost", "Domain name")
	createCmd.Flags().StringVarP(&canastaInfo.AdminName, "admin", "a", "WikiSysop", "Initial wiki admin username (default: \"WikiSysop\")")
	createCmd.Flags().StringVarP(&canastaInfo.AdminPassword, "password", "s", "", "Initial wiki admin password (if not provided, auto-generates and saves to config/admin-password_{wikiid})")
	createCmd.Flags().StringVarP(&yamlPath, "yamlfile", "f", "", "Initial wiki yaml file")
	createCmd.Flags().BoolVarP(&keepConfig, "keep-config", "k", false, "Keep the config files on installation failure")
	createCmd.Flags().StringVarP(&override, "override", "r", "", "Name of a file to copy to docker-compose.override.yml (Compose only)")
	createCmd.Flags().StringVar(&canastaInfo.RootDBPassword, "rootdbpass", "", "Root database password (if not provided, auto-generates and saves to .env). Tip: Use --rootdbpass \"$ROOT_DB_PASS\" to avoid exposing password in shell history")
	createCmd.Flags().StringVar(&canastaInfo.WikiDBUsername, "wikidbuser", "root", "The username of the wiki database user (default: \"root\")")
	createCmd.Flags().StringVar(&canastaInfo.WikiDBPassword, "wikidbpass", "", "Wiki database password (if not provided, auto-generates and saves to .env). Tip: Use --wikidbpass \"$WIKI_DB_PASS\" to avoid exposing password in shell history")
	createCmd.Flags().StringVarP(&envFile, "envfile", "e", "", "Path to .env file with password overrides (merged with default .env)")
	createCmd.Flags().StringVar(&canastaImage, "canasta-image", "", "Canasta image to use (e.g., ghcr.io/canastawiki/canasta:dev-branch)")
	createCmd.Flags().StringVar(&buildFromPath, "build-from", "", "Build from local source (directory must contain Canasta/, and optionally CanastaBase/)")
	createCmd.Flags().StringVarP(&databasePath, "database", "d", "", "Path to existing database dump (.sql or .sql.gz) to import instead of running install.php")
	createCmd.Flags().StringVarP(&wikiSettingsPath, "wiki-settings", "l", "", "Path to per-wiki settings file to copy to config/settings/wikis/<wiki_id>/ (filename preserved)")
	createCmd.Flags().StringVarP(&globalSettingsPath, "global-settings", "g", "", "Path to global settings file to copy to config/settings/global/ (filename preserved)")
	createCmd.Flags().StringVar(&composerFile, "composer", "", "Path to custom composer.local.json to copy to config/")
	createCmd.Flags().StringVar(&registry, "registry", "localhost:5000", "Container registry for pushing locally built images (used with --build-from on Kubernetes)")
	createCmd.Flags().BoolVar(&createCluster, "create-cluster", false, "Create and manage a local Kubernetes cluster for this installation (Kubernetes only)")

	// Mark required flags
	_ = createCmd.MarkFlagRequired("id")

	return createCmd
}

// createCanasta accepts all the keyword arguments and creates an installation of the latest Canasta.
func createCanasta(canastaInfo canasta.CanastaVariables, workingDir, path, wikiID, siteName, domain, yamlPath string, orch orchestrators.Orchestrator, orchestrator, override, envFile, composerFile string, canastaImage, buildFromPath, registry string, createCluster bool, databasePath, wikiSettingsPath, globalSettingsPath string) error {
	// Determine the base image to use
	var baseImage string
	var localImageBuilt bool
	if buildFromPath != "" {
		// Build Canasta (and optionally CanastaBase) from source
		logging.Print("Building from local source...\n")
		builtImage, err := imagebuild.BuildFromSource(buildFromPath)
		if err != nil {
			return fmt.Errorf("failed to build from source: %w", err)
		}
		baseImage = builtImage
		localImageBuilt = true
	} else if canastaImage != "" {
		// Use the user-specified image
		baseImage = canastaImage
	} else {
		// Use the default Canasta image
		baseImage = canasta.GetDefaultImage()
	}

	// Create the installation directory and write orchestrator stack files
	path = filepath.Join(path, canastaInfo.Id)
	if err := os.MkdirAll(path, permissions.DirectoryPermission); err != nil {
		return fmt.Errorf("failed to create installation directory: %w", err)
	}
	if err := orch.WriteStackFiles(path); err != nil {
		return fmt.Errorf("failed to write stack files: %w", err)
	}

	// Copy shared installation template files (config, settings, etc.)
	if err := canasta.CopyInstallationTemplate(path); err != nil {
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
	if err := canasta.UpdateEnvFile(envFile, path, workingDir, canastaInfo.RootDBPassword, canastaInfo.WikiDBPassword); err != nil {
		return err
	}
	// Always set CANASTA_IMAGE in .env so the installation is pinned to a
	// specific image. For default installs this is the CLI's pinned version;
	// for --canasta-image or --build-from it's the user-specified image.
	if err := canasta.SaveEnvVariable(path+"/.env", "CANASTA_IMAGE", baseImage); err != nil {
		return err
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
	if composerFile != "" {
		if err := canasta.CopyComposerFile(path, composerFile, workingDir); err != nil {
			return err
		}
	}
	isK8s := orchestrator == "kubernetes" || orchestrator == "k8s"

	// For managed K8s clusters, create the kind cluster with port mappings
	var kindClusterName string
	if createCluster && isK8s {
		httpPort, httpsPort := orchestrators.GetPortsFromEnv(path)
		kindClusterName = orchestrators.KindClusterName(canastaInfo.Id)
		if err := orchestrators.CreateKindCluster(kindClusterName, httpPort, httpsPort); err != nil {
			return fmt.Errorf("failed to create kind cluster: %w", err)
		}
	}

	// Handle K8s image distribution before InitConfig so .env has the
	// correct CANASTA_IMAGE when kustomization.yaml is generated.
	if isK8s && localImageBuilt {
		if kindClusterName != "" {
			// Load image directly into kind (no registry needed)
			logging.Print("Loading image into kind cluster...\n")
			if err := orchestrators.LoadImageToKind(kindClusterName, baseImage); err != nil {
				return fmt.Errorf("failed to load image into kind: %w", err)
			}
		} else {
			// Push to a registry the cluster can access
			logging.Print("Pushing image to registry for Kubernetes...\n")
			remoteTag, err := imagebuild.PushImage(baseImage, registry)
			if err != nil {
				return fmt.Errorf("failed to push image to registry: %w", err)
			}
			// Update .env so kustomization.yaml references the registry image
			if err := canasta.SaveEnvVariable(path+"/.env", "CANASTA_IMAGE", remoteTag); err != nil {
				return err
			}
		}
	}

	if err := orch.InitConfig(path); err != nil {
		return err
	}
	if override != "" {
		compose, ok := orch.(*orchestrators.ComposeOrchestrator)
		if !ok {
			return fmt.Errorf("--override is only supported with Docker Compose")
		}
		if err := compose.CopyOverrideFile(path, override, workingDir); err != nil {
			return err
		}
	}

	// Start without dev mode for installation
	// (xdebug can cause hangs if a debugger is listening during install.php)
	tempInstance := config.Installation{Path: path, Orchestrator: orchestrator, DevMode: false, KindCluster: kindClusterName}
	if err := orch.Start(tempInstance); err != nil {
		return err
	}

	// If database path is provided, import the database instead of running install.php
	if databasePath != "" {
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
		if err := orchestrators.ImportDatabase(orch, wikiID, databasePath, dbPassword, tempInstance); err != nil {
			return err
		}
		// Generate secret key and save to .env (DB password already in .env)
		if err := canasta.GenerateAndSaveSecretKey(path); err != nil {
			return err
		}
	} else {
		// Run MediaWiki installer
		if _, err := mediawiki.Install(path, yamlPath, orch, canastaInfo); err != nil {
			return err
		}
	}

	reg := ""
	if isK8s {
		reg = registry
	}
	instance := config.Installation{Id: canastaInfo.Id, Path: path, Orchestrator: orchestrator, DevMode: false, ManagedCluster: createCluster, Registry: reg, KindCluster: kindClusterName, BuildFrom: buildFromPath}
	if err := config.Add(instance); err != nil {
		return err
	}

	// Restart to apply all settings
	// Stop containers (started without dev mode)
	if err := orch.Stop(tempInstance); err != nil {
		return err
	}

	// Start with appropriate mode
	if err := orch.Start(instance); err != nil {
		return err
	}

	return nil
}

func deleteConfigAndContainers(installationDir string, orch orchestrators.Orchestrator) {
	fmt.Println("Removing containers")
	_, _ = orch.Destroy(installationDir)
	// Clean up any kind cluster created during this attempt.
	// KindClusterName derives the name from the directory basename (the ID).
	// DeleteKindCluster is a no-op if the cluster doesn't exist.
	if _, ok := orch.(*orchestrators.KubernetesOrchestrator); ok {
		clusterName := orchestrators.KindClusterName(filepath.Base(installationDir))
		_ = orchestrators.DeleteKindCluster(clusterName)
	}
	fmt.Println("Deleting config files")
	_, _ = orchestrators.DeleteConfig(installationDir)
	fmt.Println("Deleted all containers and config files")
}
