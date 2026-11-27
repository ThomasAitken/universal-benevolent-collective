#!/bin/sh
# Start a Lima guest and provision the current committed revision.
set -eu

if [ "$#" -gt 1 ]; then
    echo "Usage: sh vm/start-mac.sh [instance]" >&2
    exit 2
fi
instance=${1:-ubc-pilot}
case "$instance" in
    -*|*[!a-z0-9-]*) echo "Invalid Lima instance name: $instance" >&2; exit 2 ;;
esac

repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify HEAD)
transfer=$(mktemp -d)
guest_transfer=
cleanup() {
    rm -rf "$transfer"
    if [ -n "$guest_transfer" ]; then
        limactl shell --workdir /tmp "$instance" -- rm -rf "$guest_transfer" >/dev/null || :
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# A bundle preserves the commit without copying local edits or ignored files.
git -C "$repo" bundle create "$transfer/checkout.bundle" HEAD
if [ "$(limactl list --format '{{.Name}}' "$instance" 2>/dev/null)" = "$instance" ]; then
    limactl start --tty=false "$instance"
else
    limactl start --tty=false --name="$instance" "$repo/vm/lima.yaml"
fi

guest_transfer=$(limactl shell --workdir /tmp "$instance" -- mktemp -d /tmp/ubc-provision.XXXXXXXX)
limactl copy --backend=scp "$transfer/checkout.bundle" "$repo/vm/provision-linux.sh" "$instance:$guest_transfer/"
limactl shell --workdir /tmp "$instance" -- \
    sudo sh "$guest_transfer/provision-linux.sh" "$guest_transfer/checkout.bundle" "$revision"
