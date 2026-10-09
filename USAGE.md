# Using SedgeID

Every capability is available twice: interactively through the numbered menu and
non-interactively as a subcommand with flags. Nothing here needs the network.

```bash
python3 sedgeid.py            # the menu
python3 sedgeid.py --help     # the same commands, documented
```

## 1. The main menu

Running the tool with no arguments prints a banner with the dataset version, the
seven numbered choices and a prompt:

```
────────────────────────────────────────────────────────────────────────────────
SedgeID 1.0.0  sedge & grass identification  (dataset 2025.1)
  1) Identify a plant   walk the dichotomous sedge/grass key
  2) Wetland status     indicator codes, meanings and regions
  3) Species            browse and search the bundled dataset
  4) Tally              record observations in sedgeid_tally.csv
  5) Observer           who is recording this survey
  6) Dataset            integrity check and coverage
  7) Help               usage for every command
  q) Quit
  Select 1-7, q to quit:
```

Enter a number, or `q` / Enter / Ctrl-D to leave. A dataset integrity warning,
if one ever occurred, prints as a single line above the list and the menu keeps
working; the affected command refuses and says why.

## 2. Identifying a plant

```bash
python3 sedgeid.py identify --group sedge     # interactive
python3 sedgeid.py identify --group grass     # interactive
python3 sedgeid.py identify                   # asks group: sedge / grass / not sure
```

Each screen is one couplet with 2-4 numbered choices plus three helpers:

| Key | Effect |
| --- | --- |
| `b` | Step back one couplet; the earlier answer is shown as the trail |
| `r` | Restart at couplet 1 of the chosen group with an empty trail |
| `q` | Quit to the menu at any point, writing nothing to the tally sheet |

Choosing "not sure" runs two screening questions (ligule present? leaves
three-ranked?) and then starts the key in the group they point to.

When a couplet terminates, the **result card** shows the scientific and common
name, family and genus, the national wetland code with its plain-English
meaning, the region sublists, the diagnostic note and the recorded trail of
choices. At a terminal it then offers: `a` add to tally (the species is
pre-filled), `c` full species card, `r` run the key again, `m` back to the menu.

Non-interactive runs accept the same answers as numbers:

```bash
python3 sedgeid.py identify --group sedge --answers 1,2,1      # Carex vulpinoidea
printf '2 2 2 2 2 1\n' | python3 sedgeid.py identify --group grass   # Panicum virgatum
```

Unknown choices, blank lines and out-of-range numbers are reported in one line
and the same couplet is shown again; Ctrl-D ends the run cleanly with no
traceback and nothing written.

## 3. Wetland indicator status

```bash
python3 sedgeid.py wetland                            # the six codes + region legend
python3 sedgeid.py wetland --species "Carex stricta"  # one block per match
python3 sedgeid.py wetland --code OBL                 # reverse lookup by code
```

The six codes are OBL, FACW, FAC, FACU, UPL and NI, each printed with its
plain-English meaning. A species lookup prints the national code, its meaning
and the region sublists (NE, SE, MW, GP, WMV, AK, HI) that species appears on. An
unknown name prints `no match` plus up to three closest dataset names and exits
1. This is reference information: it is not a delineation call, and the bundled
set is a starter subset.

## 4. Browsing the dataset

```bash
python3 sedgeid.py species                              # every bundled species
python3 sedgeid.py species --query carex                # name fragment
python3 sedgeid.py species --family Poaceae             # grasses only
python3 sedgeid.py species --code FACU                  # by indicator code
python3 sedgeid.py species "Spartina pectinata"         # full detail card
python3 sedgeid.py species --query spartina --json      # machine readable
```

Each list prints a match count above the rows (scientific name, common name,
family, code). A filter that matches nothing prints `no matches`, the available
filter syntax and exits 1.

## 5. The survey tally sheet

```bash
python3 sedgeid.py tally add "Carex stricta" --count 3 --site "Plot A" \
    --date 2025-06-14 --region NE --method key --note "hummock"
python3 sedgeid.py tally                 # rows + running total + CSV path
python3 sedgeid.py tally list --site "Plot A" --since 2025-06-01
python3 sedgeid.py tally undo            # remove the last appended row
python3 sedgeid.py tally summary         # counts by species, site, wetland code
python3 sedgeid.py tally summary --csv summary.csv
```

The header is written automatically on first use; rows are only ever appended,
except that `undo` removes the last one. `--count` must be a positive whole
number, `--date` must be ISO `YYYY-MM-DD` (today is the default) and `--region`
must be one of the seven sublist codes - each mistake prints one line, writes
nothing and exits 2. If the sheet cannot be written, the tool names the problem
and prints the entry that could not be saved so nothing is lost.

`sedgeid_tally.csv` columns:

```
entry_id,observed_on,observer,species_id,species_name,count,site,region,method,note
E0001,2025-06-14,R. Okafor,carex_stricta,Carex stricta,3,Plot A,NE,key,hummock
```

`tally summary` prints the total row and plant count followed by three aligned
count tables (species, site, wetland code), none wider than 80 columns. With no
rows yet it prints the exact command that adds the first one and exits 0.

## 6. The observer (the offline "who is this?")

```bash
python3 sedgeid.py me --set "R. Okafor" --site "Plot A" --region NE
python3 sedgeid.py me
```

The observer and defaults live in `.sedgeid_config.json` in the working
directory and are stamped on every new tally row. On the first interactive tally
command with no config file, SedgeID asks once for a name or initials and saves
it. When the file is missing, unreadable or unwritable, one line explains the
problem, the observer is left blank and the row is still appended. There are no
passwords, sessions or accounts: offline, a local name is all an identity can be.

## 7. Dataset integrity

```bash
python3 sedgeid.py data --check
```

Prints the species count, counts by family and by wetland code, the couplet
count per group with its maximum depth, how many species are reachable by a key
path, and the dangling-reference count (0). The check also runs at load time; if
a reference ever broke, one line would print and only the affected command would
refuse.

## Status colours

SedgeID is built for a dark terminal: monospace, aligned columns and one accent
(`#008ca1`) reserved for the primary status - the wetland code. Colour is
emitted only when stdout is a terminal, honours `NO_COLOR`, and `--no-color`
turns it off explicitly; no signal is carried by colour alone.
