#!/bin/bash

# Canasta CLI installer

# This script downloads and installs the Canasta command-line interface (CLI), which is
# an executable called "canasta".
# It also checks for the presence of Git, Docker and Docker Compose, and displays a
# warning to the user if they are not correctly installed, although it installs the CLI
# regardless.

echo "Downloading Canasta CLI latest release..."
canastaURL="https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta"
# The show-progress param was added to wget in version 1.16 (October 2014).
wgetOptions=$(wget --help)
if [[ $wgetOptions == *"show-progress"* ]]
then
  wget -q --show-progress $canastaURL
else
  wget -q $canastaURL
fi

echo "Download was successful; now installing Canasta CLI."
chmod u=rwx,g=xr,o=x canasta
sudo mv canasta /usr/local/bin/canasta

# Check if Git is installed; exit if not.
git --version 2>&1 >/dev/null
GIT_IS_AVAILABLE=$?
if [ $GIT_IS_AVAILABLE -ne 0 ]; 
then echo "Git was not found, please install it before continuing.";
     exit; 
else
     echo "Checking for presence of Git... found."
fi

loc=$(which docker)
if [ -z $loc ]
then
    echo "Docker is not installed; please follow the guide at https://docs.docker.com/engine/install/ to install it."
elif [ -x $loc ]
then
    echo "Checking for presence of Docker... found."
else
    echo "Docker appears to be installed at $loc but is not executable; please check permissions."
fi

if ! docker compose  version;
then
    echo "Docker Compose is not installed; please follow the guide at https://docs.docker.com/compose/install/ to install it.
    Alternatively, set the Compose executable path in canasta CLI using \"canasta -d PATH\""
else
    echo "Checking for presence of Docker Compose... found."
fi

echo "Please make sure you have a working kubectl if you wish to use Kubernetes as an orchestrator."
echo -e "\nUsage: sudo canasta [COMMAND] [ARGUMENTS...]"