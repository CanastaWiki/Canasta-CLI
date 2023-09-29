package git

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI/internal/execute"
)

func Clone(repo, path string) error {
	err, output := execute.Run("", "git", "clone", repo, path)
	if err != nil {
		return fmt.Errorf(output)
	}
	return nil
}
