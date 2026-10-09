#!/usr/bin/env bash
# SedgeID installer.
#
# Installs PROJECT-LEVEL dependencies only (never system packages, never sudo),
# then runs a cheap self-check and EXITS.  Starting the tool is a separate
# command: `python3 sedgeid.py`.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "[1/4] interpreter: $("$PYTHON" --version 2>&1)"

echo "[2/4] upgrading pip, setuptools and wheel"
"$PYTHON" -m pip install --upgrade pip setuptools wheel --disable-pip-version-check \
  || echo "  note: no package index reachable to upgrade the packaging tools; continuing"

echo "[3/4] installing requirements.txt"
if ! "$PYTHON" -m pip install --disable-pip-version-check -r requirements.txt; then
  if "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
    echo "  note: no package index reachable; using the pytest already present in this interpreter"
  else
    echo "install.sh: could not install requirements.txt and pytest is not importable" >&2
    exit 1
  fi
fi

echo "[4/4] self-check"
"$PYTHON" -c "import ast, pathlib; ast.parse(pathlib.Path('sedgeid.py').read_text())"
"$PYTHON" sedgeid.py data --check >/dev/null
echo "  sedgeid.py parses and the embedded dataset passes its integrity check"

cat <<'MSG'

SedgeID runs on the standard library alone - there is nothing else to install.

Next steps:
  python3 sedgeid.py             # interactive menu
  python3 sedgeid.py --help      # every command
  bash demo.sh                   # end-to-end non-interactive demo
  python3 -m pytest -q           # test suite
MSG
