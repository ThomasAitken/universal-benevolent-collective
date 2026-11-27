#!/bin/sh
# Run as the guest administrator with a locally transferred Git bundle.
set -eu

if [ "$(id -u)" -ne 0 ] || [ "$#" -ne 2 ]; then
    echo "Usage: sudo sh provision-linux.sh CHECKOUT_BUNDLE COMMIT" >&2
    exit 2
fi
bundle=$1
revision=$2
case "$revision" in
    ''|*[!0-9a-f]*) echo "Expected a full Git commit ID." >&2; exit 2 ;;
esac
case "${#revision}" in
    40|64) ;;
    *) echo "Expected a full Git commit ID." >&2; exit 2 ;;
esac

. /etc/os-release
if [ "$ID" != debian ] || [ "$VERSION_ID" != 13 ] ||
    [ "$(dpkg --print-architecture)" != arm64 ] ||
    [ ! -f /sys/fs/cgroup/cgroup.controllers ]; then
    echo "Provisioning requires Debian 13 ARM64 with cgroup v2." >&2
    exit 1
fi
systemctl show --property=Version --value

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=120 update
apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \
    aardvark-dns ca-certificates crun curl dbus-user-session fuse-overlayfs git \
    netavark nftables podman python3 uidmap

if ! getent group ubc-node >/dev/null; then
    groupadd --system ubc-node
fi
if ! id ubc-node >/dev/null 2>&1; then
    useradd --create-home --gid ubc-node --shell /bin/bash ubc-node
else
    usermod --append --groups ubc-node ubc-node
fi

downloads=$(mktemp -d)
trap 'rm -rf "$downloads"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

install_tool() {
    binary=$1
    version=$2
    url=$3
    checksum=$4
    member=$5
    target=/usr/local/bin/$binary

    if [ -x "$target" ] && "$target" --version | grep -Eq "(^|[^0-9.])$version([^0-9.]|$)"; then
        return
    fi
    curl --fail --silent --show-error --location --connect-timeout 15 --retry 3 \
        "$url" -o "$downloads/tool.tar.gz"
    printf '%s  %s\n' "$checksum" "$downloads/tool.tar.gz" | sha256sum --check
    tar -xzf "$downloads/tool.tar.gz" -C "$downloads" "$member"
    install -m 0755 "$downloads/$member" "$target"
}

install_tool uv '0\.9\.28' \
    https://github.com/astral-sh/uv/releases/download/0.9.28/uv-aarch64-unknown-linux-gnu.tar.gz \
    382c342735ff29f8ba4574d88e39bca798bcbac50bff6742710ca9cd8143e7d2 \
    uv-aarch64-unknown-linux-gnu/uv
install_tool task '3\.54\.0' \
    https://github.com/go-task/task/releases/download/v3.54.0/task_linux_arm64.tar.gz \
    d7189d439d4a6058e39e1b2acd7c8b27acd4856691b408f23b242e4294e1df5b \
    task

# Read the operator-owned bundle through stdin and write the checkout as the node.
runuser -u ubc-node -- sh -eu -c '
    revision=$1
    checkouts=$HOME/checkouts
    checkout=$checkouts/$revision
    mkdir -p "$checkouts"
    exec 3>"$checkouts/.provision.lock"
    flock 3

    if [ ! -d "$checkout" ]; then
        staging=$(mktemp -d "$checkouts/.checkout.XXXXXXXX")
        trap '\''rm -rf "$staging"'\'' EXIT
        cat > "$staging/checkout.bundle"
        git clone --quiet --no-checkout "$staging/checkout.bundle" "$staging/tree"
        git -C "$staging/tree" checkout --quiet --detach "$revision"
        git -C "$staging/tree" remote remove origin
        mv -T "$staging/tree" "$checkout"
    fi

    cd "$checkout"
    current_revision=$(git rev-parse HEAD)
    changes=$(git status --porcelain)
    if [ "$current_revision" != "$revision" ] || [ -n "$changes" ]; then
        echo "Guest checkout has local changes: $checkout" >&2
        exit 1
    fi
    task sync
    uv run --locked ubc doctor

    if [ -e "$HOME/checkout" ] && [ ! -L "$HOME/checkout" ]; then
        echo "Guest checkout link would replace an existing file: $HOME/checkout" >&2
        exit 1
    fi
    ln -sfnT "$checkout" "$HOME/checkout"
    printf "Provisioned %s in %s\n" "$revision" "$checkout"
' sh "$revision" < "$bundle"
