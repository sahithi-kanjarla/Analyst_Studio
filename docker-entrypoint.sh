#!/bin/sh
# Start as root only long enough to make the data folder writable, then run the app as the unprivileged user "app".
# Why: a mounted volume (Railway, a Docker named volume on some hosts) arrives owned by root, and the app could not write to it.
set -e
DATA="${DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA"
    # only walk the whole tree when the top folder is not already ours (a big volume would make every start slow)
    if [ "$(stat -c %u "$DATA")" != "10001" ]; then
        chown -R 10001:10001 "$DATA"
    fi
    export HOME=/home/app
    exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
fi
exec "$@"
