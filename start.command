#!/bin/bash
# Form Auto-Filler - double-click this file to start the app (Mac).
cd "$(dirname "$0")" || exit 1
clear
if [ ! -x .venv/bin/python ]; then
    echo "The app is not set up yet: double-click setup.command first."
    echo
    echo "Press Enter to close this window."
    read -r _
    exit 1
fi
echo "Form Auto-Filler is running in its own window."
echo "Keep THIS window open while you use the app (you can minimise it)."
echo
.venv/bin/python autofill_app.py
status=$?
if [ $status -ne 0 ]; then
    echo
    echo "The app stopped with an error (see above). Take a screenshot of this window"
    echo "and send it to the person who gave you the app."
    echo "Press Enter to close this window."
    read -r _
fi
exit $status
