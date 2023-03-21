package execute

import (
	"bytes"
	"fmt"
	"github.com/CanastaWiki/Canasta-CLI-Go/internal/logging"
	"io"
	"os/exec"
	"strings"
	"sync"
)

type writerWithPrint struct {
	buf bytes.Buffer
	mu  sync.Mutex
}

func (w *writerWithPrint) Write(p []byte) (n int, err error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	logging.Print(string(p))
	return w.buf.Write(p)
}

func (w *writerWithPrint) String() string {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.buf.String()
}

func Run(path, command string, cmdArgs ...string) (error, string) {
	outWriter := &writerWithPrint{}
	errWriter := &writerWithPrint{}

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
