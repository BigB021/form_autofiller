#!/bin/bash
# Form Auto-Filler - one-time setup for Mac. Double-click this file.
# - installs Python (from python.org) if this Mac does not have a usable one
# - installs what the app needs into the ".venv" folder next to this file

PY_VERSION="3.13.7"
PY_PKG_URL="https://www.python.org/ftp/python/${PY_VERSION}/python-${PY_VERSION}-macos11.pkg"

cd "$(dirname "$0")" || exit 1
clear
echo "============================================="
echo "  Form Auto-Filler - one-time setup"
echo "============================================="
echo

finish() {
    echo
    echo "Press Enter to close this window."
    read -r _
    exit "$1"
}

failed() {
    echo
    echo "Something went wrong: $1"
    echo "Take a screenshot of this window and send it to the person who gave you the app."
    finish 1
}

# Files downloaded from the internet are blocked by macOS; this unblocks the app folder
# (so start.command opens normally) and makes sure the scripts can run.
xattr -dr com.apple.quarantine . 2>/dev/null
chmod +x ./*.command 2>/dev/null

# A Python that is recent enough AND has the window toolkit (tkinter) the app uses.
# /usr/bin/python3 (Apple's) is skipped on purpose: it may pop up a "developer tools" install
# and its window toolkit is too old.
usable() {
    [ -x "$1" ] && "$1" -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1
}
find_python() {
    PY=""
    # python.org's Python first (the newest one wins), then others
    for candidate in /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3; do
        usable "$candidate" && PY="$candidate"
    done
    if [ -z "$PY" ]; then
        for candidate in /usr/local/bin/python3 /opt/homebrew/bin/python3; do
            if usable "$candidate"; then
                PY="$candidate"
                break
            fi
        done
    fi
    [ -n "$PY" ]
}

echo "Step 1 of 3: Python"
if find_python; then
    echo "  Python is already installed: $("$PY" --version)"
else
    echo "  Python is not installed yet. Downloading it (about 70 MB)..."
    PKG="${TMPDIR:-/tmp}/python-${PY_VERSION}.pkg"
    curl -L --fail --progress-bar -o "$PKG" "$PY_PKG_URL" || failed "the Python download did not work. Check the internet connection and try again."
    echo
    echo "  Installing Python. Your Mac will ask for YOUR MAC PASSWORD"
    echo "  (the one you use to unlock the computer)."
    echo "  While you type it, nothing appears on the screen - that is normal. Press Enter after it."
    echo
    sudo installer -pkg "$PKG" -target / || failed "Python could not be installed."
    rm -f "$PKG"
    find_python || failed "Python was installed but cannot be used."
    echo "  Python installed: $("$PY" --version)"
fi

echo
echo "Step 2 of 3: installing what the app needs (this can take a minute)..."
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import tkinter" >/dev/null 2>&1; then
    rm -rf .venv
    "$PY" -m venv .venv || failed "could not prepare the app folder."
fi
.venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip >/dev/null 2>&1
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || failed "could not install what the app needs. Check the internet connection and try again."
echo "  Done."

echo
echo "Step 3 of 3: web browser"
BROWSER=""
for app in "Brave Browser" "Google Chrome" "Microsoft Edge"; do
    if [ -d "/Applications/$app.app" ] || [ -d "$HOME/Applications/$app.app" ]; then
        BROWSER="$app"
        break
    fi
done
if [ -n "$BROWSER" ]; then
    echo "  The app will use $BROWSER."
else
    echo "  !! No Brave or Google Chrome found on this Mac."
    echo "  !! Install Google Chrome (google.com/chrome) or Brave (brave.com), then you are ready."
fi

echo
echo "============================================="
echo "  Setup finished."
echo "  Now double-click start.command"
echo "============================================="
finish 0
