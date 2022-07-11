package execute

import (
	"bytes"
	"fmt"
	"os/exec"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

func Run(path, command string, cmdArgs ...string) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	logging.Print(fmt.Sprint(command, " ", strings.Join(cmdArgs, " ")))
	cmd := exec.Command(command, cmdArgs[:]...)
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if path != "" {
		cmd.Dir = path
	}

	err := cmd.Start()
	if err != nil {
		logging.Fatal(err)
	}
	if err := cmd.Wait(); err != nil {
		logging.Fatal(fmt.Errorf(stdout.String() + stderr.String()))
	}
	logging.Print(stdout.String() + stderr.String())
}
