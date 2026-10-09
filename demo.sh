#!/usr/bin/env bash
# SedgeID end-to-end demo.
#
# Runs every capability of the tool non-interactively - dataset check, species
# browse, wetland lookup, the dichotomous key, the tally sheet, the summary and
# the no-argument menu - inside ./demo_run, which is wiped first so the demo is
# idempotent and repeatable.  No network access and no user input are required.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
SEDGEID="$PWD/sedgeid.py"
WORK="$PWD/demo_run"

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK"

step() { printf '\n=== %s ===\n' "$1"; }

step "version and dataset integrity check"
"$PYTHON" "$SEDGEID" --version
"$PYTHON" "$SEDGEID" data --check

step "browse and search the bundled species"
"$PYTHON" "$SEDGEID" species --query spartina
"$PYTHON" "$SEDGEID" species "Spartina pectinata"

step "wetland indicator status (national code, meaning, region sublists)"
"$PYTHON" "$SEDGEID" wetland
"$PYTHON" "$SEDGEID" wetland --species "Phragmites australis"
"$PYTHON" "$SEDGEID" wetland --code UPL

step "observer: the offline equivalent of signing in"
"$PYTHON" "$SEDGEID" me --set "R. Okafor" --site "Plot A" --region NE
"$PYTHON" "$SEDGEID" me

step "dichotomous key - sedge (Carex to species)"
"$PYTHON" "$SEDGEID" identify --group sedge --answers 1,2,1

step "dichotomous key - grass (answer trail piped in)"
printf '2 2 2 2 2 1\n' | "$PYTHON" "$SEDGEID" identify --group grass

step "tally sheet: append rows, list them, undo the last one"
"$PYTHON" "$SEDGEID" tally add "Carex vulpinoidea" --count 5 --site "Plot A" \
  --method key --note "hummock in standing water"
"$PYTHON" "$SEDGEID" tally add "Panicum virgatum" --count 2 --site "Plot A"
"$PYTHON" "$SEDGEID" tally add "Carex stricta" --count 4 --site "Plot B"
"$PYTHON" "$SEDGEID" tally list
"$PYTHON" "$SEDGEID" tally undo

step "tally summary: counts by species, site and wetland code"
"$PYTHON" "$SEDGEID" tally summary
"$PYTHON" "$SEDGEID" tally summary --csv "$WORK/tally_summary.csv"

step "the no-argument launch view (menu)"
printf 'q\n' | "$PYTHON" "$SEDGEID"

step "persisted files in the working directory"
ls -l "$WORK"
printf '\n'
cat sedgeid_tally.csv

printf '\nDemo complete. Tally sheet written to %s\n' "$WORK/sedgeid_tally.csv"
