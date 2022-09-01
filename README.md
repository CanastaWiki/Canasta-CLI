# Canasta CLI
The Canasta command line interface, written in Go.

## Installation

First, make sure you have Docker and Docker Compose installed. Then, run the following line to install the Canasta CLI:

```
curl -fsL https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/installer/install.sh | bash
```

## Documentation

``` A CLI tool to create, import, start, stop and backup multiple Canasta installations

Usage:
  sudo canasta [command]

Available commands:

  completion  Generate the autocompletion script for the specified shell

  create      Create a Canasta installation
  
  delete      Delete a Canasta installation
  
  help        Help about any command
  
  import      Create a Canasta installation
  
  list        List all Canasta installations
  
  start       Start a Canasta installation
  
  stop        Stop a Canasta installation

Flags:
  -h, --help   help for canasta

Use "canasta [command] --help" for more information about a command.
```

## Example

create

` sudo canasta create -w "My Wiki" -n wiki.my.com -i mywiki -a admin -o docker-compose `

This command will create a Canasta installation with a wiki named ` My Wiki `, hosted at domain ` wiki.my.com `, with the admin name ` admin `, using the orchestrator ` docker-compose `. This also sets a unique ID, `mywiki`, for the installation, which can then be used to start, stop, and delete that instance from the command line.
