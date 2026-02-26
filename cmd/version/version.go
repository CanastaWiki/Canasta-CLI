package version

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

var (
	// Version is set via ldflags at build time, e.g. "v1.52.0"; empty for dev builds
	Version   string
	sha1      string
	buildTime string
)

func NewCmd() *cobra.Command {
	var versionCmd = &cobra.Command{
		Use:   "version",
		Short: "Show the Canasta version",
		Long: `Display the Canasta CLI version, git commit hash, and build timestamp.
Shows "dev" if the binary was built without version information.`,
		Example: `  canasta version`,
		RunE: func(cmd *cobra.Command, args []string) error {
			v := Version
			if v == "" {
				v = "dev"
			}
			fmt.Printf("Canasta CLI %s (commit %s, built %s)\n", v, sha1, buildTime)
			os.Exit(0)
			return nil
		},
	}
	return versionCmd
}
