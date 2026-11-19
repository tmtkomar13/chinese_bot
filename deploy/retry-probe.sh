#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
releases=(/opt/chinese-trainer-release.????????)
snapshots=(/var/backups/chinese-trainer.????????)
release=$(ls -td -- "${releases[@]}" | head -n 1)
snapshot=$(ls -td -- "${snapshots[@]}" | head -n 1)
test -x "$release/.venv/bin/python"
test -s "$snapshot/new-environment"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
cp -R trainer assets "$stage/"
chmod -R a+rX "$stage"
systemd-run --wait --pipe --collect -p User=chinese-trainer -p WorkingDirectory="$stage" \
    -p EnvironmentFile="$snapshot/new-environment" "$release/.venv/bin/python" -u -m trainer.probe
