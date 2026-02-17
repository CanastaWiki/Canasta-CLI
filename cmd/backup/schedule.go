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
		Use:   "schedule",
		Short: "Manage scheduled backups",
		Long:  `Manage recurring backup schedules using crontab.`,
	}

	scheduleCmd.AddCommand(scheduleSetCmdCreate())
	scheduleCmd.AddCommand(scheduleListCmdCreate())
	scheduleCmd.AddCommand(scheduleRemoveCmdCreate())
	return scheduleCmd
}

func scheduleSetCmdCreate() *cobra.Command {
	return &cobra.Command{
		Use:   "set [cron expression]",
		Short: "Set a recurring backup schedule",
		Long: `Schedule recurring backups using a cron expression. This adds or
updates a crontab entry that runs 'canasta backup create' on the
specified schedule. Backup output is logged to /var/log/canasta-backup.log.`,
		Example: `  # Schedule daily backups at 2:00 AM
  canasta backup schedule set -i myinstance "0 2 * * *"

  # Schedule hourly backups
  canasta backup schedule set -i myinstance "0 * * * *"`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return scheduleBackup(strings.Join(args, " "))
		},
	}
}

func scheduleListCmdCreate() *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "Show the backup schedule",
		Long:  `Show the current backup schedule for this installation, if one exists.`,
		Example: `  canasta backup schedule list -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return listSchedule()
		},
	}
}

func scheduleRemoveCmdCreate() *cobra.Command {
	return &cobra.Command{
		Use:   "remove",
		Short: "Remove a scheduled backup",
		Long:  `Remove the crontab entry for recurring backups of this installation.`,
		Example: `  canasta backup schedule remove -i myinstance`,
		RunE: func(cmd *cobra.Command, args []string) error {
			return unscheduleBackup()
		},
	}
}

// jobIdentifierForInstance returns the string used to identify a crontab entry for a given instance ID.
func jobIdentifierForInstance(id string) string {
	return fmt.Sprintf("backup create -i %s", id)
}

// jobIdentifier returns the string used to identify a crontab entry for this instance.
func jobIdentifier() string {
	return jobIdentifierForInstance(instance.Id)
}

// readCrontab returns the current crontab contents as non-empty lines.
func readCrontab() ([]string, error) {
	out, err := exec.Command("crontab", "-l").Output()
	if err != nil {
		if exitError, ok := err.(*exec.ExitError); ok {
			if exitError.ExitCode() == 1 {
				return nil, nil
			}
		}
		return nil, fmt.Errorf("failed to read crontab: %w", err)
	}
	var lines []string
	for _, line := range strings.Split(string(out), "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	return lines, nil
}

// writeCrontab writes the given lines as the new crontab.
func writeCrontab(lines []string) error {
	newCrontab := strings.Join(lines, "\n") + "\n"
	cmd := exec.Command("crontab", "-")
	cmd.Stdin = strings.NewReader(newCrontab)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to update crontab: %s, %w", string(output), err)
	}
	return nil
}

func scheduleBackup(cronExpression string) error {
	if err := validateCron(cronExpression); err != nil {
		return err
	}

	executable, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to get executable path: %w", err)
	}

	cmdStr := fmt.Sprintf("%s %s backup create -i %s --tag scheduled-$(date +\\%%Y\\%%m\\%%d\\%%H\\%%M\\%%S) >> /var/log/canasta-backup.log 2>&1", cronExpression, executable, instance.Id)

	logging.Print(fmt.Sprintf("Scheduling backup with cron: %s", cronExpression))

	lines, err := readCrontab()
	if err != nil {
		return err
	}

	var newLines []string
	updated := false
	identifier := jobIdentifier()

	for _, line := range lines {
		if strings.Contains(line, identifier) {
			oldCron := cronFromLine(line)
			fmt.Printf("Replacing existing schedule '%s' with '%s' for instance '%s'.\n", oldCron, cronExpression, instance.Id)
			fmt.Println("Tip: to schedule multiple times, combine them in one expression (e.g., \"0 0 * * 2,5\" for Tuesdays and Fridays).")
			newLines = append(newLines, cmdStr)
			updated = true
		} else {
			newLines = append(newLines, line)
		}
	}

	if !updated {
		newLines = append(newLines, cmdStr)
	}

	if err := writeCrontab(newLines); err != nil {
		return err
	}

	fmt.Println("Backup scheduled successfully.")
	return nil
}

func listSchedule() error {
	lines, err := readCrontab()
	if err != nil {
		return err
	}

	identifier := jobIdentifier()
	for _, line := range lines {
		if strings.Contains(line, identifier) {
			fmt.Printf("Instance '%s' is scheduled for backup at: %s\n", instance.Id, cronFromLine(line))
			return nil
		}
	}

	return fmt.Errorf("no backup schedule found for instance '%s'", instance.Id)
}

func unscheduleBackup() error {
	removed, err := RemoveSchedule(instance.Id)
	if err != nil {
		return err
	}
	if !removed {
		return fmt.Errorf("no backup schedule found for instance '%s'", instance.Id)
	}
	fmt.Println("Backup schedule removed.")
	return nil
}

// RemoveSchedule removes any crontab entry for the given instance ID.
// Returns true if an entry was found and removed, false if none existed.
// This is exported so that the delete command can clean up schedules.
func RemoveSchedule(id string) (bool, error) {
	lines, err := readCrontab()
	if err != nil {
		return false, err
	}

	var newLines []string
	found := false
	identifier := jobIdentifierForInstance(id)

	for _, line := range lines {
		if strings.Contains(line, identifier) {
			found = true
		} else {
			newLines = append(newLines, line)
		}
	}

	if !found {
		return false, nil
	}

	if len(newLines) == 0 {
		// No entries left — remove the crontab entirely
		cmd := exec.Command("crontab", "-r")
		if output, err := cmd.CombinedOutput(); err != nil {
			return false, fmt.Errorf("failed to remove crontab: %s, %w", string(output), err)
		}
	} else {
		if err := writeCrontab(newLines); err != nil {
			return false, err
		}
	}

	return true, nil
}

// cronFromLine extracts the cron expression (first 5 fields) from a crontab line.
func cronFromLine(line string) string {
	fields := strings.Fields(line)
	if len(fields) >= 5 {
		return strings.Join(fields[:5], " ")
	}
	return line
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
