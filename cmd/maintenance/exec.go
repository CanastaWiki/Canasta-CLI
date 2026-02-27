package maintenance

import (
	"fmt"
	"sort"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func newExecCmd(instance *config.Installation) *cobra.Command {
	var service string

	cmd := &cobra.Command{
		Use:   "exec [-- command ...]",
		Short: "Execute a command in a running container",
		Long: `Execute a command or open an interactive shell in a running container
of a Canasta installation.

With no arguments and no --service flag, lists the running services.
With --service (or -s) and no command, opens an interactive bash shell.
With --service and a command after --, runs that command.

The default service is "web" (the MediaWiki container).`,
		Example: `  # List running services
  canasta maintenance exec -i myinstance

  # Open a shell in the web container
  canasta maintenance exec -i myinstance -s web

  # Run a command in the web container
  canasta maintenance exec -i myinstance -s web -- ls /var/www

  # Default service is "web", so this also works
  canasta maintenance exec -i myinstance -- php -v`,
		PreRunE: func(cmd *cobra.Command, args []string) error {
			var err error
			*instance, err = canasta.CheckCanastaId(*instance)
			return err
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			orch, err := orchestrators.New(instance.Orchestrator)
			if err != nil {
				return err
			}

			// No service flag and no args: list services
			if service == "" && len(args) == 0 {
				services, err := orch.ListServices(*instance)
				if err != nil {
					return err
				}
				if len(services) == 0 {
					fmt.Println("No running services found.")
					return nil
				}
				sort.Strings(services)
				fmt.Println("Running services:")
				for _, s := range services {
					fmt.Printf("  %s\n", s)
				}
				return nil
			}

			// Default service to "web" if not specified
			if service == "" {
				service = orchestrators.ServiceWeb
			}

			// Ensure containers are running
			if err := orch.CheckRunningStatus(*instance); err != nil {
				return fmt.Errorf("containers are not running: %w", err)
			}

			// Warning message
			fmt.Println("WARNING: You are about to execute a command directly inside a container.")
			fmt.Println("Changes made here are not managed by the Canasta CLI and may not persist")
			fmt.Println("across restarts.")
			fmt.Println()

			// Default command to bash shell
			command := args
			if len(command) == 0 {
				command = []string{"/bin/bash"}
			}

			return orch.ExecInteractive(*instance, service, command)
		},
	}

	cmd.Flags().StringVarP(&service, "service", "s", "", "Service name (default: web)")
	return cmd
}
