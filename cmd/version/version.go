package version

import (
	"fmt"

	"github.com/spf13/cobra"
)

var (
	// Version is set via ldflags at build time, e.g. "v1.52.0"; empty for dev builds
	Version   string
	sha1      string
	buildTime string
)

func displayVersion() string {
	v := Version
	if v == "" {
		v = "dev"
	}
	return fmt.Sprintf("Canasta CLI %s (commit %s, built %s)", v, sha1, buildTime)
}

func NewCmd() *cobra.Command {
	var versionCmd = &cobra.Command{
		Use:   "version",
		Short: "Show the Canasta version",
		Long: `Display the Canasta CLI version, git commit hash, and build timestamp.
Shows "dev" if the binary was built without version information.`,
		Example: `  canasta version`,
		RunE: func(_ *cobra.Command, _ []string) error {
			fmt.Println(displayVersion())
			return nil
		},
	}
	return versionCmd
}
