package execute

import (
	"bytes"
	"fmt"
	"io"
	"os/exec"
	"strings"

	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
)

type writerWithPrint struct {
	buf bytes.Buffer
}

func (w *writerWithPrint) Write(p []byte) (n int, err error) {
	logging.Print(string(p))
	return w.buf.Write(p)
}

func (w *writerWithPrint) String() string {
	return w.buf.String()
}

func Run(path, command string, cmdArgs ...string) (error, string) {
	outWriter := &writerWithPrint{}
	errWriter := &writerWithPrint{}

	isVerbose := logging.GetVerbose()
	if isVerbose {
		if command == "docker-compose" {
			cmdArgs = append([]string{"--verbose"}, cmdArgs...)
		}
	}

	logging.Print(fmt.Sprint(command, " ", strings.Join(cmdArgs, " ")))
	cmd := exec.Command("bash", "-c", command+" "+strings.Join(cmdArgs, " "))
	cmd.Stdout = io.MultiWriter(outWriter)
	cmd.Stderr = io.MultiWriter(errWriter)

	if path != "" {
		cmd.Dir = path
	}

	if err := cmd.Start(); err != nil {
		logging.Fatal(err)
	}

	err := cmd.Wait()
	output := outWriter.String() + errWriter.String()

	return err, output
}
