package git

import (
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/execute"
)

func Clone(repo, path string) {
	execute.Run("", "git", "clone", repo, path)
}
