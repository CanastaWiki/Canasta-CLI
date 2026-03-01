package extensionsskins

import (
	"fmt"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/maintenance"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
)

// NewCmd builds the full command tree (parent + enable/disable/list subcommands)
// for an extension-or-skin item type.
func NewCmd(constants Item) *cobra.Command {
	var (
		instance config.Installation
		orch     orchestrators.Orchestrator
		wiki     string
	)

	workingDir, wdErr := os.Getwd()
	if wdErr != nil {
		logging.Fatal(wdErr)
	}
	instance.Path = workingDir

	cmd := &cobra.Command{
		Use:   constants.CmdName,
		Short: fmt.Sprintf("Manage Canasta %s", constants.Plural),
		Long: fmt.Sprintf(`Manage MediaWiki %s in a Canasta installation. Subcommands allow you
to list all available %s, and enable or disable them globally or for
a specific wiki in a farm.`, constants.Plural, constants.Plural),
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			var err error
			instance, err = canasta.CheckCanastaId(instance)
			if err != nil {
				return err
			}
			orch, err = orchestrators.New(instance.Orchestrator)
			return err
		},
	}

	cmd.PersistentFlags().StringVarP(&instance.Id, "id", "i", "", "Canasta instance ID")
	cmd.PersistentFlags().StringVarP(&wiki, "wiki", "w", "", "ID of the specific wiki within the Canasta farm")

	cmd.AddCommand(newListCmd(&instance, &orch, &wiki, &constants))
	cmd.AddCommand(newEnableCmd(&instance, &orch, &wiki, &constants))
	cmd.AddCommand(newDisableCmd(&instance, &orch, &wiki, &constants))

	return cmd
}

func newListCmd(instance *config.Installation, orch *orchestrators.Orchestrator, wiki *string, constants *Item) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: fmt.Sprintf("Lists all the installed Canasta %s", constants.Plural),
		Long: fmt.Sprintf(`List all Canasta %s available in the installation. Each %s
is shown with its enabled/disabled status.`, constants.Plural, constants.CmdName),
		Example: fmt.Sprintf("  canasta %s list -i myinstance", constants.CmdName),
		RunE: func(cmd *cobra.Command, args []string) error {
			return List(*instance, *orch, *constants)
		},
	}
}

func newEnableCmd(instance *config.Installation, orch *orchestrators.Orchestrator, wiki *string, constants *Item) *cobra.Command {
	var skipUpdate bool

	// Build the Use string with an appropriate argument placeholder
	argName := strings.ToUpper(constants.CmdName)
	useStr := fmt.Sprintf("enable %s1,%s2,...", argName, argName)

	// Build example text
	firstName := strings.SplitN(constants.ExampleNames, ",", 2)[0]
	example := fmt.Sprintf(`  # Enable a single %s
  canasta %s enable %s -i myinstance`, constants.CmdName, constants.CmdName, firstName)

	if strings.Contains(constants.ExampleNames, ",") {
		example += fmt.Sprintf(`

  # Enable multiple %s at once
  canasta %s enable %s -i myinstance`, constants.Plural, constants.CmdName, constants.ExampleNames)
	}

	example += fmt.Sprintf(`

  # Enable %s %s for a specific wiki
  canasta %s enable %s -i myinstance -w docs

  # Enable without running update.php
  canasta %s enable %s -i myinstance --skip-update`, article(constants.CmdName), constants.CmdName, constants.CmdName, firstName, constants.CmdName, firstName)

	cmd := &cobra.Command{
		Use:   useStr,
		Short: fmt.Sprintf("Enable a %s", constants.Name),
		Long: fmt.Sprintf(`Enable one or more Canasta %s by name. Multiple %s can be
specified as a comma-separated list. Use the --wiki flag to enable %s %s
for a specific wiki only.

After enabling, update.php is automatically run to apply any required
database changes. Use --skip-update to skip this step.`, constants.Plural, constants.Plural, article(constants.CmdName), constants.CmdName),
		Example: example,
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			names := strings.Split(args[0], ",")
			anyEnabled := false
			for _, name := range names {
				name = strings.TrimSpace(name)
				checkedName, err := CheckInstalled(name, *instance, *orch, *constants)
				if err != nil {
					fmt.Print(err.Error() + "\n")
					continue
				}
				if err := Enable(checkedName, *wiki, *instance, *constants); err != nil {
					return err
				}
				anyEnabled = true
			}
			if anyEnabled && !skipUpdate {
				if err := maintenance.RunUpdateAllWikis(*instance, *orch, *wiki); err != nil {
					return err
				}
			}
			return nil
		},
	}

	cmd.Flags().BoolVar(&skipUpdate, "skip-update", false, "Skip running update.php after enabling")

	return cmd
}

func newDisableCmd(instance *config.Installation, orch *orchestrators.Orchestrator, wiki *string, constants *Item) *cobra.Command {
	// Build the Use string with an appropriate argument placeholder
	argName := strings.ToUpper(constants.CmdName)
	useStr := fmt.Sprintf("disable %s1,%s2,...", argName, argName)

	// Build example text
	firstName := strings.SplitN(constants.ExampleNames, ",", 2)[0]
	example := fmt.Sprintf(`  # Disable a single %s
  canasta %s disable %s -i myinstance`, constants.CmdName, constants.CmdName, firstName)

	example += fmt.Sprintf(`

  # Disable %s %s for a specific wiki
  canasta %s disable %s -i myinstance -w docs`, article(constants.CmdName), constants.CmdName, constants.CmdName, firstName)

	return &cobra.Command{
		Use:   useStr,
		Short: fmt.Sprintf("Disable a %s", constants.Name),
		Long: fmt.Sprintf(`Disable one or more Canasta %s by name. Multiple %s can be
specified as a comma-separated list. Use the --wiki flag to disable %s %s
for a specific wiki only.`, constants.Plural, constants.Plural, article(constants.CmdName), constants.CmdName),
		Example: example,
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			names := strings.Split(args[0], ",")
			for _, name := range names {
				name = strings.TrimSpace(name)
				checkedName, err := CheckEnabled(name, *wiki, *instance, *constants)
				if err != nil {
					fmt.Print(err.Error() + "\n")
					continue
				}
				if err := Disable(checkedName, *wiki, *instance, *constants); err != nil {
					return err
				}
			}
			return nil
		},
	}
}

// article returns "an" for words starting with a vowel sound, "a" otherwise.
func article(word string) string {
	if len(word) > 0 && strings.ContainsRune("aeiouAEIOU", rune(word[0])) {
		return "an"
	}
	return "a"
}
