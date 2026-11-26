#!/bin/sh
# Run as root on a disposable Linux acceptance host with the base image loaded.
set -eu

base_image=${1:?provide the loaded C03 browser image reference}
fixture_image=localhost/ubc-browser:c03-fixture
network=ubc-egress-fixture
work=$(mktemp -d /run/ubc-c03-fixture.XXXXXX)
trap 'rm -rf "$work"' EXIT
mkdir "$work/build"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=approved.ubc.test \
    -addext subjectAltName=DNS:approved.ubc.test \
    -keyout "$work/fixture.key" -out "$work/fixture.crt" >/dev/null 2>&1
spki=$(openssl x509 -in "$work/fixture.crt" -pubkey -noout \
    | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | openssl base64 -A)
test "${#spki}" = 44
cat > "$work/build/Containerfile" <<EOF
FROM $base_image
COPY --chmod=755 chromium-fixture /usr/local/bin/chromium-fixture
ENV CHROME_PATH=/usr/local/bin/chromium-fixture
EOF
cat > "$work/build/chromium-fixture" <<EOF
#!/bin/sh
exec /usr/bin/chromium --ignore-certificate-errors-spki-list=$spki "\$@"
EOF

podman --cgroup-manager=cgroupfs build --network=none --pull=never \
    --tag "$fixture_image" "$work/build" >&2
podman --cgroup-manager=cgroupfs network create --internal --subnet 10.89.97.0/24 "$network" >&2
podman --cgroup-manager=cgroupfs run --detach --pull=never --read-only \
    --name ubc-approved-fixture --network "$network" --network-alias approved.ubc.test \
    --ip 10.89.97.2 --user 0 --entrypoint python \
    --volume "$work:/fixture:ro" "$base_image" -c "$(cat tests/egress_fixture.py)" approved >&2
podman --cgroup-manager=cgroupfs run --detach --pull=never --read-only \
    --name ubc-denied-fixture --network "$network" --network-alias denied.ubc.test \
    --ip 10.89.97.3 --user 0 --entrypoint python \
    "$base_image" -c "$(cat tests/egress_fixture.py)" denied >&2

for name in ubc-approved-fixture ubc-denied-fixture; do
    attempts=0
    until [ "$(podman --cgroup-manager=cgroupfs logs "$name" 2>&1)" = ready ]; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 50 ]; then
            podman --cgroup-manager=cgroupfs logs "$name" >&2
            exit 1
        fi
        sleep 0.1
    done
done

install -d -m 0755 /etc/ubc-browser
install -m 0600 /dev/stdin /etc/ubc-browser/egress.json <<'EOF'
{
  "http://approved.ubc.test:18765": ["10.89.97.2/32"],
  "https://approved.ubc.test:18443": ["10.89.97.2/32"]
}
EOF
digest=$(podman --cgroup-manager=cgroupfs image inspect "$fixture_image" --format '{{.Digest}}')
printf 'localhost/ubc-browser@%s\n' "$digest"
