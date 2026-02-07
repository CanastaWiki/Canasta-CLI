package system

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
)

// GetAvailableMemory returns the available system memory in bytes.
func GetAvailableMemory() (uint64, error) {
	switch runtime.GOOS {
	case "linux":
		return getLinuxAvailableMemory()
	case "darwin":
		return getDarwinAvailableMemory()
	default:
		return 0, nil
	}
}

func getLinuxAvailableMemory() (uint64, error) {
	file, err := os.Open("/proc/meminfo")
	if err != nil {
		return 0, err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "MemAvailable:") {
			parts := strings.Fields(line)
			if len(parts) < 2 {
				return 0, fmt.Errorf("unexpected format in /proc/meminfo")
			}
			kbStr := parts[1]
			kb, err := strconv.ParseUint(kbStr, 10, 64)
			if err != nil {
				return 0, err
			}
			return kb * 1024, nil
		}
	}
	if err := scanner.Err(); err != nil {
		return 0, err
	}
	return 0, fmt.Errorf("MemAvailable not found in /proc/meminfo")
}

func getDarwinAvailableMemory() (uint64, error) {
	// use vm_stat to get the available memory

	cmd := exec.Command("vm_stat")
	output, err := cmd.Output()
	if err != nil {
		return 0, err
	}

	lines := strings.Split(string(output), "\n")
	var freePages, inactivePages uint64

	for _, line := range lines {
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		key := fields[1]
		valStr := strings.TrimRight(fields[len(fields)-1], ".")

		val, err := strconv.ParseUint(valStr, 10, 64)
		if err != nil {
			continue
		}

		switch key {
		case "free:":
			freePages = val
		case "inactive:":
			inactivePages = val
		}
	}

	pageSize := uint64(4096)
	cmdPage := exec.Command("pagesize")
	if out, err := cmdPage.Output(); err == nil {
		if s, err := strconv.ParseUint(strings.TrimSpace(string(out)), 10, 64); err == nil {
			pageSize = s
		}
	}

	return (freePages + inactivePages) * pageSize, nil
}

// CheckMemoryInGB verifies if the system has at least minGB of available memory.
func CheckMemoryInGB(minGB int) error {
	availMem, err := GetAvailableMemory()
	if err != nil {
		return fmt.Errorf("failed to determine system memory: %w", err)
	}

	if availMem == 0 {
		return nil
	}

	minBytes := gbToByte(minGB)
	if availMem < minBytes {
		return fmt.Errorf("available system memory %.1f GiB is less than required %d GiB", byteToGB(availMem), minGB)
	}

	return nil
}

func gbToByte(gb int) uint64 {
	return uint64(gb) * 1024 * 1024 * 1024
}

func byteToGB(b uint64) float64 {
	return float64(b) / (1024 * 1024 * 1024)
}
