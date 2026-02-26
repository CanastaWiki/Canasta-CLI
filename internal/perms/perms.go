package perms

import "io/fs"

const (
	// DirPerm is the default permission mode for directories created by the CLI.
	DirPerm fs.FileMode = 0755
	// FilePerm is the default permission mode for files created by the CLI.
	FilePerm fs.FileMode = 0644
)
