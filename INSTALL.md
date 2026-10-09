# Installing and running SedgeID

SedgeID is one standard-library Python file. There is no server, no database and
no network access. The steps below are the whole setup.

## Prerequisites

* **Python 3.9 or newer** (`python3 --version`). No other system package is
  needed: the tool imports only `argparse`, `csv`, `difflib`, `json`, `os`,
  `sys`, `textwrap`, `dataclasses` and `datetime`.
* A terminal. Linux, macOS and Windows terminals all work.

Nothing is installed system-wide, no `sudo` is used, and no service is started.

## 1. Install (one command, then it exits)

```bash
bash install.sh
```

`install.sh` upgrades `pip`/`setuptools`/`wheel`, installs `requirements.txt`
(the pinned test runner - the tool itself needs no third-party package), parses
`sedgeid.py` and runs `data --check` as a self-check. It does **not** start the
tool and it does not need a `.env` file.

If you would rather not run it at all: the tool already works, because it only
uses the standard library.

## 2. Seed demo data (optional, one command, then it exits)

```bash
bash demo.sh
```

This exercises every capability non-interactively inside `./demo_run`: it wipes
that directory first (so it is safe to re-run), sets an observer, walks both
keys, appends three tally rows, undoes one, prints the summary and writes
`demo_run/tally_summary.csv`. It requires no input and exits 0.

The bundled species dataset needs no seeding - it is embedded in `sedgeid.py`.
Your own tally sheet is created automatically the first time you add a row:
`sedgeid.py tally add "Carex stricta" --count 3 --site "Plot A"` writes the
header and the row into `./sedgeid_tally.csv`.

## 3. Run

```bash
python3 sedgeid.py                 # interactive menu
python3 sedgeid.py --help          # every command and its flags
python3 sedgeid.py identify --group sedge
```

There is no background process and no port: every command runs, prints and
exits. Walkthroughs of each command are in `USAGE.md`.

## 4. Test

```bash
python3 -m pytest -q          # or: python3 -m unittest test_sedgeid
```

The suite creates its own temporary working directories, so it never touches the
tally sheet or config file in the directory you are standing in.

## Uninstalling

Delete `sedgeid.py`, the files you are done with (`sedgeid_tally.csv`,
`.sedgeid_config.json`) and, if you ran the demo, `demo_run/`. Nothing was
installed outside the project directory except the pinned test runner in the
Python environment.

## Troubleshooting

* **`python3: command not found`** - install Python 3.9+ from your platform's
  usual source. `install.sh` installs only Python packages, never interpreters.
* **`Permission denied` when adding a tally row** - the tool names the problem
  and prints the entry it could not write, so nothing is lost; move to a
  writable directory (or pass `--csv`/`--site` pointing somewhere writable) and
  re-run the `tally add` command it shows you.
* **`no package index reachable` during `install.sh`** - you are offline. That is
  fine for SedgeID: the tool needs no packages. `install.sh` continues when
  `pytest` is already importable and fails loudly when it is not.
* **Colours look wrong in a pipe or a log** - colour is disabled automatically
  when stdout is not a terminal; `NO_COLOR=1` or `--no-color` forces it off.
