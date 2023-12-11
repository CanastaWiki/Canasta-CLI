#!/bin/bash

# Canasta CLI installer

# This script downloads and installs the Canasta command-line interface (CLI), which is
# an executable called "canasta".

# First it checks for the prerequisites of Git, Docker and Docker Compose, displaying a
# warning to the user if they are not correctly installed; exiting the installer.

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

# Canasta only supports Compose V2
# Since July 2023 Compose V1 stopped receiving updates. It is also no longer available in new releases of Docker Desktop.
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose V2 is not installed"
  echo "Please follow the guide at https://docs.docker.com/compose/install/ to install it."
  echo "It is recommended to use the official Docker repositories."
  echo "Alternatively, set the Compose executable path in canasta CLI using \"canasta -d PATH\" but note that Compose V1 is NOT supported."
  exit 1
else
    echo "Checking for presence of Docker Compose V2... found."
fi

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

echo "Please make sure you have a working kubectl if you wish to use Kubernetes as an orchestrator."
echo -e "\nUsage: sudo canasta [COMMAND] [ARGUMENTS...]"