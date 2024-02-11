package spinner

import (
	"fmt"
	"os"
	"time"

	"github.com/schollz/progressbar/v3"
)

// New returns a new spinner with the following structure.
// ⠏ Description...
// The spinner will keep spinning until a message is passed to the done channel.
func New(description string) (*progressbar.ProgressBar, chan struct{}) {
	// Create a spinner to show progress and time taken, to avoid the illusion of the CLI being hung.
	spinner := progressbar.NewOptions(-1,
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

	// done channel tracks whether the process using the spinner completed. It doesn't care about success or failure,
	// just the completion.
	done := make(chan struct{})

	// Create a goroutine to actively listen on the done channel, and update the spinner while the execution
	// of the process is ongoing.
	go func() {
		for {
			select {
			case <-done:
				return
			default:
				spinner.Add(1)
				time.Sleep(5 * time.Millisecond)
			}
		}
	}()

	return spinner, done
}
