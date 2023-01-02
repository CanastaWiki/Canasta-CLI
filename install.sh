#!/usr/bin/env bash
unset choice canastaURL
die() { echo "$*" >&2; exit 2; }  # complain to STDERR and exit with error
needs_arg() { if [ -z "$OPTARG" ]; then die "No arg for --$OPT option"; fi; }

is_command_available() {
    for cmd in "$@"; do
        if [[ -z $(command -v ${cmd}) ]]; then
            echo ${cmd}
            return 1
        fi
    done
}

result=$(is_command_available git docker jq docker-compose)

if [[ $? -ne 0 ]]; then
    echo "${result} is not installed; exiting."
    exit 1
fi

github_api="https://api.github.com"

repo="repos/CanastaWiki/Canasta-CLI/git/refs/tags"

data=$(curl ${github_api}/${repo} 2>/dev/null)

refs=$(jq -r '.. | select(.ref?) | .ref' <<< "${data}")
mapfile -t versions < <(echo "latest";cut -d '/' -f 3 <<< "${refs}" | sort -h | tac | head -n 5)

get_versions() {
	echo "The following are the possible Canasta versions:"
	for index in "${!versions[@]}"; do
	  echo "  $((index))) ${versions[$index]}"
  	done
}

query_version() {
	read -r -p "Pick a version using index or by name: " choice # Read stdin and save the value on the $choice var
	echo "${choice}"
}
containsElement () {
	local e match="$1"
	shift
	for e; do [[ $e == "$match" ]] && return 0; done
	return 1
}
download_package() {
	if [[ "$1" =~ ^[0-9]$ ]] && [[ "$1" < ${#versions[@]} ]]; then
			version=${versions[$1]}
	elif containsElement "$1" "${versions[@]}"; then
			version=${1:-latest}
	elif [[ -z "$1" ]]; then
		version=latest
	else	
		echo "Invalid version."
		exit
	fi
	if [[ "$version" == latest ]]; then
		canastaURL="https://github.com/CanastaWiki/Canasta-CLI/releases/$version/download/canasta"
	else
		canastaURL="https://github.com/CanastaWiki/Canasta-CLI/releases/download/$version/canasta"
	fi

	wargs=(-q)
        if wget --help | grep -q -e --show-progress ; then
		wargs+=(--show-progress)
	fi
	echo "Downloading Canasta ${version}"
	wget "${wargs[@]}" "$canastaURL"
	echo "Installing ${version} Canasta CLI"
	chmod u=rwx,g=xr,o=x canasta
        mv canasta /usr/local/bin/canasta
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
			download_package
			break
			;;
  esac
done

