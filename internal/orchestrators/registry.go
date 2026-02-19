package orchestrators

import (
	"fmt"
)

// New creates an Orchestrator implementation for the given orchestrator ID.
// This is the single point where orchestrator IDs are mapped to implementations.
func New(orchestratorID string) (Orchestrator, error) {
	switch orchestratorID {
	case "compose", "docker-compose":
		return &ComposeOrchestrator{}, nil
	case "kubernetes", "k8s":
		return nil, fmt.Errorf("kubernetes orchestrator not yet implemented")
	default:
		return nil, fmt.Errorf("orchestrator: %s is not available", orchestratorID)
	}
}
