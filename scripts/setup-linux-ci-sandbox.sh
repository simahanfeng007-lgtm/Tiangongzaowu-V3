#!/usr/bin/env bash
# Prepare only the disposable GitHub-hosted Linux runner, never a developer host.
set -euo pipefail
if [[ ${GITHUB_ACTIONS:-} != true || ${RUNNER_OS:-} != Linux || ${RUNNER_ENVIRONMENT:-} != github-hosted ]]; then
    echo 'This setup is restricted to GitHub-hosted Linux CI.' >&2
    exit 2
fi

sudo apt-get update
sudo apt-get install -y bubblewrap ffmpeg apparmor

probe() {
    bwrap --die-with-parent --unshare-user --unshare-net --ro-bind / / /bin/true
}

if ! probe; then
    restriction=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
    if [[ ! -r $restriction || $(cat "$restriction") != 1 ]]; then
        echo 'Containment failed without the Ubuntu AppArmor user namespace restriction.' >&2
        exit 1
    fi
    # Ubuntu 24.04 allows named unconfined profiles to grant userns to sandbox
    # builders. Do not disable AppArmor or the system-wide userns restriction.
    # https://discourse.ubuntu.com/t/ubuntu-24-04-lts-noble-numbat-release-notes/39890
    if [[ -f /etc/apparmor.d/bwrap ]]; then
        sudo apparmor_parser -r /etc/apparmor.d/bwrap
    else
        sudo tee /etc/apparmor.d/tiangong-ci-bwrap >/dev/null <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
}
PROFILE
        sudo apparmor_parser -r /etc/apparmor.d/tiangong-ci-bwrap
    fi
    [[ $(cat "$restriction") == 1 ]]
    probe
fi

# Check the intended namespaces and read-only host mount, beyond a zero exit.
probe_file=$(mktemp)
trap 'rm -f "$probe_file"' EXIT
bwrap --die-with-parent --unshare-user --unshare-net --ro-bind / / --proc /proc \
    /usr/bin/python3 - "$probe_file" "$(readlink /proc/self/ns/user)" "$(readlink /proc/self/ns/net)" <<'PY'
import errno, os, socket, sys
assert os.readlink('/proc/self/ns/user') != sys.argv[2]
assert os.readlink('/proc/self/ns/net') != sys.argv[3]
assert {name for _, name in socket.if_nameindex()} == {'lo'}
try:
    with open(sys.argv[1], 'wb') as output:
        output.write(b'host write must be denied')
except OSError as exc:
    assert exc.errno == errno.EROFS, exc
else:
    raise AssertionError('host mount was writable')
print('Containment verified: separate user/network namespaces, read-only host mount.')
PY
