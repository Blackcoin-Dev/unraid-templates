#!/bin/sh
set -eu
exec python3 /usr/local/lib/blackcoin-gui/supervise.py "$@"
