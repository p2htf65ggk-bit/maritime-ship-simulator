#!/bin/sh
# SPDX-License-Identifier: MPL-2.0

APP_DIR=$1
shift
APP_EXE="$APP_DIR/Ship Simulator V2"
UBUNTU_HELP_URL="https://help.ubuntu.com/stable/ubuntu-help/addremove-install.html"

show_missing_libraries() {
    missing=$1
    missing_list=$(printf '%s\n' "$missing" | awk 'NF { print "- " $0 }')
    message="Maritime Ship Simulator V2 cannot start because required Ubuntu system libraries are missing:\n\n$missing_list\n\nPlease install the required Ubuntu packages and retry.\n\nCommon Qt runtime packages:\n\nsudo apt update && sudo apt install libgl1 libnss3 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1\n\nOfficial Ubuntu help:\n$UBUNTU_HELP_URL"

    # stderr is the authoritative non-interactive diagnostic. Emit it before
    # any optional desktop helper so CI, terminal and launcher logs are useful.
    printf '%b\n' "$message" >&2

    if [ -n "${CI:-}" ] || [ -n "${PYTEST_CURRENT_TEST:-}" ] || \
       [ "${QT_QPA_PLATFORM:-}" = "offscreen" ] || \
       { [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; }
    then
        return 1
    fi

    if command -v zenity >/dev/null 2>&1; then
        response=$(zenity --question --title="Required component missing" --width=620 \
            --text="$message" --ok-label="Retry" --cancel-label="Close" \
            --extra-button="Open Ubuntu help" 2>/dev/null)
        status=$?
        if [ "$response" = "Open Ubuntu help" ]; then
            if command -v xdg-open >/dev/null 2>&1; then
                xdg-open "$UBUNTU_HELP_URL" >/dev/null 2>&1 &
            fi
            return 0
        fi
        return "$status"
    fi
    if command -v kdialog >/dev/null 2>&1; then
        kdialog --title "Required component missing" --warningyesno "$message" \
            --yes-label "Retry" --no-label "Close"
        return $?
    fi
    return 1
}

if [ ! -x "$APP_EXE" ]; then
    printf 'Maritime Ship Simulator V2 is incomplete. The application executable is missing from:\n%s\n' "$APP_DIR" >&2
    exit 126
fi

while :; do
    missing=""
    if command -v ldd >/dev/null 2>&1; then
        for library in \
            "$APP_EXE" \
            "$APP_DIR/_internal/PySide6/Qt/lib/libQt6Core.so.6" \
            "$APP_DIR/_internal/PySide6/Qt/lib/libQt6Gui.so.6" \
            "$APP_DIR/_internal/PySide6/Qt/lib/libQt6Widgets.so.6" \
            "$APP_DIR/_internal/PySide6/Qt/lib/libQt6WebEngineCore.so.6" \
            "$APP_DIR/_internal/PySide6/Qt/plugins/platforms/libqxcb.so"
        do
            if [ -f "$library" ]; then
                current=$(ldd "$library" 2>/dev/null | awk '/=> not found/ { print $1 }')
                if [ -n "$current" ]; then
                    missing="$missing
$current"
                fi
            fi
        done
    fi
    missing=$(printf '%s\n' "$missing" | awk 'NF' | sort -u)
    if [ -z "$missing" ]; then
        exec "$APP_EXE" "$@"
    fi
    show_missing_libraries "$missing" || exit 127
done
