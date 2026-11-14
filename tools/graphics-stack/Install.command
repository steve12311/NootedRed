#!/bin/bash
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SYSTEM=$(/usr/bin/osascript -e 'POSIX path of (choose folder with prompt "Choose the mounted macOS 27 TEST system volume")')
EFI=$(/usr/bin/osascript -e 'POSIX path of (choose folder with prompt "Choose the mounted OpenCore EFI/OC folder")')
STAMP=$(/bin/date '+%Y%m%d-%H%M%S')
WORK="$HOME/Library/Caches/NootedRed/graphics-$STAMP"
RECEIPT="$HOME/Library/Application Support/NootedRed/graphics-$STAMP"
echo "Preparing graphics for: $SYSTEM"
echo "OpenCore folder: $EFI"
echo "Rollback receipt: $RECEIPT"
sudo /usr/bin/python3 "$HERE/stack.py" setup --package "$HERE" --system "$SYSTEM" --efi "$EFI" --work "$WORK" --receipt "$RECEIPT"
echo "Finished. No restart was performed. Keep this rollback command:"
printf 'sudo /usr/bin/python3 %q rollback --receipt %q\n' "$HERE/stack.py" "$RECEIPT"
read -r -p "Press Return to close. " _
