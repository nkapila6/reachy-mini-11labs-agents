#!/bin/bash
# deploy.sh -- scp changed files to the Reachy Mini and sync the venv
#
# Compares SHA256 hashes to skip unchanged files.
# Uses a single SSH session for all remote operations.
#
# Usage:
#   ./deploy.sh
#   ROBOT=pollen@reachy-mini.local ./deploy.sh

set -euo pipefail

ROBOT="${ROBOT:-pollen@reachy-mini.local}"
ROBOT_DIR="${ROBOT_DIR:-reachy-11labs-agent}"

# Load SSH key into agent so we only enter the passphrase once.
if ! ssh-add -l 2>/dev/null | grep -q "id_ed25519"; then
	ssh-add ~/.ssh/id_ed25519 2>/dev/null || true
fi

# Python source + config files to sync.
FILES="main.py reachy_audio.py pinchtab_tools.py pyproject.toml .env"

echo "=== checking robot prerequisites ==="
# Ensure the daemon's Python 3.12 exists (uv-managed, can get deleted).
ssh "$ROBOT" "/venvs/mini_daemon/bin/python --version 2>/dev/null || /opt/uv/uv python install 3.12"
# Max out the speaker volume.
ssh "$ROBOT" "amixer -c 0 cset numid=5 60,60 >/dev/null 2>&1 || true"
echo "done"

echo ""
echo "=== checking which files changed ==="

# Single SSH call: get all remote hashes at once.
REMOTE_HASHES=$(ssh "$ROBOT" "mkdir -p $ROBOT_DIR && cd $ROBOT_DIR 2>/dev/null && for f in $FILES; do if [ -f \"\$f\" ]; then echo \$(sha256sum \"\$f\"); else echo \"MISSING \$f\"; fi; done" 2>/dev/null || echo "")

CHANGED=""
for f in $FILES; do
	if [ "$f" = ".env" ] && [ ! -f "$f" ]; then
		echo "  SKIP $f (not found locally)"
		continue
	fi
	if [ ! -f "$f" ]; then
		echo "  SKIP $f (not found locally)"
		continue
	fi
	local_hash=$(shasum -a 256 "$f" | awk '{print $1}')
	remote_line=$(echo "$REMOTE_HASHES" | grep " $f$" || true)
	if [ -z "$remote_line" ]; then
		if echo "$REMOTE_HASHES" | grep -q "MISSING $f"; then
			echo "  CHG  $f (not on robot)"
			CHANGED="$CHANGED $f"
		else
			echo "  CHG  $f (no remote info)"
			CHANGED="$CHANGED $f"
		fi
	else
		remote_hash=$(echo "$remote_line" | awk '{print $1}')
		if [ "$local_hash" = "$remote_hash" ]; then
			echo "  OK   $f (unchanged)"
		else
			echo "  CHG  $f"
			CHANGED="$CHANGED $f"
		fi
	fi
done

echo ""
CHANGED=$(echo $CHANGED | xargs)
if [ -z "$CHANGED" ]; then
	echo "=== nothing to copy -- all files match ==="
else
	# Stop the running agent so we can overwrite files.
	ssh "$ROBOT" "pkill -f 'python.*main.py' 2>/dev/null; sleep 1" || true
	echo "=== scp changed file(s) to $ROBOT ==="
	scp $CHANGED "$ROBOT:$ROBOT_DIR/"
	echo "done"
fi

echo ""
echo "=== syncing Python venv on robot ==="
ssh "$ROBOT" "cd $ROBOT_DIR && /opt/uv/uv sync"
echo "venv ready"

echo ""
echo "=== to run ==="
echo "  ssh $ROBOT"
echo "  cd $ROBOT_DIR && /opt/uv/uv run reachy-agent"
