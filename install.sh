#!/bin/bash

# Canasta CLI installer
# Requirements Docker Engine 18.06.0+ and DockerCompose 

echo "Downloading Canasta CLI latest release"
canastaURL="https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta"
# The show-progress param was added to wget in version 1.16 (October 2014).
wgetOptions=$(wget --help)
if [[ $wgetOptions == *"show-progress"* ]]
then
  wget -q --show-progress $canastaURL
else
  wget -q $canastaURL
fi

echo "Installing Canasta CLI"
chmod u=rwx,g=xr,o=x canasta
sudo mv canasta /usr/local/bin/canasta

# Testing git if installed else tell to install it
git --version 2>&1 >/dev/null

GIT_IS_AVAILABLE=$?

if [ $GIT_IS_AVAILABLE -ne 0 ]; 
then echo "Git was not found, please install before continuing.";
     exit; 
else
     echo "Git was found on the system"
fi

loc=$(command -v docker)
if [ -z $loc ]
then
    echo "Docker is not installed; please follow the guide at https://docs.docker.com/engine/install/ to install it."
elif [ -x $loc ]
then
    echo "Docker is already installed."
else
    echo "Docker appears to be installed at $loc but is not executable; please check permissions."
fi

loc=$(command -v docker-compose)
if [ -z $loc ]
then
    echo "Docker Compose is not installed; please follow the guide at https://docs.docker.com/compose/install/ to install it."
elif [ -x $loc ]
then
    echo "Docker Compose is already installed."
else
    echo "Docker Compose appears to be installed at $loc but is not executable; please check permissions."
fi

echo "Please make sure you have a working kubectl if you wish to use Kubernetes as an orchestrator."
echo -e "\nUsage: sudo canasta [COMMAND] [ARGUMENTS...]"
