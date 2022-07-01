# Canasta-CLI-Go
The Canasta command line interface, written in Go

# Installation
Make sure you have `go` installed and set your `$GOPATH`

Clone the repo

`git clone git@github.com:CanastaWiki/Canasta-CLI-Go.git`

Checkout into the `dev` branch

`git checkout dev`

Install Canasta.go

`go install canasta.go`

Now you should be able to access the `canasta` cli from any directories

# Documentation

``` A CLI tool to create, import, start, stop and backup multiple Canasta installations

Usage:
  canasta [command]

Available Commands:

  completion  Generate the autocompletion script for the specified shell

  create      Create a Canasta Installation
  
  delete      delete a  Canasta installation
  
  help        Help about any command
  
  import      Create a Canasta Installation
  
  list        list all  Canasta installations
  
  start       Start the Canasta installation
  
  stop        Stop the Canasta installation

Flags:
  -h, --help   help for canasta

Use "canasta [command] --help" for more information about a command.
```