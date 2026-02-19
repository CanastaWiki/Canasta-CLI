package spinner

import (
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/schollz/progressbar/v3"
)

// New creates a spinner that displays progress with the following structure:
// ⠏ Description...
// It returns a stop function. Call stop() to finish the spinner, clear the line,
// and print a newline so subsequent output appears on its own line.
// The stop function is idempotent and safe to call multiple times.
func New(description string) func() {
	// Create a spinner to show progress and time taken, to avoid the illusion of the CLI being hung.
	s := progressbar.NewOptions(-1,
		progressbar.OptionEnableColorCodes(true),
		progressbar.OptionSetWriter(os.Stdout),
		progressbar.OptionSetWidth(10),
		progressbar.OptionSetDescription(description),
		progressbar.OptionThrottle(65*time.Millisecond),
		progressbar.OptionSpinnerType(14),
		progressbar.OptionFullWidth(),
		progressbar.OptionSetRenderBlankState(true),
		progressbar.OptionOnCompletion(func() {
			fmt.Fprint(os.Stdout, "\n")
		}),
	)

	// done signals the goroutine to stop. finished is closed after cleanup completes.
	done := make(chan struct{})
	finished := make(chan struct{})

	go func() {
		defer close(finished)
		for {
			select {
			case <-done:
				_ = s.Finish()
				return
			default:
				_ = s.Add(1)
				time.Sleep(5 * time.Millisecond)
			}
		}
	}()

	var once sync.Once
	return func() {
		once.Do(func() {
			done <- struct{}{}
			<-finished
		})
	}
}
