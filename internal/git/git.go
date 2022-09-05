package git

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func Clone(repo, path string) {
	err, output := execute.Run("", "git", "clone", repo, path)
	if err != nil {
		logging.Fatal(fmt.Errorf(output))
	}
}
