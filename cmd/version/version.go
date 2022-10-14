package version

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

var (
	sha1      string
	buildTime string
)

func NewCmdCreate() *cobra.Command {
	var versionCmd = &cobra.Command{
		Use:   "version",
		Short: "Show the Canasta version",
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Printf("This is canasta: built at %s from git commit %s.\n", buildTime, sha1)
			os.Exit(0)
			return nil
		},
	}
	return versionCmd
}
