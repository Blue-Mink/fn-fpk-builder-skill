#!/bin/sh
# Local / CI gate: sanitize -> compile -> CLI surface -> full test suite -> diff check.
set -eu
cd "$(dirname "$0")/.."

python3 tests/sanitize.py
python3 -m compileall -q scripts tests

for command in toolchain init doctor build inspect sources; do
  python3 scripts/fpk.py "$command" --help | grep -q -- '--json'
done
for command in doctor deploy status logs start stop uninstall smoke; do
  python3 scripts/fnos.py "$command" --help | grep -q -- '--json'
done

python3 -m unittest discover -s tests -v

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check
fi
echo "CI PASS"
