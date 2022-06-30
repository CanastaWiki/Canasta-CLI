package git

import (
	"os/exec"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func Clone(repo, path string) error {

	err := exec.Command("git", "clone", repo, path).Run()
	if err != nil {
		logging.Fatal(err)
	}
	return nil
}
