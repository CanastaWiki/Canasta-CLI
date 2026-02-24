package restart

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

func NewCmdCreate() *cobra.Command {
	var instance config.Installation
	var verbose bool

	workingDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}
	instance.Path = workingDir

	var restartCmd = &cobra.Command{
		Use:   "restart",
		Short: "Restart the Canasta installation",
		Long: `Restart a Canasta installation by stopping and then starting all Docker
containers. Any pending configuration migrations are applied during the
restart. Use 'canasta devmode enable' or 'canasta devmode disable' to
change the development mode setting.`,
		Example: `  # Restart an installation by ID
  canasta restart -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			logging.SetVerbose(verbose)
			if instance.Id == "" && len(args) > 0 {
				instance.Id = args[0]
			}
			resolvedInstance, err := canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			fmt.Println("Restarting Canasta installation '" + resolvedInstance.Id + "'...")
			if err = Restart(resolvedInstance); err != nil {
				return err
			}
			fmt.Println("Restarted.")
			return nil
		},
	}
	restartCmd.Flags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	restartCmd.Flags().BoolVarP(&verbose, "verbose", "v", false, "Verbose Output")
	return restartCmd
}

func Restart(instance config.Installation) error {
	orch, err := orchestrators.NewFromInstance(instance)
	if err != nil {
		return err
	}

	// Migrate to the new version Canasta
	if err = canasta.MigrateToNewVersion(instance.Path); err != nil {
		return err
	}

	// Stop containers
	if err = orch.Stop(instance); err != nil {
		return err
	}

	// Regenerate orchestrator config (Compose: Caddyfile; K8s: kustomization.yaml)
	if err := orch.UpdateConfig(instance.Path); err != nil {
		return err
	}

	// Start containers
	return orch.Start(instance)
}
