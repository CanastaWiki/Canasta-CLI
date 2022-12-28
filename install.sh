#!/usr/bin/env bash
unset choice canastaURL
die() { echo "$*" >&2; exit 2; }  # complain to STDERR and exit with error
needs_arg() { if [ -z "$OPTARG" ]; then die "No arg for --$OPT option"; fi; }

git --version >/dev/null 2>&1
GIT_IS_AVAILABLE=$?
if [ $GIT_IS_AVAILABLE -ne 0 ]; 
then echo "Git was not found, please install before continuing.";
     exit; 
else
     echo "Git was found on the system"
fi

loc=$(command -v docker)
if [ -z "$loc" ]
then
    echo "Docker is not installed; please follow the guide at https://docs.docker.com/engine/install/ to install it."
elif [ -x "$loc" ]
then
    echo "Docker is already installed."
else
    echo "Docker appears to be installed at $loc but is not executable; please check permissions."
fi

loc=$(command -v docker-compose)
if [ -z "$loc" ]
then
    echo "Docker Compose is not installed; please follow the guide at https://docs.docker.com/compose/install/ to install it."
elif [ -x "$loc" ]
then
    echo "Docker Compose is already installed."
else
    echo "Docker Compose appears to be installed at $loc but is not executable; please check permissions."
fi


github_api="https://api.github.com"

repo="repos/CanastaWiki/Canasta-CLI/git/refs/tags"

data=$(curl ${github_api}/${repo} 2>/dev/null)

refs=$(jq -r '.. | select(.ref?) | .ref' <<< "${data}")
mapfile -t versions < <(cut -d '/' -f 3 <<< "${refs}" | sort -h | tac | head -n 5)

get_versions() {
	for index in "${!versions[@]}"; do
	  echo "  $((index))) ${versions[$index]}"
	done
}

query_version() {
	read -r -p "Pick a version (index): " choice # Read stdin and save the value on the $choice var
	echo "${choice}"
}
download_package() {
	version=${versions[${1}]}
	if [[ -n ${version} ]]; then # Verify if the version with that index exists
		canastaURL="https://github.com/CanastaWiki/Canasta-CLI/releases/download/${version}/canasta"
		wgetOptions=$(wget --help)
		if [[ $wgetOptions == *"show-progress"* ]]
		then
		    wget -q --show-progress "$canastaURL"
		else
		    wget -q "$canastaURL"
		fi
		echo "Installing ${version:-latest} Canasta CLI"
		chmod u=rwx,g=xr,o=x canasta
		sudo mv canasta /usr/local/bin/canasta
	else
		echo "Invalid version"
	fi
}

while true; do
	case "${1}" in
		-l|--list-versions)
			get_versions
			break
			;;
		-i|--install)
			if [[ -n "${2}" ]]; then
				download_package "${2}"
				shift
			else
				get_versions
				download_package "$(query_version)"
			fi
		        break	
			;;
		-*)
			die "Illegal option ${1}"
			;;
		*) 		
			wget -q  --show-progress "https://github.com/CanastaWiki/Canasta-CLI/releases/latest/download/canasta"
			echo "Installing latest Canasta CLI"
	                chmod u=rwx,g=xr,o=x canasta
        	        sudo mv canasta /usr/local/bin/canasta
			break
			;;
  esac
done
