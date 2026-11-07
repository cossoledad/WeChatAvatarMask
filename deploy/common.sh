#!/usr/bin/env bash
# Sourced by the installer and management commands; not an entry point.
APP_ROOT=/opt/wechat-avatar-mask
APP_UNIT=wechat-avatar-mask.service
APP_USER=wechat-avatar-mask
APP_ENV=/etc/wechat-avatar-mask.env
UNIT_FILE=/etc/systemd/system/wechat-avatar-mask.service
RESOURCE_FILE=/etc/systemd/system/wechat-avatar-mask.service.d/resources.conf

fail() { printf '%s\n' "ERROR: $*" >&2; exit 1; }
need_root() { [[ $EUID -eq 0 ]] || fail 'Run this command with sudo.'; }

release_path() {
    local resolved
    resolved=$(realpath -e -- "$1") || return 1
    [[ $resolved == "$APP_ROOT/releases/"* && -f $resolved/backend/main.py && -x $resolved/.venv/bin/python ]] || return 1
    printf '%s\n' "$resolved"
}

activate_link() {
    local target=$1 name=$2 temporary
    temporary="$APP_ROOT/.${name}-$$"
    ln -s -- "$target" "$temporary"
    mv -Tf -- "$temporary" "$APP_ROOT/$name"
}

service_url() {
    local port listen
    port=$(sed -n 's/^AVATAR_MASK_HTTP_PORT=//p' "$APP_ENV")
    listen=$(sed -n 's/^AVATAR_MASK_HTTP_LISTEN=//p' "$APP_ENV")
    [[ $port =~ ^[0-9]+$ ]] || return 1
    [[ $listen != 0.0.0.0 ]] || listen=127.0.0.1
    printf 'http://%s:%s\n' "$listen" "$port"
}

wait_api() {
    local count url
    url=$(service_url) || return 1
    for count in {1..30}; do
        if curl --noproxy '*' -fsS --max-time 2 "$url/api/health" >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    return 1
}

check_frontend() {
    local url
    url=$(service_url) || return 1
    curl --noproxy '*' -fsS --max-time 5 "$url/" >/dev/null || return 1
    curl --noproxy '*' -fsS --max-time 5 "$url/api/health" >/dev/null
}

show_urls() {
    local port listen ip
    port=$(sed -n 's/^AVATAR_MASK_HTTP_PORT=//p' "$APP_ENV")
    listen=$(sed -n 's/^AVATAR_MASK_HTTP_LISTEN=//p' "$APP_ENV")
    if [[ $listen != 0.0.0.0 ]]; then printf 'URL: http://%s:%s\n' "$listen" "$port"; return; fi
    while IFS= read -r ip; do
        [[ -z $ip ]] || printf 'URL: http://%s:%s\n' "$ip" "$port"
    done < <(ip -4 -o addr show scope global | awk '{split($4,a,"/"); print a[1]}')
}
