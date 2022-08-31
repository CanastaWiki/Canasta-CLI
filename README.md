# Canasta-CLI-Go
The Canasta command line interface, written in Go

# Installation

First, ake sure you have Docker and Docker Compose installed. Then, run the following line to install the Canasta CLI:

```
curl -fsL https://raw.githubusercontent.com/CanastaWiki/Canasta-CLI/installer/install.sh | bash
```

# Documentation

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

# Example

create

` sudo canasta create -w "My Wiki" -n wiki.my.com -i mywiki -a admin -o docker-compose `

This command will create a canasta installation with a Wiki named ` My Wiki `, hosted at domain ` wiki.my.com `, admin name as ` admin `, using the orchestrator ` docker-compose `. This also sets a unique ID `mywiki` to the Installation. Which can be used to start, stop, delete instances without moving into the installation folder.
