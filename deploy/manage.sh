#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
COMMAND=${1:-status}

case "$COMMAND" in
    status)
        systemctl --no-pager --full status "$APP_UNIT"
        show_urls
        ;;
    logs)
        lines=${2:-100}
        [[ $lines =~ ^[0-9]+$ && ${#lines} -le 4 ]] || fail 'Use a numeric log line count (max 9999)'
        journalctl --no-pager -u "$APP_UNIT" -n "$lines"
        ;;
    health)
        wait_api
        check_frontend
        curl --noproxy '*' -fsS --max-time 5 "$(service_url)/api/health"
        printf '\n'
        show_urls
        ;;
    versions)
        printf 'Current: %s\n' "$(readlink -f -- "$APP_ROOT/current")"
        printf 'Previous: %s\n' "$(readlink -f -- "$APP_ROOT/previous" || true)"
        find "$APP_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r
        ;;
    start|restart)
        need_root
        systemctl "$COMMAND" "$APP_UNIT"
        wait_api
        check_frontend
        show_urls
        ;;
    stop)
        need_root
        # Stop only this application's single service.
        systemctl stop "$APP_UNIT"
        ;;
    rollback)
        need_root
        exec 9>/run/lock/wechat-avatar-mask-deploy.lock
        flock -n 9 || fail 'Another install/rollback is running'
        old=$(release_path "$APP_ROOT/current") || fail 'Current release is invalid'
        if [[ -n ${2:-} ]]; then
            [[ $2 =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || fail 'Invalid release ID'
            target=$(release_path "$APP_ROOT/releases/$2") || fail 'Requested release is invalid'
        else
            target=$(release_path "$APP_ROOT/previous") || fail 'No previous release exists'
        fi
        [[ $target != "$old" ]] || fail 'That release is already active'
        restore_current() {
            trap - ERR INT TERM
            set +e
            activate_link "$old" current
            systemctl restart "$APP_UNIT"
            printf '%s\n' 'Rollback target failed health checks; original release restored.' >&2
            exit 1
        }
        trap restore_current ERR
        trap 'false' INT TERM
        activate_link "$target" current
        systemctl restart "$APP_UNIT"
        wait_api
        check_frontend
        activate_link "$old" previous
        trap - ERR INT TERM
        printf 'Active release: %s\n' "$target"
        printf '%s\n' 'In-memory image tasks were cleared; select screenshots again.'
        ;;
    --help|-h)
        printf '%s\n' 'Usage: sudo bash deploy/manage.sh {status|logs [LINES]|health|versions|start|restart|stop|rollback [RELEASE_ID]}'
        ;;
    *) fail "Unknown command: $COMMAND" ;;
esac
