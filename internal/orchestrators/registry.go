package orchestrators

import (
	"fmt"

	"github.com/CanastaWiki/Canasta-CLI/internal/config"
)

// New creates an Orchestrator implementation for the given orchestrator ID.
// This is the single point where orchestrator IDs are mapped to implementations.
func New(orchestratorID string) (Orchestrator, error) {
	switch orchestratorID {
	case "compose", "docker-compose":
		return &ComposeOrchestrator{}, nil
	case "kubernetes", "k8s":
		return &KubernetesOrchestrator{}, nil
	default:
		return nil, fmt.Errorf("orchestrator: %s is not available", orchestratorID)
	}
}

// NewFromInstance creates an Orchestrator from a config.Installation, restoring
// any orchestrator-specific state (e.g. ManagedCluster for Kubernetes).
func NewFromInstance(instance config.Installation) (Orchestrator, error) {
	orch, err := New(instance.Orchestrator)
	if err != nil {
		return nil, err
	}
	if k8s, ok := orch.(*KubernetesOrchestrator); ok {
		k8s.ManagedCluster = instance.ManagedCluster
	}
	return orch, nil
}
