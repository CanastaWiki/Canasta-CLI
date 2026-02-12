package orchestrators

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
)

// New creates an Orchestrator implementation for the given orchestrator ID.
// This is the single point where orchestrator IDs are mapped to implementations.
func New(orchestratorID string) Orchestrator {
	switch orchestratorID {
	case "compose", "docker-compose":
		return &ComposeOrchestrator{}
	default:
		logging.Fatal(fmt.Errorf("orchestrator: %s is not available", orchestratorID))
		return nil // unreachable, but satisfies compiler
	}
}
