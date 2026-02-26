package permissions

import "io/fs"

const (
	// DirectoryPermission is the default permission mode for directories created by the CLI.
	DirectoryPermission fs.FileMode = 0755
	// FilePermission is the default permission mode for files created by the CLI.
	FilePermission fs.FileMode = 0644
)
