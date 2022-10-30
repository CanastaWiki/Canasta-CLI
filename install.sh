#!/usr/bin/env bash

# Canasta CLI installer
# Requirements Docker Engine 18.06.0+ and DockerCompose 

usage() {
  cat << EOF >&2
Usage: $PROGNAME [-i] [-v <ver>]

-v <ver>: install specific version
      -i: interactive version
       *: usage
EOF
  exit 1
}

while getopts v:i opt; do
  case ${opt} in
  i)
    echo "interactive mode"
    INTERACTIVE=1
    ;;
  v)
    VERSION=$OPTARG
    ;;
  *)
    usage
    ;;
  esac
done

if [[ -n ${INTERACTIVE-} ]]; then
  echo "-----"
  echo "Checking the Version..."
  echo "Which version do you want to install?"
  echo "   1) Default: latest"
  echo "   2) Custom"
  until [[ $VERSION_CHOICE =~ ^[1-2]$ ]]; do
  	read -rp "Version choice [1-2]: " -e -i 1 VERSION_CHOICE
  done
  case $VERSION_CHOICE in
  1)
    VERSION="latest"
  	;;
  2)
    until [[ $VERSION =~ ^(([0-9]{1,3}[\.]){2}[0-9]{1,3}).* ]]; do
      read -rp "Version (e.g. 1.2.0): " -e VERSION
  	done
  	;;
  esac
fi

if [[ -z ${VERSION-} ]]; then
  VERSION="latest"
  echo "No version have been specified, latest will be used."
fi

if [[ ${VERSION} == "latest" ]]; then
  canastaURL="https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta"
else
  canastaURL="https://github.com/CanastaWiki/Canasta-CLI/releases/download/v${VERSION}/canasta"
fi

echo "Downloading Canasta CLI ${VERSION} release"
# The show-progress param was added to wget in version 1.16 (October 2014).
wgetOptions=$(wget --help)
if [[ $wgetOptions == *"show-progress"* ]]
then
  wget -q --show-progress $canastaURL
else
  wget -q $canastaURL
fi
if [ $? -ne 0 ]; then
  echo "The version you have specified is not a valid version of the Canasta CLI."
  exit 1
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
