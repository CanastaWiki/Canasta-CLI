package prompt

import (
	"testing"
)

func TestPasswordCheck(t *testing.T) {
	tests := []struct {
		username string
		password string
		wantErr  bool
		errMsg   string
	}{
		{"user1", "short", true, "Password must be at least 10 characters long"},
		{"user1", "user1password", true, "Password should not be similar to the username"},
		{"password1234User", "password1234", true, "Password should not be similar to the username"},
		{"user1", "securepassword", false, ""},
	}

	for _, tt := range tests {
		err := passwordCheck(tt.username, tt.password)
		if (err != nil) != tt.wantErr {
			t.Errorf("passwordCheck(%q, %q) error = %v, wantErr %v", tt.username, tt.password, err, tt.wantErr)
		}
		if err != nil && err.Error() != tt.errMsg {
			t.Errorf("passwordCheck(%q, %q) error message = %v, want %v", tt.username, tt.password, err.Error(), tt.errMsg)
		}
	}
}