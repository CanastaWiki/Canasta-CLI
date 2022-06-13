package git

import (
	"os/exec"
)

func Clone(repo, path string) error {

	err := exec.Command("git", "clone", repo, path).Run()
	if err != nil {
		return err
	} else {
		return nil
	}
}
