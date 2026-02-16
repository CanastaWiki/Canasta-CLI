package backup

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI/internal/logging"
	"github.com/spf13/cobra"
)

func scheduleCmdCreate() *cobra.Command {
	scheduleCmd := &cobra.Command{
		Use:   "schedule [cron expression]",
		Short: "Schedule a recurring backup",
		Long: `Schedule recurring backups using a cron expression. This adds or
updates a crontab entry that runs 'canasta backup create' on the
specified schedule. Backup output is logged to /var/log/canasta-backup.log.`,
		Example: `  # Schedule daily backups at 2:00 AM
  canasta backup schedule -i myinstance "0 2 * * *"

  # Schedule hourly backups
  canasta backup schedule -i myinstance "0 * * * *"`,
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return scheduleBackup(strings.Join(args, " "))
		},
	}
	return scheduleCmd
}

func scheduleBackup(cronExpression string) error {
	if err := validateCron(cronExpression); err != nil {
		return err
	}

	executable, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to get executable path: %w", err)
	}

	cwd := instance.Path
	cmdStr := fmt.Sprintf("%s cd %s && %s backup create --tag scheduled-$(date +\\%%Y\\%%m\\%%d\\%%H\\%%M\\%%S) >> /var/log/canasta-backup.log 2>&1", cronExpression, cwd, executable)

	logging.Print(fmt.Sprintf("Scheduling backup with cron: %s", cronExpression))

	out, err := exec.Command("crontab", "-l").Output()
	currentCrontab := string(out)
	if err != nil {
		if exitError, ok := err.(*exec.ExitError); ok {
			if exitError.ExitCode() != 1 {
				return fmt.Errorf("failed to read crontab: %w", err)
			}
			currentCrontab = ""
		}
	}

	// Parse and update crontab if already scheduled
	lines := strings.Split(currentCrontab, "\n")
	var newLines []string
	updated := false
	jobIdentifier := fmt.Sprintf("cd %s &&", cwd)

	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			continue
		}
		if strings.Contains(line, jobIdentifier) && strings.Contains(line, "backup create") {
			newLines = append(newLines, cmdStr)
			updated = true
			logging.Print("Updated existing backup schedule.")
		} else {
			newLines = append(newLines, line)
		}
	}

	if !updated {
		newLines = append(newLines, cmdStr)
		logging.Print("Added new backup schedule.")
	}
	
	newCrontab := strings.Join(newLines, "\n") + "\n"

	cmd := exec.Command("crontab", "-")
	cmd.Stdin = strings.NewReader(newCrontab)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to update crontab: %s, %w", string(output), err)
	}

	logging.Print("Backup scheduled successfully.")
	return nil
}

func validateCron(cron string) error {
	fields := strings.Fields(cron)
	if len(fields) != 5 {
		return fmt.Errorf("invalid cron expression: expected 5 fields, got %d", len(fields))
	}
	validChars := "0123456789*,/-"
	for _, field := range fields {
		for _, char := range field {
			if !strings.ContainsRune(validChars, char) {
				return fmt.Errorf("invalid character '%c' in cron expression", char)
			}
		}
	}
	return nil
}
