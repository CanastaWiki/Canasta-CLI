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

func NewCmdCreate() *cobra.Command {
	var versionCmd = &cobra.Command{
		Use:   "version",
		Short: "Show the Canasta version",
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
