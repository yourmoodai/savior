#!/bin/sh
set -eu

release_architecture=$(uname -m)
case "$release_architecture" in
    aarch64|x86_64) ;;
    *) exit 1 ;;
esac
release_lock="/opt/release-tools/packages-$release_architecture.lock"
test -s "$release_lock"
release_packages=$(mktemp -d)
while read -r checksum url; do
    case "$url" in
        https://dl-cdn.alpinelinux.org/alpine/v3.24/*/*.apk) ;;
        *) exit 1 ;;
    esac
    destination="$release_packages/${url##*/}"
    wget -q -O "$destination" "$url"
    printf '%s  %s\n' "$checksum" "$destination" | sha256sum -c -
done < "$release_lock"
# APK also verifies Alpine signatures before installing the complete pinned closure.
apk add --no-cache --no-network --upgrade "$release_packages"/*.apk
rm -rf "$release_packages"
