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
	"github.com/CanastaWiki/Canasta-CLI/internal/mediawiki"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
)

func NewCmdCreate() *cobra.Command {
	var (
		path         string
		orchestrator string
		workingDir   string
		wikiID       string
		siteName     string
		domain       string
		yamlPath     string
		err          error
		keepConfig   bool
		canastaInfo  canasta.CanastaVariables
		override     string
		envFile      string
	)
	createCmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Canasta installation",
		Long:  "Creates a Canasta installation using an orchestrator of your choice.",
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

			// Validate Canasta ID format
			validString := regexp.MustCompile(`^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$`)
			if !validString.MatchString(canastaInfo.Id) {
				log.Fatal(fmt.Errorf("Error: CanastaID should not contain spaces or non-ASCII characters, only alphanumeric characters are allowed"))
			}

			// Generate passwords (auto-gen if not provided via flags)
			if canastaInfo, err = canasta.GeneratePasswords(workingDir, canastaInfo); err != nil {
				log.Fatal(err)
			}

			description := "Creating Canasta installation '" + canastaInfo.Id + "'..."
			_, done := spinner.New(description)

			if err = createCanasta(canastaInfo, workingDir, path, wikiID, siteName, domain, yamlPath, orchestrator, override, envFile, done); err != nil {
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

	// Mark required flags
	createCmd.MarkFlagRequired("id")
	createCmd.MarkFlagRequired("admin")

	return createCmd
}

// importCanasta accepts all the keyword arguments and create a installation of the latest Canasta.
func createCanasta(canastaInfo canasta.CanastaVariables, workingDir, path, wikiID, siteName, domain, yamlPath, orchestrator, override, envFile string, done chan struct{}) error {
	// Pass a message to the "done" channel indicating the completion of createCanasta function.
	// This signals the spinner to stop printing progress, regardless of success or failure.
	defer func() {
		done <- struct{}{}
	}()
	if _, err := config.GetDetails(canastaInfo.Id); err == nil {
		log.Fatal(fmt.Errorf("Canasta installation with the ID already exist!"))
	}
	// Clone the stack repository first to create the installation directory
	if err := canasta.CloneStackRepo(orchestrator, canastaInfo.Id, &path); err != nil {
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
	if err := canasta.CopySettings(path); err != nil {
		return err
	}
	if err := canasta.RewriteCaddy(path); err != nil {
		return err
	}
	if err := orchestrators.CopyOverrideFile(path, orchestrator, override, workingDir); err != nil {
		return err
	}
	if err := orchestrators.Start(path, orchestrator); err != nil {
		return err
	}
	if _, err := mediawiki.Install(path, yamlPath, orchestrator, canastaInfo); err != nil {
		return err
	}
	if err := config.Add(config.Installation{Id: canastaInfo.Id, Path: path, Orchestrator: orchestrator}); err != nil {
		return err
	}
	if err := orchestrators.StopAndStart(path, orchestrator); err != nil {
		log.Fatal(err)
	}
	fmt.Println("\033[32mIf you need email enabled for this wiki, please set $wgSMTP; email will not work otherwise. See https://mediawiki.org/wiki/Manual:$wgSMTP for options.\033[0m")
	return nil
}
