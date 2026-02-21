package create

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/CanastaWiki/Canasta-CLI/internal/canasta"
	"github.com/CanastaWiki/Canasta-CLI/internal/config"
	"github.com/CanastaWiki/Canasta-CLI/internal/imagebuild"
	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/CanastaWiki/Canasta-CLI/internal/orchestrators"
	"github.com/CanastaWiki/Canasta-CLI/internal/spinner"
)

func newK8sCmd(opts *CreateOptions) *cobra.Command {
	var (
		registry      string
		createCluster bool
	)

	cmd := &cobra.Command{
		Use:   "k8s",
		Short: "Create a Canasta installation using Kubernetes",
		Long: `Create a new Canasta MediaWiki installation on Kubernetes. Use
--create-cluster to have the CLI manage a local kind cluster, or omit it
to deploy to an existing cluster.`,
		Example: `  # Create with a managed kind cluster (recommended)
  canasta create k8s --create-cluster -i my-wiki -w main -a admin -n localhost

  # Deploy to an existing cluster
  canasta create k8s -i my-wiki -w main -a admin -n my-wiki.example.com

  # Build from source with a managed cluster
  canasta create k8s --create-cluster --build-from /path/to/workspace -i my-wiki -w main -a admin -n localhost`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := validateOpts(cmd, opts); err != nil {
				return err
			}
			return createK8s(opts, registry, createCluster)
		},
	}

	cmd.Flags().StringVar(&registry, "registry", "localhost:5000", "Container registry for pushing locally built images (used with --build-from)")
	cmd.Flags().BoolVar(&createCluster, "create-cluster", false, "Create and manage a local Kubernetes cluster for this installation")

	return cmd
}

func createK8s(opts *CreateOptions, registry string, createCluster bool) error {
	stopSpinner := spinner.New("Creating Canasta installation '" + opts.CanastaInfo.Id + "'...")

	orch := &orchestrators.KubernetesOrchestrator{ManagedCluster: createCluster}
	if err := orch.CheckDependencies(); err != nil {
		return err
	}

	baseImage, localImageBuilt, err := determineBaseImage(opts.BuildFromPath, opts.DevTag)
	if err != nil {
		return err
	}

	path, err := setupInstallation(opts, orch, baseImage)
	if err != nil {
		stopSpinner()
		return err
	}

	// After this point, failure requires cleanup.
	// kindClusterName is captured by reference so the fail closure
	// can clean up the cluster even if it's created later.
	kindClusterName := ""
	fail := func(err error) error {
		stopSpinner()
		fmt.Println(err.Error())
		if !opts.KeepConfig {
			deleteConfigAndContainers(path, orch, kindClusterName)
			return fmt.Errorf("Installation failed and files were cleaned up")
		}
		return fmt.Errorf("Installation failed. Keeping all the containers and config files")
	}

	// Create kind cluster if requested
	if createCluster {
		httpPort, httpsPort := orchestrators.GetPortsFromEnv(path)
		kindClusterName = orchestrators.KindClusterName(opts.CanastaInfo.Id)
		if err := orchestrators.CreateKindCluster(kindClusterName, httpPort, httpsPort); err != nil {
			return fail(fmt.Errorf("failed to create kind cluster: %w", err))
		}
	}

	// Distribute locally built image before InitConfig so .env has the
	// correct CANASTA_IMAGE when kustomization.yaml is generated.
	if localImageBuilt {
		if kindClusterName != "" {
			logging.Print("Loading image into kind cluster...\n")
			if err := orchestrators.LoadImageToKind(kindClusterName, baseImage); err != nil {
				return fail(fmt.Errorf("failed to load image into kind: %w", err))
			}
		} else {
			logging.Print("Pushing image to registry for Kubernetes...\n")
			remoteTag, err := imagebuild.PushImage(baseImage, registry)
			if err != nil {
				return fail(fmt.Errorf("failed to push image to registry: %w", err))
			}
			if err := canasta.SaveEnvVariable(path+"/.env", "CANASTA_IMAGE", remoteTag); err != nil {
				return fail(err)
			}
		}
	}

	if err := orch.InitConfig(path); err != nil {
		return fail(err)
	}

	instance := config.Installation{
		Id:             opts.CanastaInfo.Id,
		Path:           path,
		Orchestrator:   "kubernetes",
		ManagedCluster: createCluster,
		Registry:       registry,
		KindCluster:    kindClusterName,
	}
	if err := installAndRegister(opts, path, orch, instance); err != nil {
		return fail(err)
	}

	stopSpinner()
	fmt.Println("\033[32mIf you need email enabled for this wiki, please set $wgSMTP; email will not work otherwise. See https://mediawiki.org/wiki/Manual:$wgSMTP for options.\033[0m")
	fmt.Println("Done.")
	return nil
}
