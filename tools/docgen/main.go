package main

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

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

	// Generate documentation for all commands
	if err := genMarkdownTree(rootCmd, docsDir); err != nil {
		log.Fatalf("Failed to generate documentation: %v", err)
	}

	log.Printf("Documentation generated successfully in %s", docsDir)
}

// genMarkdownTree generates markdown documentation for a command and all its subcommands
func genMarkdownTree(cmd *cobra.Command, dir string) error {
	for _, c := range cmd.Commands() {
		if !c.IsAvailableCommand() || c.IsAdditionalHelpTopicCommand() {
			continue
		}
		if err := genMarkdownTree(c, dir); err != nil {
			return err
		}
	}

	basename := strings.ReplaceAll(cmd.CommandPath(), " ", "_") + ".md"
	filename := filepath.Join(dir, basename)
	f, err := os.Create(filename)
	if err != nil {
		return err
	}
	defer f.Close()

	if err := genMarkdown(cmd, f); err != nil {
		return err
	}
	return nil
}

// genMarkdown generates the markdown documentation for a single command
func genMarkdown(cmd *cobra.Command, w *os.File) error {
	cmd.InitDefaultHelpCmd()
	cmd.InitDefaultHelpFlag()

	buf := new(strings.Builder)
	name := cmd.CommandPath()

	buf.WriteString("## " + name + "\n\n")
	buf.WriteString(cmd.Short + "\n\n")

	if len(cmd.Long) > 0 {
		buf.WriteString("### Synopsis\n\n")
		buf.WriteString(cmd.Long + "\n\n")
	}

	if cmd.Runnable() {
		buf.WriteString("```\n")
		buf.WriteString(cmd.UseLine() + "\n")
		buf.WriteString("```\n\n")
	}

	if len(cmd.Example) > 0 {
		buf.WriteString("### Examples\n\n")
		buf.WriteString("```\n")
		buf.WriteString(cmd.Example + "\n")
		buf.WriteString("```\n\n")
	}

	// Print options as a table
	if hasNonInheritedFlags(cmd) {
		buf.WriteString("### Options\n\n")
		buf.WriteString(flagsToTable(cmd.NonInheritedFlags()))
		buf.WriteString("\n")
	}

	// Print inherited options as a table
	if hasInheritedFlags(cmd) {
		buf.WriteString("### Options inherited from parent commands\n\n")
		buf.WriteString(flagsToTable(cmd.InheritedFlags()))
		buf.WriteString("\n")
	}

	// Print see also section
	if hasSeeAlso(cmd) {
		buf.WriteString("### SEE ALSO\n\n")
		if cmd.HasParent() {
			parent := cmd.Parent()
			pname := parent.CommandPath()
			link := strings.ReplaceAll(pname, " ", "_") + ".md"
			buf.WriteString(fmt.Sprintf("* [%s](%s)\t - %s\n", pname, link, parent.Short))
			cmd.VisitParents(func(c *cobra.Command) {
				if c.DisableAutoGenTag {
					cmd.DisableAutoGenTag = c.DisableAutoGenTag
				}
			})
		}

		children := cmd.Commands()
		sort.Sort(byName(children))

		for _, child := range children {
			if !child.IsAvailableCommand() || child.IsAdditionalHelpTopicCommand() {
				continue
			}
			cname := name + " " + child.Name()
			link := strings.ReplaceAll(cname, " ", "_") + ".md"
			buf.WriteString(fmt.Sprintf("* [%s](%s)\t - %s\n", cname, link, child.Short))
		}
		buf.WriteString("\n")
	}

	_, err := w.WriteString(buf.String())
	return err
}

// flagsToTable converts a FlagSet to a markdown table
func flagsToTable(flags *pflag.FlagSet) string {
	buf := new(strings.Builder)
	buf.WriteString("| Flag | Shorthand | Description | Default |\n")
	buf.WriteString("|------|-----------|-------------|---------|\n")

	flags.VisitAll(func(flag *pflag.Flag) {
		if flag.Hidden {
			return
		}

		shorthand := ""
		if flag.Shorthand != "" {
			shorthand = "`-" + flag.Shorthand + "`"
		}

		name := "`--" + flag.Name + "`"

		// Escape pipe characters in description
		description := strings.ReplaceAll(flag.Usage, "|", "\\|")
		// Replace newlines with spaces
		description = strings.ReplaceAll(description, "\n", " ")

		defaultVal := ""
		if flag.DefValue != "" && flag.DefValue != "false" && flag.DefValue != "0" && flag.DefValue != "[]" {
			defaultVal = "`" + flag.DefValue + "`"
		}

		buf.WriteString(fmt.Sprintf("| %s | %s | %s | %s |\n", name, shorthand, description, defaultVal))
	})

	return buf.String()
}

func hasNonInheritedFlags(cmd *cobra.Command) bool {
	found := false
	cmd.NonInheritedFlags().VisitAll(func(f *pflag.Flag) {
		if !f.Hidden {
			found = true
		}
	})
	return found
}

func hasInheritedFlags(cmd *cobra.Command) bool {
	found := false
	cmd.InheritedFlags().VisitAll(func(f *pflag.Flag) {
		if !f.Hidden {
			found = true
		}
	})
	return found
}

func hasSeeAlso(cmd *cobra.Command) bool {
	if cmd.HasParent() {
		return true
	}
	for _, c := range cmd.Commands() {
		if !c.IsAvailableCommand() || c.IsAdditionalHelpTopicCommand() {
			continue
		}
		return true
	}
	return false
}

type byName []*cobra.Command

func (s byName) Len() int           { return len(s) }
func (s byName) Swap(i, j int)      { s[i], s[j] = s[j], s[i] }
func (s byName) Less(i, j int) bool { return s[i].Name() < s[j].Name() }
