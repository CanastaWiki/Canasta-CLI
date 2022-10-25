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

loc=$(which docker)
if [ -z $loc ]
then
    echo "Docker is not installed; please follow the guide at https://docs.docker.com/engine/install/ to install it."
elif [ -x $loc ]
then
    echo "Docker is already installed."
else
    echo "Docker appears to be installed at $loc but is not executable; please check permissions."
fi

loc=$(which docker-compose)
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
