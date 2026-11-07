#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
SOURCE=$(cd -- "$SCRIPT_DIR/.." && pwd)
HTTP_PORT=30127
HTTP_LISTEN=0.0.0.0
PORT_SET=0
LISTEN_SET=0
SKIP_APT=0
CHECK_ONLY=0

usage() {
    cat <<'USAGE'
Usage: sudo bash deploy/install.sh [options]
  --port PORT       LAN HTTP port (default: 30127; preserved on upgrades)
  --listen IPV4     Bind address (default: 0.0.0.0; preserved on upgrades)
  --skip-apt        Do not install OS packages (they must already exist)
  --check           Check this device only; do not change anything
  --help            Show this help
Target: Ubuntu 24.04 LTS arm64, systemd, Python 3.12. Single-service deployment.
USAGE
}
while (($#)); do
    case "$1" in
        --port) (($#>=2)) || fail 'Missing --port value'; HTTP_PORT=$2; PORT_SET=1; shift 2 ;;
        --listen) (($#>=2)) || fail 'Missing --listen value'; HTTP_LISTEN=$2; LISTEN_SET=1; shift 2 ;;
        --skip-apt) SKIP_APT=1; shift ;;
        --check) CHECK_ONLY=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done
[[ $HTTP_PORT =~ ^[0-9]+$ && ${#HTTP_PORT} -le 5 ]] || fail 'Invalid port'
((10#$HTTP_PORT>=1024 && 10#$HTTP_PORT<=65535)) || fail 'Use port 1024..65535'
[[ -r /etc/os-release ]] || fail 'This installer requires Ubuntu 24.04'
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || fail "Expected Ubuntu 24.04; found ${PRETTY_NAME:-unknown}"
[[ $(uname -m) == aarch64 && $(getconf LONG_BIT) == 64 ]] || fail 'A 64-bit ARM userspace/kernel is required'
[[ $(dpkg --print-architecture) == arm64 ]] || fail 'The OS package architecture must be arm64'
[[ -d /run/systemd/system ]] || fail 'systemd must be running (this is not a container installer)'
MEMORY_MB=$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)
((MEMORY_MB>=1500)) || fail 'At least 1.5 GiB available physical RAM is required; 2 GiB+ is recommended'
FREE_MB=$(df -Pm /opt | awk 'NR==2 {print $4}')
((FREE_MB>=1536)) || fail 'At least 1.5 GiB free disk space is needed for a new isolated release'
printf 'Device: %s | arm64 | %s MiB RAM | %s MiB free disk\n' "$(hostname)" "$MEMORY_MB" "$FREE_MB"
if ((CHECK_ONLY)); then
    for command in python3.12 systemctl curl ss; do command -v "$command" || true; done
    printf '%s\n' 'Preflight complete. No files or services were changed.'
    exit 0
fi
need_root
[[ -f $SOURCE/backend/main.py && -f $SOURCE/backend/assets/default-avatar.jpg && -f $SOURCE/frontend/image-viewer.js ]] || fail 'Incomplete source package'

# Only this application owns these paths. Do not replace unrelated files.
for link in "$APP_ROOT/current" "$APP_ROOT/previous"; do
    [[ ! -e $link || -L $link ]] || fail "Refusing to replace a non-symlink: $link"
done
if [[ -f $APP_ENV ]]; then
    ((PORT_SET)) || HTTP_PORT=$(sed -n 's/^AVATAR_MASK_HTTP_PORT=//p' "$APP_ENV")
    ((LISTEN_SET)) || HTTP_LISTEN=$(sed -n 's/^AVATAR_MASK_HTTP_LISTEN=//p' "$APP_ENV")
fi

if ((!SKIP_APT)); then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends python3.12 python3.12-venv curl ca-certificates iproute2
fi
for command in python3.12 systemctl curl ss flock; do command -v "$command" >/dev/null || fail "Missing command: $command"; done
exec 9>/run/lock/wechat-avatar-mask-deploy.lock
flock -n 9 || fail 'Another install/rollback is running'
python3.12 -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 is required"'
python3.12 "$SCRIPT_DIR/configure.py" verify "$SOURCE"

if [[ -n $(ss -H -ltn "sport = :$HTTP_PORT") ]]; then
    old_port=''
    if [[ -f $APP_ENV ]]; then old_port=$(sed -n 's/^AVATAR_MASK_HTTP_PORT=//p' "$APP_ENV"); fi
    if ! systemctl is-active --quiet "$APP_UNIT" || [[ $old_port != "$HTTP_PORT" ]]; then fail "HTTP port $HTTP_PORT is already occupied"; fi
fi
FREE_MB=$(df -Pm /opt | awk 'NR==2 {print $4}')
((FREE_MB>=1536)) || fail 'At least 1.5 GiB free disk space is needed for a new isolated release'

install -d -m 755 "$APP_ROOT/releases"
RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
RELEASE="$APP_ROOT/releases/$RELEASE_ID"
mkdir -m 755 -- "$RELEASE"
install -d -m 755 "$RELEASE/backend/assets" "$RELEASE/frontend" "$RELEASE/deploy"
for file in "$SOURCE"/backend/*.py; do install -m 644 "$file" "$RELEASE/backend/"; done
install -m 644 "$SOURCE/backend/assets/default-avatar.jpg" "$RELEASE/backend/assets/"
for name in index.html style.css app.js image-viewer.js; do install -m 644 "$SOURCE/frontend/$name" "$RELEASE/frontend/"; done
for file in "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/requirements-arm64.txt; do install -m 644 "$file" "$RELEASE/deploy/"; done
python3.12 "$SCRIPT_DIR/configure.py" render --listen "$HTTP_LISTEN" --port "$HTTP_PORT" --memory-mb "$MEMORY_MB" --output "$RELEASE/.rendered"
python3.12 -m venv "$RELEASE/.venv"
# Never silently compile NumPy/OpenCV on the board or install into system Python.
"$RELEASE/.venv/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: -r "$RELEASE/deploy/requirements-arm64.txt"
"$RELEASE/.venv/bin/python" -m pip check
(cd -- "$RELEASE" && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import cv2,numpy,PIL; from backend.main import app,DEFAULT_AVATAR; assert DEFAULT_AVATAR.is_file(); print("Runtime imports OK; OpenCV",cv2.__version__)')
"$RELEASE/.venv/bin/python" -m pip freeze > "$RELEASE/installed-requirements.txt"
printf '%s\n' "$RELEASE_ID" > "$RELEASE/RELEASE_ID"
chmod -R a+rX "$RELEASE"

getent group "$APP_USER" >/dev/null || groupadd --system "$APP_USER"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --gid "$APP_USER" --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$APP_USER"
OLD_RELEASE=''
if [[ -L $APP_ROOT/current ]]; then OLD_RELEASE=$(release_path "$APP_ROOT/current") || fail 'Current release is invalid'; fi
BACKUP="/var/lib/wechat-avatar-mask-deploy/$RELEASE_ID"
install -d -m 700 "$BACKUP"
for pair in "env:$APP_ENV" "unit:$UNIT_FILE" "resource:$RESOURCE_FILE"; do
    name=${pair%%:*}; path=${pair#*:}; if [[ -f $path ]]; then cp -p -- "$path" "$BACKUP/$name"; fi
done
API_ENABLED_BEFORE=0
if systemctl is-enabled --quiet "$APP_UNIT"; then API_ENABLED_BEFORE=1; fi
ACTIVATING=0

restore_file() {
    local name=$1 destination=$2
    if [[ -f $BACKUP/$name ]]; then cp -p -- "$BACKUP/$name" "$destination"; else rm -f -- "$destination"; fi
}
rollback_failed_install() {
    local status=$?
    trap - ERR INT TERM
    set +e
    if ((ACTIVATING)); then
        printf '%s\n' 'Activation failed; restoring previous application/configuration.' >&2
        if ((!API_ENABLED_BEFORE)); then systemctl disable "$APP_UNIT"; fi
        restore_file env "$APP_ENV"; restore_file unit "$UNIT_FILE"
        restore_file resource "$RESOURCE_FILE"
        if [[ -n $OLD_RELEASE ]]; then activate_link "$OLD_RELEASE" current; else rm -f -- "$APP_ROOT/current"; fi
        systemctl daemon-reload
        if [[ -n $OLD_RELEASE ]]; then systemctl restart "$APP_UNIT"; else systemctl stop "$APP_UNIT"; fi
    fi
    printf 'Failed release retained for diagnosis: %s\n' "$RELEASE" >&2
    exit "$status"
}
trap rollback_failed_install ERR
trap 'false' INT TERM
ACTIVATING=1

# Existing resource limits remain the administrator's choice on upgrades.
if [[ ! -f $APP_ENV ]]; then install -m 600 "$RELEASE/.rendered/app.env" "$APP_ENV"; fi
if ((PORT_SET)); then sed -i "s/^AVATAR_MASK_HTTP_PORT=.*/AVATAR_MASK_HTTP_PORT=$HTTP_PORT/" "$APP_ENV"; fi
if ((LISTEN_SET)); then sed -i "s/^AVATAR_MASK_HTTP_LISTEN=.*/AVATAR_MASK_HTTP_LISTEN=$HTTP_LISTEN/" "$APP_ENV"; fi
install -D -m 644 "$SCRIPT_DIR/wechat-avatar-mask.service" "$UNIT_FILE"
if [[ ! -f $RESOURCE_FILE ]]; then install -D -m 644 "$RELEASE/.rendered/resources.conf" "$RESOURCE_FILE"; fi
activate_link "$RELEASE" current
systemctl daemon-reload
systemctl restart "$APP_UNIT"
wait_api
systemctl enable "$APP_UNIT"
check_frontend
if [[ -n $OLD_RELEASE ]]; then activate_link "$OLD_RELEASE" previous; fi
ACTIVATING=0
trap - ERR INT TERM
printf 'Installed release: %s\n' "$RELEASE_ID"
show_urls
printf '%s\n' 'No firewall rules were changed. Restarting/updating releases clears in-memory image tasks.'
printf '%s\n' 'Manage: sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh status'
