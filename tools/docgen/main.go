package main

import (
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra/doc"

	cmd "github.com/CanastaWiki/Canasta-CLI/cmd/root"
)

func main() {
	// Get project root (two directories up from tools/docgen/)
	projectRoot := filepath.Join("..", "..")
	docsDir := filepath.Join(projectRoot, "docs", "cli")

	// Create docs/cli directory if it doesn't exist
	if err := os.MkdirAll(docsDir, 0755); err != nil {
		log.Fatalf("Failed to create docs directory: %v", err)
	}

	// Get the root command from the CLI
	rootCmd := cmd.GetRootCmd()
	rootCmd.DisableAutoGenTag = true

	// Custom link handler to create MkDocs-compatible links
	linkHandler := func(name string) string {
		base := strings.TrimSuffix(name, ".md")
		return base + ".md"
	}

	// Custom file prepender to add MkDocs front matter
	filePrepender := func(filename string) string {
		name := filepath.Base(filename)
		name = strings.TrimSuffix(name, ".md")
		name = strings.ReplaceAll(name, "_", " ")
		return ""
	}

	// Generate markdown documentation
	err := doc.GenMarkdownTreeCustom(rootCmd, docsDir, filePrepender, linkHandler)
	if err != nil {
		log.Fatalf("Failed to generate documentation: %v", err)
	}

	log.Printf("Documentation generated successfully in %s", docsDir)
}
