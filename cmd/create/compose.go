package create

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/devmode"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
)

func newComposeCmd(opts *CreateOptions) *cobra.Command {
	var (
		override    string
		devModeFlag bool
	)

	cmd := &cobra.Command{
		Use:   "compose",
		Short: "Create a Canasta installation using Docker Compose",
		Long: `Create a new Canasta MediaWiki installation using Docker Compose. This sets
up the Docker Compose stack, generates configuration files, starts the
containers, and runs the MediaWiki installer. You can optionally import an
existing database dump instead of running the installer, or enable
development mode with Xdebug.`,
		Example: `  # Create a basic single-wiki installation
  canasta create compose -i myinstance -w main -a admin -n example.com

  # Create with an existing database dump
  canasta create compose -i myinstance -w main -d /path/to/dump.sql -n example.com

  # Create with development mode enabled
  canasta create compose -i myinstance -w main -a admin -n localhost -D`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := validateOpts(cmd, opts); err != nil {
				return err
			}
			return createCompose(opts, override, devModeFlag)
		},
	}

	cmd.Flags().StringVarP(&override, "override", "r", "", "Name of a file to copy to docker-compose.override.yml")
	cmd.Flags().BoolVarP(&devModeFlag, "dev", "D", false, "Enable development mode with Xdebug and code extraction")

	return cmd
}

func createCompose(opts *CreateOptions, override string, devMode bool) error {
	description := "Creating Canasta installation '" + opts.CanastaInfo.Id + "'..."
	if devMode {
		description = "Creating Canasta installation '" + opts.CanastaInfo.Id + "' with dev mode..."
	}
	stopSpinner := spinner.New(description)

	orch := &orchestrators.ComposeOrchestrator{}
	if err := orch.CheckDependencies(); err != nil {
		return err
	}

	baseImage, _, err := determineBaseImage(opts.BuildFromPath, opts.DevTag)
	if err != nil {
		return err
	}

	path, err := setupInstallation(opts, orch, baseImage)
	if err != nil {
		stopSpinner()
		return err
	}

	// After this point, failure requires cleanup
	fail := func(err error) error {
		stopSpinner()
		fmt.Println(err.Error())
		if !opts.KeepConfig {
			deleteConfigAndContainers(path, orch, "")
			return fmt.Errorf("Installation failed and files were cleaned up")
		}
		return fmt.Errorf("Installation failed. Keeping all the containers and config files")
	}

	if err := orch.InitConfig(path); err != nil {
		return fail(err)
	}
	if override != "" {
		if err := orch.CopyOverrideFile(path, override, opts.WorkingDir); err != nil {
			return fail(err)
		}
	}
	if devMode {
		if err := devmode.SetupFullDevMode(path, orch, baseImage); err != nil {
			return fail(err)
		}
	}

	instance := config.Installation{
		Id:           opts.CanastaInfo.Id,
		Path:         path,
		Orchestrator: "compose",
		DevMode:      devMode,
	}
	if err := installAndRegister(opts, path, orch, instance); err != nil {
		return fail(err)
	}

	stopSpinner()
	if devMode {
		fmt.Println("\033[32mDevelopment mode enabled. Edit files in mediawiki-code/ - changes appear immediately.\033[0m")
		fmt.Println("\033[32mVSCode: Open the installation directory, install PHP Debug extension, and start 'Listen for Xdebug'.\033[0m")
	}
	fmt.Println("\033[32mIf you need email enabled for this wiki, please set $wgSMTP; email will not work otherwise. See https://mediawiki.org/wiki/Manual:$wgSMTP for options.\033[0m")
	fmt.Println("Done.")
	return nil
}
