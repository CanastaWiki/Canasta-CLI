package orchestrators

import "testing"

func TestServiceFromContainer(t *testing.T) {
	tests := []struct {
		name          string
		containerName string
		project       string
		want          string
	}{
		{"standard dash", "myproject-web-1", "myproject", "web"},
		{"standard underscore", "myproject_web_1", "myproject", "web"},
		{"multi-part service", "myproject-my-service-1", "myproject", "my-service"},
		{"numeric suffix", "wiki-db-2", "wiki", "db"},
		{"no match", "other-web-1", "myproject", "other-web-1"},
		{"legacy underscore service", "proj_my_service_1", "proj", "my_service"},
		{"project with hyphen", "my-project-web-1", "my-project", "web"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := serviceFromContainer(tt.containerName, tt.project)
			if got != tt.want {
				t.Errorf("serviceFromContainer(%q, %q) = %q, want %q",
					tt.containerName, tt.project, got, tt.want)
			}
		})
	}
}
