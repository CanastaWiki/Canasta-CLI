package spinner

import (
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/schollz/progressbar/v3"
)

// mu protects both the spinner's rendering (Add/Finish) and Print's
// clear-and-write, so intermediate messages never collide with the spinner.
var mu sync.Mutex

// active is true while a spinner is running.
var active bool

// New creates a spinner that displays progress with the following structure:
// ⠏ Description...
// It returns a stop function. Call stop() to finish the spinner, clear the line,
// and print a newline so subsequent output appears on its own line.
// The stop function is idempotent and safe to call multiple times.
func New(description string) func() {
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

	done := make(chan struct{})
	finished := make(chan struct{})

	mu.Lock()
	active = true
	mu.Unlock()

	go func() {
		defer close(finished)
		for {
			select {
			case <-done:
				mu.Lock()
				_ = s.Finish()
				mu.Unlock()
				return
			default:
				mu.Lock()
				_ = s.Add(1)
				mu.Unlock()
				time.Sleep(5 * time.Millisecond)
			}
		}
	}()

	var once sync.Once
	return func() {
		once.Do(func() {
			done <- struct{}{}
			<-finished
			mu.Lock()
			active = false
			mu.Unlock()
		})
	}
}

// Print prints a message to stdout. If a spinner is active, it clears the
// spinner line first so the message appears on its own line, then lets the
// spinner continue rendering on the next line.
// When no spinner is active, it behaves like fmt.Fprint(os.Stdout, msg).
func Print(msg string) {
	mu.Lock()
	defer mu.Unlock()
	if active {
		// Clear the spinner line: move cursor to column 0 and erase to end of line.
		fmt.Fprint(os.Stdout, "\r\033[K")
	}
	fmt.Fprint(os.Stdout, msg)
}
