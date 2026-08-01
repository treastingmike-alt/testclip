#!/bin/sh
# Refreshes the YouTube session used for downloads.
#
# Run this on a machine with a browser signed in to YouTube. It writes
# cookies.txt, which is portable: copy it to a server and downloads work there
# with no browser and no keychain.
#
# YouTube sessions expire, so re-run this if downloads start failing with
# "Sign in to confirm you're not a bot".
set -e
BROWSER="${1:-chrome}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Reading your $BROWSER YouTube session (macOS may ask for your password once)..."
yt-dlp --cookies-from-browser "$BROWSER" --cookies "$DIR/cookies.txt" \
       --skip-download --no-playlist --quiet --no-warnings \
       "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
chmod 600 "$DIR/cookies.txt"

echo "Verifying..."
yt-dlp --cookies "$DIR/cookies.txt" --no-playlist --simulate --quiet --no-warnings \
       "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
echo "OK -- cookies.txt written. Restart the backend."
