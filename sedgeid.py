#!/usr/bin/env python3
"""SedgeID -- sedge and grass identification by traits, wetland indicator status
lookup, and a botanical survey tally sheet.

SedgeID is a single-file, standard-library-only command line tool for field
botanists.  The species, trait and couplet data are embedded in this file, so
the tool runs on an air-gapped field laptop with nothing installed and no
network.

Usage:
    python3 sedgeid.py                          # interactive menu
    python3 sedgeid.py --help                   # usage for every command
    python3 sedgeid.py identify --group sedge
    python3 sedgeid.py wetland --species "Phragmites australis"
    python3 sedgeid.py species --query carex
    python3 sedgeid.py tally add "Carex stricta" --count 3 --site "Plot A"
    python3 sedgeid.py tally summary
    python3 sedgeid.py me --set "R. Okafor"
    python3 sedgeid.py data --check
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import sys
import textwrap
from json import loads as json_loads
from dataclasses import dataclass
from datetime import date

VERSION = "1.0.0"
DATASET_VERSION = "2025.1"
TALLY_FILE = "sedgeid_tally.csv"
CONFIG_FILE = ".sedgeid_config.json"
OUTPUT_WIDTH = 80

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2
EXIT_IO_ERROR = 3

CSV_FIELDS = (
    "entry_id",
    "observed_on",
    "observer",
    "species_id",
    "species_name",
    "count",
    "site",
    "region",
    "method",
    "note",
)

# National wetland indicator codes (2018 National Wetland Plant List wording).
WETLAND_CODES = {
    "OBL": "Obligate wetland -- occurs almost always (>99%) in wetlands.",
    "FACW": "Facultative wetland -- usually in wetlands (67-99%), occasionally upland.",
    "FAC": "Facultative -- equally likely in wetlands and non-wetlands (34-66%).",
    "FACU": "Facultative upland -- usually in non-wetlands (67-99%), occasionally wet.",
    "UPL": "Obligate upland -- occurs almost always (>99%) in non-wetlands.",
    "NI": "No indicator -- not rated on the regional list; treat as undetermined.",
}

# National Wetland Plant List subregions referenced by the bundled species.
REGIONS = {
    "NE": "Northeast",
    "SE": "Southeast",
    "MW": "Midwest",
    "GP": "Great Plains",
    "WMV": "Western Mountains, Valleys & Coast",
    "AK": "Alaska",
    "HI": "Hawaii",
}

FAMILIES = ("Cyperaceae", "Poaceae")

MAX_COUPLET_DEPTH = 12


@dataclass(frozen=True)
class Trait:
    """A diagnostic character asked for by one or more couplets."""

    id: str
    label: str
    kind: str
    states: tuple
    applies_to: str


@dataclass(frozen=True)
class Species:
    """One bundled taxon with its traits, indicator code and regions."""

    id: str
    scientific_name: str
    common_name: str
    family: str
    genus: str
    trait_values: dict
    wetland_code: str
    regions: tuple
    key_note: str


@dataclass(frozen=True)
class KeyChoice:
    """One offered state of a couplet: a label plus where it leads."""

    label: str
    next: str


@dataclass(frozen=True)
class KeyNode:
    """One couplet of a dichotomous key for a group (sedge or grass)."""

    id: str
    group: str
    order_index: int
    prompt: str
    choices: tuple
    parent_id: str | None


@dataclass
class TallyEntry:
    """One row of the botanical survey tally sheet."""

    entry_id: str
    observed_on: str
    observer: str
    species_id: str
    species_name: str
    count: int
    site: str
    region: str = ""
    method: str = ""
    note: str = ""


@dataclass
class LocalConfig:
    """The local observer record stored in .sedgeid_config.json."""

    observer: str = ""
    default_site: str = ""
    default_region: str = ""
    dataset_version: str = DATASET_VERSION
    created_at: str = ""


TRAITS = (
    Trait("stem", "Stem cross-section", "categorical",
          ("triangular", "round", "sharply_triangular"), "sedge"),
    Trait("fruit", "Fruit covering", "binary",
          ("perigynium", "achene_in_scale"), "sedge"),
    Trait("spike_count", "Spikes per stem", "categorical",
          ("one", "two_to_ten", "many_in_head", "solitary_spikelet", "several_spikelets"),
          "sedge"),
    Trait("perigynium_shape", "Perigynium shape", "categorical",
          ("inflated", "flattened_winged", "flat_nerved", "lanceolate", "plump_beakless"),
          "sedge"),
    Trait("beak_length", "Perigynium beak", "ordered", ("none", "short", "long"), "sedge"),
    Trait("leaves", "Leaf width", "ordered", ("capillary", "narrow", "broad"), "sedge"),
    Trait("habit", "Habit", "categorical",
          ("tussock", "rhizomatous", "tufted", "mat_forming", "sod_forming",
           "stoloniferous", "annual"), "both"),
    Trait("inflorescence", "Inflorescence", "categorical",
          ("plume", "open_panicle", "spike", "raceme", "panicle_of_racemes", "contracted_panicle"),
          "grass"),
    Trait("awns", "Awns", "ordered", ("none", "short", "long"), "grass"),
    Trait("spikelet_compression", "Spikelet compression", "categorical",
          ("laterally_flattened", "terete", "plano_convex"), "grass"),
    Trait("florets", "Florets per spikelet", "categorical",
          ("one", "two_to_five", "many"), "grass"),
)


def _species(species_id, scientific_name, common_name, family, code, regions, note, traits):
    return Species(
        id=species_id,
        scientific_name=scientific_name,
        common_name=common_name,
        family=family,
        genus=scientific_name.split()[0],
        trait_values=traits,
        wetland_code=code,
        regions=tuple(regions.split()),
        key_note=note,
    )


# ---------------------------------------------------------------------------
# Bundled starter dataset: 45 common sedges (Cyperaceae) and grasses (Poaceae).
# Coverage is deliberately a starter set -- absence from this table is not
# evidence about a plant's indicator status.
# ---------------------------------------------------------------------------
SPECIES = (
    # --- Carex (18) -------------------------------------------------------
    _species(
        "carex_stricta", "Carex stricta", "Upright sedge", "Cyperaceae", "OBL", "NE MW SE GP WMV",
        "Large tussocks; erect pistillate spikes; perigynia 3-5 mm with a short straight beak.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "lanceolate", "beak_length": "short", "habit": "tussock",
         "leaves": "broad"}),
    _species(
        "carex_aquatilis", "Carex aquatilis", "Water sedge", "Cyperaceae", "OBL", "AK NE MW WMV",
        "Glaucous narrow leaves; slender perigynia 2-3.5 mm; wet meadows and shallow water.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "lanceolate", "beak_length": "short", "habit": "rhizomatous",
         "leaves": "narrow"}),
    _species(
        "carex_utriculata", "Carex utriculata", "Beaked sedge", "Cyperaceae", "OBL", "AK NE MW WMV",
        "Broad glaucous leaves in standing water; perigynia 5-8 mm spreading with a long beak.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "lanceolate", "beak_length": "long", "habit": "rhizomatous",
         "leaves": "broad"}),
    _species(
        "carex_lacustris", "Carex lacustris", "Lakebank sedge", "Cyperaceae", "OBL", "NE MW WMV",
        "Stout marsh sedge; broad leaves; perigynia ovoid 4-5.5 mm on short-stalked spikes.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "inflated", "beak_length": "short", "habit": "rhizomatous",
         "leaves": "broad"}),
    _species(
        "carex_lupulina", "Carex lupulina", "Hop sedge", "Cyperaceae", "OBL", "NE MW SE",
        "Largest perigynia of the key (7-12 mm), strongly inflated with a bent reflexed beak.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "inflated", "beak_length": "short", "habit": "tufted",
         "leaves": "broad"}),
    _species(
        "carex_intumescens", "Carex intumescens", "Greater bladder sedge", "Cyperaceae", "FACW", "NE MW SE",
        "Few large inflated perigynia 6-9 mm in a lax spike; wet woods and swamps.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "inflated", "beak_length": "short", "habit": "tufted",
         "leaves": "broad"}),
    _species(
        "carex_comosa", "Carex comosa", "Bristly sedge", "Cyperaceae", "OBL", "NE MW SE",
        "Long slender 2-toothed beak; perigynia reflexed and bristly-looking; wet meadows.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "lanceolate", "beak_length": "long", "habit": "tufted",
         "leaves": "broad"}),
    _species(
        "carex_crinita", "Carex crinita", "Fringed sedge", "Cyperaceae", "OBL", "NE MW SE",
        "Long-cylindric pistillate spikes on arching stalks; inflated many-nerved perigynia.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "inflated", "beak_length": "short", "habit": "tufted",
         "leaves": "broad"}),
    _species(
        "carex_hystericina", "Carex hystericina", "Porcupine sedge", "Cyperaceae", "OBL", "NE MW",
        "Short stout spikes; perigynia with long straight toothed beaks; alkaline fens and seeps.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "lanceolate", "beak_length": "long", "habit": "tufted",
         "leaves": "broad"}),
    _species(
        "carex_vulpinoidea", "Carex vulpinoidea", "Fox sedge", "Cyperaceae", "FACW", "NE MW SE GP",
        "Dense bristly head; flat strongly nerved perigynia radiate; beak teeth prominent.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "many_in_head",
         "perigynium_shape": "flat_nerved", "beak_length": "long", "habit": "tussock",
         "leaves": "narrow"}),
    _species(
        "carex_stipata", "Carex stipata", "Awl-fruited sedge", "Cyperaceae", "FACW", "AK NE MW SE",
        "Dense head; perigynia widest near the base, tapered to a serrate-margined beak.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "many_in_head",
         "perigynium_shape": "flat_nerved", "beak_length": "long", "habit": "tussock",
         "leaves": "narrow"}),
    _species(
        "carex_scoparia", "Carex scoparia", "Broom sedge", "Cyperaceae", "FACW", "NE MW SE",
        "Flattened wing-margined perigynia; spikes tapered; head open and bristly.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "many_in_head",
         "perigynium_shape": "flattened_winged", "beak_length": "short", "habit": "tussock",
         "leaves": "narrow"}),
    _species(
        "carex_alata", "Carex alata", "Broad-winged sedge", "Cyperaceae", "OBL", "NE SE",
        "Broad-ovoid spikes; perigynia with wide thin wings and a slightly bent beak.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "many_in_head",
         "perigynium_shape": "flattened_winged", "beak_length": "short", "habit": "tussock",
         "leaves": "narrow"}),
    _species(
        "carex_pensylvanica", "Carex pensylvanica", "Pennsylvania sedge", "Cyperaceae", "FACU", "NE MW SE",
        "Colonial from long rhizomes; narrow leaves; short appressed spikes; dry woods.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "lanceolate", "beak_length": "short", "habit": "rhizomatous",
         "leaves": "narrow"}),
    _species(
        "carex_granularis", "Carex granularis", "Limestone meadow sedge", "Cyperaceae", "FACW", "NE MW SE GP",
        "Head of short spikes; plump pale perigynia with an almost absent beak.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "many_in_head",
         "perigynium_shape": "plump_beakless", "beak_length": "none", "habit": "tufted",
         "leaves": "narrow"}),
    _species(
        "carex_blanda", "Carex blanda", "Eastern woodland sedge", "Cyperaceae", "FACU", "NE MW SE GP",
        "Loose heads of beakless plump perigynia; woods and old fields.",
        {"stem": "triangular", "fruit": "perigynium", "spike_count": "two_to_ten",
         "perigynium_shape": "plump_beakless", "beak_length": "none", "habit": "tufted",
         "leaves": "narrow"}),
    _species(
        "carex_leptalea", "Carex leptalea", "Bristle-stalked sedge", "Cyperaceae", "OBL", "NE MW SE",
        "Single slender terminal spike; few appressed perigynia; mossy wet ground.",
        {"stem": "round", "fruit": "perigynium", "spike_count": "one",
         "perigynium_shape": "lanceolate", "beak_length": "none", "habit": "tufted",
         "leaves": "capillary"}),
    _species(
        "carex_pauciflora", "Carex pauciflora", "Few-flowered sedge", "Cyperaceae", "OBL", "NE MW AK",
        "Single spike with a few spreading perigynia; low bog plant with arching leaves.",
        {"stem": "round", "fruit": "perigynium", "spike_count": "one",
         "perigynium_shape": "lanceolate", "beak_length": "short", "habit": "rhizomatous",
         "leaves": "narrow"}),
    # --- Other sedges: Eleocharis, Schoenoplectus, Scirpus, Dulichium, Cyperus (9)
    _species(
        "eleocharis_palustris", "Eleocharis palustris", "Common spikerush", "Cyperaceae", "OBL",
        "AK NE MW SE GP WMV",
        "Single terminal spikelet 8-30 mm on round stems from creeping rhizomes.",
        {"stem": "round", "fruit": "achene_in_scale", "spike_count": "solitary_spikelet",
         "habit": "rhizomatous", "leaves": "narrow"}),
    _species(
        "eleocharis_acicularis", "Eleocharis acicularis", "Needle spikerush", "Cyperaceae", "OBL",
        "AK NE MW SE GP WMV",
        "Capillary stems under 1 mm; tiny spikelets; carpets exposed mudflats.",
        {"stem": "round", "fruit": "achene_in_scale", "spike_count": "solitary_spikelet",
         "habit": "mat_forming", "leaves": "capillary"}),
    _species(
        "eleocharis_obtusa", "Eleocharis obtusa", "Blunt spikerush", "Cyperaceae", "FACW", "NE MW SE",
        "Tufted annual of pond shores; ovoid spikelets 3-8 mm; achene with a small tubercle.",
        {"stem": "round", "fruit": "achene_in_scale", "spike_count": "solitary_spikelet",
         "habit": "tufted", "leaves": "narrow"}),
    _species(
        "schoenoplectus_acutus", "Schoenoplectus acutus", "Hardstem bulrush", "Cyperaceae", "OBL",
        "NE MW SE GP WMV AK",
        "Hard sharply triangular stems that resist crushing; spikelets 8-15 mm.",
        {"stem": "sharply_triangular", "fruit": "achene_in_scale", "spike_count": "several_spikelets",
         "habit": "rhizomatous", "leaves": "narrow"}),
    _species(
        "schoenoplectus_tabernaemontani", "Schoenoplectus tabernaemontani", "Softstem bulrush",
        "Cyperaceae", "OBL", "NE MW SE GP",
        "Soft stems easily crushed between thumb and finger; shallow water and marshes.",
        {"stem": "round", "fruit": "achene_in_scale", "spike_count": "several_spikelets",
         "habit": "rhizomatous", "leaves": "narrow"}),
    _species(
        "scirpus_cyperinus", "Scirpus cyperinus", "Woolgrass", "Cyperaceae", "FACW", "NE MW SE",
        "Branching woolly-looking cluster; long curly perianth bristles.",
        {"stem": "triangular", "fruit": "achene_in_scale", "spike_count": "several_spikelets",
         "habit": "tufted", "leaves": "narrow"}),
    _species(
        "dulichium_arundinaceum", "Dulichium arundinaceum", "Three-way sedge", "Cyperaceae", "OBL", "NE MW SE",
        "Three-ranked leaves; one spikelet per axil; acid bogs and pond margins.",
        {"stem": "round", "fruit": "achene_in_scale", "spike_count": "several_spikelets",
         "habit": "rhizomatous", "leaves": "narrow"}),
    _species(
        "cyperus_esculentus", "Cyperus esculentus", "Yellow nutsedge", "Cyperaceae", "FACW", "NE MW SE GP",
        "Umbel of starburst spikes; tubers on the rhizomes; wet fields and gardens.",
        {"stem": "triangular", "fruit": "achene_in_scale", "spike_count": "several_spikelets",
         "habit": "rhizomatous", "leaves": "narrow"}),
    _species(
        "cyperus_strigosus", "Cyperus strigosus", "Straw-coloured flatsedge", "Cyperaceae", "FACW",
        "NE MW SE",
        "Narrow straw-coloured spikes in an umbel; wet meadows and shorelines.",
        {"stem": "triangular", "fruit": "achene_in_scale", "spike_count": "several_spikelets",
         "habit": "tufted", "leaves": "narrow"}),
    # --- Grasses (18) -----------------------------------------------------
    _species(
        "phragmites_australis", "Phragmites australis", "Common reed", "Poaceae", "FACW", "NE MW SE GP",
        "Tall (2-4 m) colonial reed; broad glaucous leaves; dense plume 20-40 cm.",
        {"habit": "rhizomatous", "inflorescence": "plume", "awns": "none",
         "florets": "many", "spikelet_compression": "laterally_flattened"}),
    _species(
        "phalaris_arundinacea", "Phalaris arundinacea", "Reed canarygrass", "Poaceae", "FACW",
        "NE MW SE GP WMV AK",
        "Coarse sod-forming grass; panicle contracted into a dense spike-like head 5-15 cm.",
        {"habit": "sod_forming", "inflorescence": "contracted_panicle", "awns": "none",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "calamagrostis_canadensis", "Calamagrostis canadensis", "Bluejoint", "Poaceae", "FACW",
        "NE MW AK WMV",
        "Loose purple-tinged panicle 8-20 cm nodding at the tip; lemma awned from the back.",
        {"habit": "rhizomatous", "inflorescence": "plume", "awns": "short",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "zizania_aquatica", "Zizania aquatica", "Wild rice", "Poaceae", "OBL", "NE MW",
        "Tall aquatic annual; large panicle with pistillate spikelets on the lower branches.",
        {"habit": "annual", "inflorescence": "open_panicle", "awns": "long",
         "florets": "one", "spikelet_compression": "terete"}),
    _species(
        "leersia_oryzoides", "Leersia oryzoides", "Rice cutgrass", "Poaceae", "OBL", "NE MW SE",
        "Scratchy leaf margins; laterally flattened 1-flowered spikelets; mud and shallow water.",
        {"habit": "rhizomatous", "inflorescence": "open_panicle", "awns": "none",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "glyceria_striata", "Glyceria striata", "Fowl mannagrass", "Poaceae", "OBL", "NE MW SE AK",
        "Strongly 7-nerved lemmas; spikelets 4-7 mm; open drooping panicle of wet woods.",
        {"habit": "tufted", "inflorescence": "open_panicle", "awns": "none",
         "florets": "many", "spikelet_compression": "terete"}),
    _species(
        "bromus_inermis", "Bromus inermis", "Smooth brome", "Poaceae", "UPL", "NE MW SE GP",
        "Sod-forming; awnless 5-7 nerved lemmas; narrow few-branched panicle; roadsides.",
        {"habit": "sod_forming", "inflorescence": "open_panicle", "awns": "none",
         "florets": "many", "spikelet_compression": "terete"}),
    _species(
        "bromus_tectorum", "Bromus tectorum", "Cheatgrass", "Poaceae", "NI", "NE MW GP WMV",
        "Winter annual; drooping open panicle; lemmas tipped with straight awns 8-15 mm.",
        {"habit": "annual", "inflorescence": "open_panicle", "awns": "long",
         "florets": "many", "spikelet_compression": "terete"}),
    _species(
        "agrostis_stolonifera", "Agrostis stolonifera", "Creeping bentgrass", "Poaceae", "FACW",
        "NE MW SE AK",
        "Stoloniferous turf grass; delicate diffuse panicle; awn, when present, from the back.",
        {"habit": "stoloniferous", "inflorescence": "open_panicle", "awns": "short",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "poa_pratensis", "Poa pratensis", "Kentucky bluegrass", "Poaceae", "FACU", "NE MW SE GP AK",
        "Sod-forming; 2-5 florets with cobwebby callus; pyramidal panicle; lawns and meadows.",
        {"habit": "sod_forming", "inflorescence": "open_panicle", "awns": "none",
         "florets": "two_to_five", "spikelet_compression": "laterally_flattened"}),
    _species(
        "echinochloa_crus_galli", "Echinochloa crus-galli", "Barnyard grass", "Poaceae", "FACW",
        "NE MW SE GP",
        "Coarse annual of wet fields; plano-convex spikelets 2.5-4 mm; drooping panicle.",
        {"habit": "annual", "inflorescence": "open_panicle", "awns": "short",
         "florets": "two_to_five", "spikelet_compression": "plano_convex"}),
    _species(
        "panicum_virgatum", "Panicum virgatum", "Switchgrass", "Poaceae", "FAC", "NE MW SE GP",
        "Tall clonal grass with scaly rhizomes; 2-flowered ovoid spikelets in an open panicle.",
        {"habit": "rhizomatous", "inflorescence": "open_panicle", "awns": "none",
         "florets": "two_to_five", "spikelet_compression": "terete"}),
    _species(
        "dichanthelium_clandestinum", "Dichanthelium clandestinum", "Deertongue", "Poaceae", "FACW",
        "NE MW SE",
        "Winter rosette; broad leaves; 2-flowered spikelets 3-6 mm in a terminal panicle.",
        {"habit": "rhizomatous", "inflorescence": "open_panicle", "awns": "none",
         "florets": "two_to_five", "spikelet_compression": "terete"}),
    _species(
        "spartina_pectinata", "Spartina pectinata", "Prairie cordgrass", "Poaceae", "FACW", "NE MW SE GP",
        "Sod-forming; 10-25 mm spikelets in one-sided spikes on arching stems; wet prairies.",
        {"habit": "sod_forming", "inflorescence": "panicle_of_racemes", "awns": "none",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "spartina_alterniflora", "Spartina alterniflora", "Smooth cordgrass", "Poaceae", "OBL", "NE SE",
        "Stout salt-marsh grass; 6-12 mm spikelets in narrow one-sided spikes.",
        {"habit": "rhizomatous", "inflorescence": "panicle_of_racemes", "awns": "none",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "andropogon_gerardii", "Andropogon gerardii", "Big bluestem", "Poaceae", "FACU", "NE MW SE GP",
        "Silky racemes of paired spikelets; awns 8-18 mm; tall prairie grass 1-2.5 m.",
        {"habit": "sod_forming", "inflorescence": "raceme", "awns": "long",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "schizachyrium_scoparium", "Schizachyrium scoparium", "Little bluestem", "Poaceae", "FACU",
        "NE MW SE GP",
        "Slender bunchgrass; paired spikelets on a jointed rachis; awns 10-15 mm.",
        {"habit": "tufted", "inflorescence": "raceme", "awns": "long",
         "florets": "one", "spikelet_compression": "laterally_flattened"}),
    _species(
        "sorghastrum_nutans", "Sorghastrum nutans", "Indiangrass", "Poaceae", "FACU", "NE MW SE GP",
        "Loose nodding panicle; spikelets 7-12 mm; geniculate twisted awn 5-9 mm from the middle.",
        {"habit": "tufted", "inflorescence": "open_panicle", "awns": "long",
         "florets": "two_to_five", "spikelet_compression": "laterally_flattened"}),
)


SPECIES_BY_ID = {species.id: species for species in SPECIES}

TRAITS_BY_ID = {trait.id: trait for trait in TRAITS}


def find_species(query: str, limit: int = 0) -> list:
    """Resolve a name fragment to species, best matches first.

    Exact id, exact scientific name and exact common name always win over
    substring hits so that a scripted ``--species "Carex stricta"`` is stable.
    """
    text = (query or "").strip().lower()
    if not text:
        return []
    exact, prefix, substring = [], [], []
    for species in SPECIES:
        names = (species.id.lower(), species.scientific_name.lower(), species.common_name.lower())
        if text in names:
            exact.append(species)
        elif any(name.startswith(text) for name in names):
            prefix.append(species)
        elif any(text in name for name in names):
            substring.append(species)
    matches = exact + prefix + substring
    return matches[:limit] if limit else matches


def suggest_species_names(query: str, limit: int = 3) -> list:
    """Up to ``limit`` nearest dataset names for an unknown name."""
    names = [species.scientific_name for species in SPECIES]
    return difflib.get_close_matches(query, names, n=limit, cutoff=0.35)


def species_label(species: Species) -> str:
    return f"{species.scientific_name} ({species.common_name})"


# ---------------------------------------------------------------------------
# The dichotomous key.  Keys are authored as nested literals for readability;
# ``build_key_nodes`` flattens them into the explicit node table the plan's
# data model describes (id, parent_id, choices[].next) so that step-back,
# restart and the load-time dangling-reference check are all possible.
# ---------------------------------------------------------------------------


def node(prompt, *choices):
    """Author one couplet: a prompt plus (label, next) pairs."""
    return {"prompt": prompt, "choices": [{"label": label, "next": nxt} for label, nxt in choices]}


KEY_TREES = {
    "sedge": node(
        "Is each fruit enclosed in a bottle-shaped sac (a perigynium), one per scale?",
        ("Yes - perigynia present; spikes have one perigynium per scale (genus Carex)",
         node(
             "How many spikes does the stem carry?",
             ("One spike only",
              node(
                  "The single spike and its perigynia",
                  ("Perigynia few (3-10) and appressed; stems thread-like, leafless below",
                   "carex_leptalea"),
                  ("Perigynia several and spreading; low bog plant with arching leaves",
                   "carex_pauciflora"),
              )),
             ("Two or more spikes",
              node(
                  "Are the lowest bracts long and leaf-like, overtopping a dense head of short spikes?",
                  ("Yes - flat, strongly nerved perigynia radiate from the axis; beak teeth prominent",
                   "carex_vulpinoidea"),
                  ("No - the perigynia are not radially arranged, or the spikes are separate",
                   node(
                       "Is the inflorescence still a dense head of short spikes under long bracts?",
                       ("Yes, still a dense bracted head",
                        node(
                            "Appearance of the perigynia in the head",
                            ("Ovate perigynia widest near the base, tapered to a serrate beak",
                             "carex_stipata"),
                            ("Perigynia with broad thin wing margins, flat and fan-like",
                             node(
                                 "Shape of the spikes in the head",
                                 ("Narrowly lance-ovoid spikes; head open and bristly-looking",
                                  "carex_scoparia"),
                                 ("Broad-ovoid spikes; wings narrow, beak slightly bent",
                                  "carex_alata"),
                             )),
                        )),
                       ("No - spikes separate, or bracts shorter or sheathing",
                        node(
                            "Are the pistillate (female) spikes long-stalked, drooping or widely spreading?",
                            ("Yes - pistillate spikes on long stalks, drooping or spreading",
                             node(
                                 "Perigynium beak and spikes",
                                 ("Beak long and slender (over 1 mm) with two teeth; perigynia reflexed",
                                  "carex_comosa"),
                                 ("Beak short and straight; long-cylindric spikes 4-10 cm, perigynia inflated",
                                  "carex_crinita"),
                                 ("Spikes short and stout (2-4 cm); beak strongly toothed; fens and seeps",
                                  "carex_hystericina"),
                             )),
                            ("No - pistillate spikes sessile or short-stalked and erect",
                             node(
                                 "Perigynium outline",
                                 ("Much longer than wide (lanceolate or ellipsoid) with an evident beak",
                                  node(
                                      "Vegetative characters of the clump",
                                      ("Colonial from long rhizomes; leaves 1-3 mm wide; spikes short and appressed",
                                       "carex_pensylvanica"),
                                      ("Densely clump-forming (tussock); leaves mostly 3 mm or wider",
                                       node(
                                           "Perigynium body and beak",
                                           ("Perigynia 3-5 mm with a short straight beak; 2-5 spikes; large tussocks",
                                            "carex_stricta"),
                                           ("Perigynia with a beak 0.8-2 mm and a narrower body",
                                            node(
                                                "Perigynia size and leaf width",
                                                ("Perigynia 2-3.5 mm; leaves 2-4 mm wide, glaucous; wet meadows",
                                                 "carex_aquatilis"),
                                                ("Perigynia 5-8 mm, spreading; leaves broad; standing water",
                                                 "carex_utriculata"),
                                            )),
                                       )),
                                  )),
                                 ("Broad and inflated (balloon-like), about as long as wide",
                                  node(
                                      "Pistillate spikes and perigynia",
                                      ("2-4 stout spikes 1-2 cm; perigynia 4-5.5 mm with a slender beak",
                                       "carex_lacustris"),
                                      ("1-3 spikes; perigynia 7-12 mm, the largest here, beak bent and reflexed",
                                       "carex_lupulina"),
                                      ("1-3 lax spikes; perigynia 6-9 mm, ovoid, beak short and straight",
                                       "carex_intumescens"),
                                  )),
                                 ("Plump and pale with an almost absent beak",
                                  node(
                                      "Where is the plant growing?",
                                      ("Wet to mesic limestone meadows and fens; perigynia 2.5-3.5 mm",
                                       "carex_granularis"),
                                      ("Woods and old fields; perigynia 2.5-3.5 mm in loose heads",
                                       "carex_blanda"),
                                  )),
                             )),
                        )),
                   )),
              )),
         )),
        ("No perigynia - the achene sits in the axil of a single scale (Eleocharis, "
         "Schoenoplectus, Scirpus, Dulichium, Cyperus)",
         node(
             "Flower cluster",
             ("A single terminal spikelet on a round stem, no leafy bract below it",
              node(
                  "Stem and spikelet of the single-spikelet plant",
                  ("Stems capillary, under 1 mm thick; spikelets 3-8 mm; mats on exposed mud",
                   "eleocharis_acicularis"),
                  ("Stems over 1 mm thick",
                   node(
                       "Spikelet and stem",
                       ("Spikelets 8-30 mm on stems from creeping rhizomes; achenes lenticular",
                        "eleocharis_palustris"),
                       ("Spikelets ovoid 3-8 mm on tufted stems; small tubercle on the achene",
                        "eleocharis_obtusa"),
                   )),
              )),
             ("Spikelets several, or in a cluster with leafy bracts",
              node(
                  "The bract below the spikelets (the involucre)",
                  ("Long and leaf-like, appearing as a continuation of the stem; spikelets brown and clustered",
                   node(
                       "Stem texture between thumb and finger",
                       ("Soft, crushes easily; spikelets 5-10 mm, ovoid",
                        "schoenoplectus_tabernaemontani"),
                       ("Hard and sharply triangular, resists crushing; spikelets 8-15 mm",
                        "schoenoplectus_acutus"),
                   )),
                  ("Short, or bracts several; spikelets otherwise arranged",
                   node(
                       "Spikelet arrangement along or above the stem",
                       ("In a branching, woolly-looking terminal cluster with long curly bristles",
                        "scirpus_cyperinus"),
                       ("Axillary along the stem in two ranks, or flattened spikes in an umbrella-like head",
                        node(
                            "Leaves and spikelets",
                            ("Leaves three-ranked; one spikelet per axil; acid bogs and pond margins",
                             "dulichium_arundinaceum"),
                            ("Spikelets flattened in umbel-like heads with long rays",
                             node(
                                 "Spike and achene",
                                 ("Achene 2.5-3.5 mm; spikes in a starburst head; tubers on the rhizomes",
                                  "cyperus_esculentus"),
                                 ("Achene 1.2-1.8 mm; spikes narrow and straw-coloured",
                                  "cyperus_strigosus"),
                             )),
                        )),
                   )),
              )),
         )),
    ),
    "grass": node(
        "Which inflorescence does the plant have?",
        ("Dense, plume-like or spike-like panicle with spikelets crowded on a contracted axis",
         node(
             "Size and texture of the plume",
             ("Stems 2-4 m tall with broad glaucous leaves; panicle 20-40 cm",
              "phragmites_australis"),
             ("Panicle 5-20 cm, denser and smaller",
              node(
                  "Panicle shape",
                  ("Contracted into a dense spike-like cylindrical head; sod-forming",
                   "phalaris_arundinacea"),
                  ("Loose and slightly nodding at the tip, purple-tinged; awned from the back",
                   "calamagrostis_canadensis"),
              )),
         )),
        ("Open branching panicle, or spikelets in slender spikes or racemes",
         node(
             "Are the lemmas or glumes tipped with awns longer than 3 mm?",
             ("Yes - awns longer than 3 mm",
              node(
                  "Spikelet arrangement and where the awn is attached",
                  ("Spikelets in pairs (one sessile, one pedicellate) along a jointed rachis; awns 8-25 mm",
                   node(
                       "Hairs on the rachis joints and pedicels",
                       ("Silky-hairy; sessile spikelet 6-10 mm; stout plants 1-2.5 m of prairies and roadsides",
                        "andropogon_gerardii"),
                       ("Essentially glabrous; sessile spikelet 6-8 mm; slender plants 0.5-1.2 m",
                        "schizachyrium_scoparium"),
                   )),
                  ("Awn 5-9 mm, geniculate and twisted, attached near the middle of the lemma",
                   "sorghastrum_nutans"),
                  ("Awns attached at the tip of the lemma, 5-15 mm; loose drooping panicle",
                   "bromus_tectorum"),
                  ("Awn tiny and straight (1-3 mm) from the back of the lemma; delicate diffuse panicle",
                   "agrostis_stolonifera"),
              )),
             ("No - awnless, or with tiny awns under 3 mm",
              node(
                  "Spikelet shape",
                  ("Laterally flattened, 1-flowered spikelets (glumes absent or tiny)",
                   node(
                       "Plant and lemma characters",
                       ("Leaf margins scratchy; lemma firm and bristly-hairy; wet mud and shallow water",
                        "leersia_oryzoides"),
                       ("Plants 1-3 m tall and aquatic; lower panicle branches bear pistillate spikelets",
                        "zizania_aquatica"),
                   )),
                  ("Spikelets not distinctly flattened nor single-flowered",
                   node(
                       "Florets per spikelet",
                       ("5-12 florets; cylindrical spikelets with prominently nerved lemmas",
                        node(
                            "Lemma nervation and panicle",
                            ("Lemmas strongly 7-nerved; spikelets 4-7 mm; open drooping panicle of wet woods",
                             "glyceria_striata"),
                            ("Lemmas 5-nerved; narrow few-branched panicle; roadsides and dry meadows",
                             "bromus_inermis"),
                        )),
                       ("1-5 florets",
                        node(
                            "Spikelet arrangement",
                            ("In one-sided spikes or a panicle of short one-sided racemes",
                             node(
                                 "Spikelet size and habitat",
                                 ("Spikelets 10-25 mm in spikes 5-15 cm long; sod-forming wet-prairie grass",
                                  "spartina_pectinata"),
                                 ("Spikelets 6-12 mm in narrow spikes; stout salt and brackish marsh grass",
                                  "spartina_alterniflora"),
                             )),
                            ("In an open or narrow panicle",
                             node(
                                 "Panicle and spikelet details",
                                 ("Ovoid 2-flowered spikelets 2-5 mm; first glume half the length; scaly rhizomes",
                                  "panicum_virgatum"),
                                 ("Plano-convex 2.5-4 mm spikelets, awns on the sterile lemma; annual",
                                  "echinochloa_crus_galli"),
                                 ("Broad leaves from a winter rosette; 2-flowered spikelets 3-6 mm",
                                  "dichanthelium_clandestinum"),
                                 ("2-5 florets with a cobwebby callus; pyramidal panicle; sod-forming",
                                  "poa_pratensis"),
                             )),
                        )),
                   )),
              )),
         )),
    ),
}


def build_key_nodes(trees):
    """Flatten the nested key literals into an explicit node table."""
    nodes = {}

    def walk(group, spec, node_id, parent_id, order_index):
        choices = []
        for position, choice in enumerate(spec["choices"], start=1):
            child_id = f"{node_id}.{position}"
            target = choice["next"]
            if isinstance(target, str):
                next_id = target
            else:
                next_id = child_id
                walk(group, target, child_id, node_id, position)
            choices.append(KeyChoice(label=choice["label"], next=next_id))
        nodes[node_id] = KeyNode(
            id=node_id,
            group=group,
            order_index=order_index,
            prompt=spec["prompt"],
            choices=tuple(choices),
            parent_id=parent_id,
        )

    for group in sorted(trees):
        walk(group, trees[group], group, None, 0)
    return nodes


KEY_NODES = build_key_nodes(KEY_TREES)

KEY_GROUPS = tuple(sorted(KEY_TREES))


def group_root(group: str) -> str:
    return group


def species_reachable_from(group: str) -> set:
    """Species ids that some path through ``group``'s key terminates at."""
    found = set()

    def walk(node_id, seen):
        if node_id in seen:
            return
        seen.add(node_id)
        node_obj = KEY_NODES.get(node_id)
        if node_obj is None:
            found.add(node_id)
            return
        for choice in node_obj.choices:
            walk(choice.next, seen)

    walk(group_root(group), set())
    return found


def key_depths(group: str) -> list:
    """Every root-to-leaf couplet depth in a group, as a list of ints."""
    depths = []

    def walk(node_id, depth):
        node_obj = KEY_NODES.get(node_id)
        if node_obj is None:
            depths.append(depth)
            return
        for choice in node_obj.choices:
            walk(choice.next, depth + 1)

    walk(group_root(group), 0)
    return depths


def check_dataset() -> list:
    """Return a list of integrity problems found in the embedded dataset."""
    problems = []
    seen_ids = set()
    for species in SPECIES:
        if species.id in seen_ids:
            problems.append(f"duplicate species id {species.id}")
        seen_ids.add(species.id)
        if not species.scientific_name or not species.common_name:
            problems.append(f"{species.id}: missing a scientific or common name")
        if species.family not in FAMILIES:
            problems.append(f"{species.id}: unknown family {species.family!r}")
        if species.wetland_code not in WETLAND_CODES:
            problems.append(f"{species.id}: unknown wetland code {species.wetland_code!r}")
        for region in species.regions:
            if region not in REGIONS:
                problems.append(f"{species.id}: unknown region code {region!r}")
        if not species.regions:
            problems.append(f"{species.id}: no region sublist recorded")
        for trait_id, state in species.trait_values.items():
            trait = TRAITS_BY_ID.get(trait_id)
            if trait is None:
                problems.append(f"{species.id}: unknown trait {trait_id!r}")
                continue
            if state not in trait.states:
                problems.append(f"{species.id}: {trait_id}={state!r} is not a valid state")
            family_group = "grass" if species.family == "Poaceae" else "sedge"
            if trait.applies_to not in ("both", family_group):
                problems.append(f"{species.id}: trait {trait_id!r} does not apply to {family_group}s")

    for node_id, key_node in KEY_NODES.items():
        if not key_node.prompt.strip():
            problems.append(f"couplet {node_id}: empty prompt")
        if not 2 <= len(key_node.choices) <= 4:
            problems.append(f"couplet {node_id}: {len(key_node.choices)} choices (expected 2-4)")
        if key_node.parent_id is not None and key_node.parent_id not in KEY_NODES:
            problems.append(f"couplet {node_id}: parent {key_node.parent_id} is missing")
        for choice in key_node.choices:
            if choice.next not in KEY_NODES and choice.next not in SPECIES_BY_ID:
                problems.append(f"couplet {node_id}: choice leads to unknown target {choice.next!r}")
            elif not choice.label.strip():
                problems.append(f"couplet {node_id}: choice with an empty label")

    # Every node must be reachable from its group root, so nothing is orphaned.
    for group in KEY_GROUPS:
        reachable_nodes = set()
        stack = [group_root(group)]
        while stack:
            current = stack.pop()
            if current in reachable_nodes or current not in KEY_NODES:
                continue
            reachable_nodes.add(current)
            stack.extend(choice.next for choice in KEY_NODES[current].choices)
        for node_id in KEY_NODES:
            if KEY_NODES[node_id].group == group and node_id not in reachable_nodes:
                problems.append(f"couplet {node_id}: unreachable from the {group} root")

    for group in KEY_GROUPS:
        depths = key_depths(group)
        if depths and max(depths) > MAX_COUPLET_DEPTH:
            problems.append(
                f"{group} key: a path is {max(depths)} couplets deep (limit {MAX_COUPLET_DEPTH})")
    return problems


def dangling_references() -> int:
    """Count of couplet choices that point at nothing in the dataset."""
    dangling = 0
    for key_node in KEY_NODES.values():
        for choice in key_node.choices:
            if choice.next not in KEY_NODES and choice.next not in SPECIES_BY_ID:
                dangling += 1
    return dangling


def dataset_report() -> dict:
    """Numbers behind ``data --check``."""
    by_family = {}
    by_code = {}
    for species in SPECIES:
        by_family[species.family] = by_family.get(species.family, 0) + 1
        by_code[species.wetland_code] = by_code.get(species.wetland_code, 0) + 1
    couplets = {group: sum(1 for n in KEY_NODES.values() if n.group == group)
                for group in KEY_GROUPS}
    keyed = set()
    depths = {}
    for group in KEY_GROUPS:
        keyed |= species_reachable_from(group)
        group_depths = key_depths(group)
        depths[group] = max(group_depths) if group_depths else 0
    return {
        "dataset_version": DATASET_VERSION,
        "species_total": len(SPECIES),
        "by_family": by_family,
        "by_code": by_code,
        "couplets": couplets,
        "couplet_total": sum(couplets.values()),
        "max_depth": depths,
        "dangling_references": dangling_references(),
        "keyed_species": len([s for s in SPECIES if s.id in keyed]),
        "unkeyed_species": sorted(s.id for s in SPECIES if s.id not in keyed),
        "problems": check_dataset(),
    }


DATA_PROBLEMS = dataset_report()["problems"]


def key_available(group: str) -> tuple:
    """Is this group's key usable?  Returns (ok, reason)."""
    for problem in DATA_PROBLEMS:
        if problem.startswith(f"{group} key") or f"couplet {group}" in problem:
            return False, problem
    if group not in KEY_TREES:
        return False, f"no key is bundled for group {group!r}"
    return True, ""


# ---------------------------------------------------------------------------
# Terminal presentation.  Terminal-dark, monospace, aligned columns, one accent
# for the primary status (the wetland code).  Colour is off unless stdout is a
# terminal, and NO_COLOR / --no-color always win.  Separators fall back to ASCII
# when the terminal encoding cannot carry box-drawing characters.
# ---------------------------------------------------------------------------

ACCENT_RGB = (0, 140, 161)  # #008ca1 -- the committed project accent


def _detect_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or ""
    return "utf" in encoding.lower()


UNICODE_OK = _detect_unicode()
HBAR = "\u2500" if UNICODE_OK else "-"
USE_COLOR = None  # None = decide from the stream, else True/False


def colors_on() -> bool:
    global USE_COLOR
    if USE_COLOR is None:
        USE_COLOR = bool(getattr(sys.stdout, "isatty", lambda: False)()) and \
            os.environ.get("NO_COLOR") is None and os.environ.get("TERM") != "dumb"
    return USE_COLOR


def accent(text: str) -> str:
    if not colors_on():
        return text
    r, g, b = ACCENT_RGB
    return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"


ANSI_PATTERN = re.compile("\\x1b\\[[0-9;]*m")


def visible_length(text: str) -> int:
    """Length of a line as the terminal sees it, ignoring ANSI colour codes."""
    return len(ANSI_PATTERN.sub("", str(text)))


def emit(text: str = "") -> None:
    """Print a line, wrapping prose to the 80-column budget."""
    for line in str(text).splitlines() or [""]:
        if visible_length(line) <= OUTPUT_WIDTH:
            print(line)
            continue
        plain = ANSI_PATTERN.sub("", line)
        indent = plain[:len(plain) - len(plain.lstrip())]
        print(textwrap.fill(plain, width=OUTPUT_WIDTH, subsequent_indent=indent + "  ",
                            break_long_words=False, break_on_hyphens=False))


def rule(title: str = "") -> str:
    if not title:
        return HBAR * OUTPUT_WIDTH
    label = f" {title} "
    left = 2
    right = max(0, OUTPUT_WIDTH - left - len(label) - 1)
    return HBAR * left + label + HBAR * right


def print_table(headers, rows, aligns=None) -> None:
    aligns = aligns or ["<"] * len(headers)
    columns = len(headers)
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for index in range(columns):
            widths[index] = max(widths[index], len(str(row[index])))
    gap = 2
    while sum(widths) + gap * (columns - 1) > OUTPUT_WIDTH:
        widest = max(range(columns), key=lambda i: widths[i])
        if widths[widest] <= 8:
            break
        widths[widest] -= 1

    def format_row(cells):
        parts = []
        for index, cell in enumerate(cells):
            text = str(cell)
            if len(text) > widths[index]:
                text = text[:max(0, widths[index] - 2)] + ".."
            parts.append(f"{text:{aligns[index]}{widths[index]}}")
        return (" " * gap).join(parts).rstrip()

    emit(format_row(headers))
    emit(HBAR * min(OUTPUT_WIDTH, sum(widths) + gap * (columns - 1)))
    for row in rows:
        emit(format_row(row))


def humanize_state(state: str) -> str:
    return str(state).replace("_", " ")


def banner() -> str:
    return accent(f"SedgeID {VERSION}") + \
        f"  sedge & grass identification  (dataset {DATASET_VERSION})"


# ---------------------------------------------------------------------------
# Local observer config (.sedgeid_config.json in the working directory).
# ---------------------------------------------------------------------------

def config_path() -> str:
    return os.path.join(os.getcwd(), CONFIG_FILE)


def tally_path() -> str:
    return os.path.join(os.getcwd(), TALLY_FILE)


def load_config() -> tuple:
    """Return (config, note).  A missing or unreadable file is never fatal."""
    path = config_path()
    if not os.path.exists(path):
        return LocalConfig(), ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json_loads(handle.read())  # JSON only; nothing executable is parsed
    except (OSError, ValueError) as error:
        return LocalConfig(), (f"note: {CONFIG_FILE} could not be read "
                               f"({error.__class__.__name__}); observer left blank")
    if not isinstance(raw, dict):
        return LocalConfig(), f"note: {CONFIG_FILE} does not hold an object; observer left blank"
    config = LocalConfig(
        observer=str(raw.get("observer", "") or ""),
        default_site=str(raw.get("default_site", "") or ""),
        default_region=str(raw.get("default_region", "") or ""),
        dataset_version=str(raw.get("dataset_version", DATASET_VERSION)),
        created_at=str(raw.get("created_at", "") or ""),
    )
    return config, ""


def save_config(config: LocalConfig) -> tuple:
    """Write the config.  Returns (ok, note) -- never raises."""
    if not config.created_at:
        config.created_at = date.today().isoformat()
    payload = {
        "observer": config.observer,
        "default_site": config.default_site,
        "default_region": config.default_region,
        "dataset_version": DATASET_VERSION,
        "created_at": config.created_at,
    }
    try:
        with open(config_path(), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as error:
        return False, (f"warning: could not save {CONFIG_FILE} in this directory "
                       f"({error.strerror or error.__class__.__name__}); the observer stays "
                       "blank but the tally row is still written")
    return True, ""


def read_line(prompt_text: str):
    """Read one line.  Returns None on EOF / Ctrl-D / Ctrl-C."""
    try:
        return input(prompt_text)
    except (EOFError, KeyboardInterrupt):
        print("")
        return None


def ensure_observer(interactive: bool) -> tuple:
    """Return (observer, notes).  Prompts once on first use of a terminal."""
    config, note = load_config()
    notes = [note] if note else []
    if config.observer:
        return config.observer, notes
    if interactive:
        typed = read_line("Observer name or initials (saved to "
                          f"{CONFIG_FILE} for this directory): ")
        if typed is not None and typed.strip():
            config.observer = typed.strip()
            ok, save_note = save_config(config)
            if not ok:
                notes.append(save_note)
                return "", notes
            notes.append(f"observer saved to {CONFIG_FILE}")
            return config.observer, notes
    notes.append("note: no observer recorded yet -- set one with "
                 "'sedgeid.py me --set \"Your Name\"'")
    return "", notes


# ---------------------------------------------------------------------------
# Tally sheet: append-only CSV in the working directory.
# ---------------------------------------------------------------------------

def read_tally(path: str = None) -> tuple:
    """Return (entries, skipped_rows).  A missing file is an empty sheet."""
    path = path or tally_path()
    if not os.path.exists(path):
        return [], 0
    entries, skipped = [], 0
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return [], 0
            for row in reader:
                try:
                    count = int(str(row.get("count", "")).strip())
                    if count < 1:
                        raise ValueError("count")
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                entries.append(TallyEntry(
                    entry_id=str(row.get("entry_id", "") or ""),
                    observed_on=str(row.get("observed_on", "") or ""),
                    observer=str(row.get("observer", "") or ""),
                    species_id=str(row.get("species_id", "") or ""),
                    species_name=str(row.get("species_name", "") or ""),
                    count=count,
                    site=str(row.get("site", "") or ""),
                    region=str(row.get("region", "") or ""),
                    method=str(row.get("method", "") or ""),
                    note=str(row.get("note", "") or ""),
                ))
    except OSError as error:
        raise OSError(f"{path}: {error.strerror or error}") from error
    return entries, skipped


def entry_row(entry: TallyEntry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "observed_on": entry.observed_on,
        "observer": entry.observer,
        "species_id": entry.species_id,
        "species_name": entry.species_name,
        "count": entry.count,
        "site": entry.site,
        "region": entry.region,
        "method": entry.method,
        "note": entry.note,
    }


def next_entry_id(entries) -> str:
    highest = 0
    for entry in entries:
        digits = "".join(ch for ch in entry.entry_id if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"E{highest + 1:04d}"


def append_entry(entry: TallyEntry, path: str = None) -> tuple:
    """Append one row, writing the header the first time.  Returns (path, total)."""
    path = path or tally_path()
    needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
    try:
        with open(path, "a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
            if needs_header:
                writer.writeheader()
            writer.writerow(entry_row(entry))
    except OSError as error:
        raise OSError(f"{path}: {error.strerror or error}") from error
    entries, _ = read_tally(path)
    return path, len(entries)


def undo_last(path: str = None) -> tuple:
    """Drop the last appended row by rewriting the header plus the survivors."""
    path = path or tally_path()
    entries, _ = read_tally(path)
    if not entries:
        return None, 0
    removed = entries[-1]
    survivors = entries[:-1]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for entry in survivors:
            writer.writerow(entry_row(entry))
    return removed, len(survivors)


# ---------------------------------------------------------------------------
# Rendering helpers for species.
# ---------------------------------------------------------------------------

def species_rows(species_list) -> list:
    return [[item.scientific_name, item.common_name, item.family, item.wetland_code]
            for item in species_list]


def print_species_list(species_list, heading: str = "") -> None:
    if heading:
        emit(heading)
    print_table(["Scientific name", "Common name", "Family", "Code"],
                species_rows(species_list))


def print_species_card(species: Species) -> None:
    emit(rule(species.scientific_name))
    emit(f"  {species.scientific_name}  ({species.common_name})")
    emit(f"  Family      {species.family}   genus {species.genus}")
    emit(f"  Wetland     {accent(species.wetland_code)} - {WETLAND_CODES[species.wetland_code]}")
    region_names = ", ".join(f"{code} ({REGIONS[code]})" for code in species.regions)
    emit(f"  Regions     {region_names}")
    emit(f"  Diagnostic  {species.key_note}")
    trait_rows = []
    for trait_id, state in species.trait_values.items():
        trait = TRAITS_BY_ID.get(trait_id)
        if trait is not None:
            trait_rows.append([trait.label, humanize_state(state)])
    if trait_rows:
        emit("  Traits")
        print_table(["Character", "State recorded"], trait_rows)
    emit(f"  Dataset id  {species.id}")


def print_region_legend() -> None:
    emit("Region sublists (National Wetland Plant List)")
    print_table(["Code", "Region"], [[code, REGIONS[code]] for code in REGIONS])


def print_code_table() -> None:
    print_table(["Code", "Meaning"],
                [[code, WETLAND_CODES[code]] for code in WETLAND_CODES])


# ---------------------------------------------------------------------------
# The key engine.
# ---------------------------------------------------------------------------

class KeySession:
    """One in-memory walk of a group's key."""

    def __init__(self, group: str):
        self.group = group
        self.current_node_id = group_root(group)
        self.trail = []
        self.resolved_species_id = None

    def current_node(self):
        return KEY_NODES[self.current_node_id]

    @property
    def couplet_number(self) -> int:
        return len(self.trail) + 1

    def restart(self) -> None:
        self.current_node_id = group_root(self.group)
        self.trail = []
        self.resolved_species_id = None

    def answer(self, token: str) -> str:
        """Apply one answer.  Returns node|species|invalid|back|restart|quit."""
        text = (token or "").strip().lower()
        if text in ("q", "quit"):
            return "quit"
        if text in ("r", "restart"):
            self.restart()
            return "restart"
        if text in ("b", "back"):
            if not self.trail:
                return "invalid"
            step = self.trail.pop()
            self.current_node_id = step["node_id"]
            self.resolved_species_id = None
            return "back"
        node = self.current_node()
        if text.isdigit() and 1 <= int(text) <= len(node.choices):
            choice = node.choices[int(text) - 1]
            self.trail.append({"couplet_id": node.id, "node_id": node.id, "label": choice.label})
            if choice.next in KEY_NODES:
                self.current_node_id = choice.next
                return "node"
            self.resolved_species_id = choice.next
            return "species"
        return "invalid"


def print_couplet(session: KeySession) -> None:
    node = session.current_node()
    emit("")
    emit(rule(f"{session.group.capitalize()} key - couplet {session.couplet_number}"))
    if session.trail:
        trail = " > ".join(step["label"] for step in session.trail[-2:])
        emit(textwrap.fill(f"Trail: {trail}", width=OUTPUT_WIDTH, initial_indent="  ",
                           subsequent_indent="    ", break_long_words=False,
                           break_on_hyphens=False))
    emit(textwrap.fill(node.prompt, width=OUTPUT_WIDTH, initial_indent="  ",
                       subsequent_indent="  ", break_long_words=False,
                       break_on_hyphens=False))
    for index, choice in enumerate(node.choices, start=1):
        emit(textwrap.fill(choice.label, width=OUTPUT_WIDTH - 6,
                           initial_indent=f"   {index}) ", subsequent_indent="      ",
                           break_long_words=False, break_on_hyphens=False))
    emit("   b) back    r) restart    q) quit")


def print_result_card(species: Species, trail) -> None:
    emit("")
    emit(rule("Identified"))
    emit(f"  {accent(species.scientific_name)}  ({species.common_name})")
    emit(f"  Family      {species.family}   genus {species.genus}")
    emit(f"  Wetland     {accent(species.wetland_code)} - {WETLAND_CODES[species.wetland_code]}")
    emit(f"  Regions     {', '.join(species.regions)}")
    emit(f"  Diagnostic  {species.key_note}")
    if trail:
        emit("  Trail")
        for index, step in enumerate(trail, start=1):
            emit(textwrap.fill(f"{index}. {step['label']}", width=OUTPUT_WIDTH,
                               initial_indent="    ", subsequent_indent="       ",
                               break_long_words=False, break_on_hyphens=False))


def screen_group():
    """Two screening questions for the 'not sure' start."""
    emit("")
    emit(rule("Not sure? Two screening questions"))
    emit("  Grasses have a ligule - a thin collar or membrane where the leaf blade")
    emit("  meets the sheath.  Sedges have a solid stem and leaves in three ranks.")
    first = read_line("  Do you see a ligule at the blade/sheath junction? (y/n, q to quit): ")
    if first is None or first.strip().lower() in ("q", "quit"):
        return None
    if first.strip().lower().startswith("y"):
        emit("  Screening says: grass (Poaceae).")
        return "grass"
    second = read_line("  Are the leaves three-ranked and the stem solid, often triangular? (y/n): ")
    if second is None or second.strip().lower() in ("q", "quit"):
        return None
    if second.strip().lower().startswith("y"):
        emit("  Screening says: sedge (Cyperaceae).")
        return "sedge"
    emit("  Screening is inconclusive - starting with the sedge key; press 'r' there to")
    emit("  restart in the grass key if it does not fit.")
    return "sedge"


def run_key_scripted(group: str, tokens) -> int:
    session = KeySession(group)
    for token in tokens:
        result = session.answer(token)
        if result == "species":
            species = SPECIES_BY_ID.get(session.resolved_species_id)
            if species is None:
                emit(rule("Result unavailable"))
                emit("  The key leaf points at a species missing from the dataset.")
                emit("  Re-run the couplets or check 'sedgeid.py data --check'.")
                return EXIT_NO_MATCH
            print_result_card(species, session.trail)
            emit("")
            emit(f"  {session.group.capitalize()} key resolved after {len(session.trail)} "
                 "couplets.")
            return EXIT_OK
        if result == "quit":
            emit("  Key ended - nothing was written to the tally sheet.")
            return EXIT_OK
        if result in ("restart", "back"):
            emit(f"  '{token.strip()}' is an interactive key control; it cannot be used "
                 "in a scripted run.")
            print_couplet(session)
            return EXIT_USAGE
        if result == "invalid":
            node = session.current_node()
            emit(f"  '{token.strip()}' is not one of the offered choices "
                 f"(1-{len(node.choices)}); the answer trail stopped here.")
            print_couplet(session)
            return EXIT_USAGE
    emit("")
    emit("  The answer trail ended before a species resolved.")
    print_couplet(session)
    return EXIT_NO_MATCH


def run_key_interactive(group: str) -> int:
    """Walk the key at a terminal.  Returns an exit code."""
    session = KeySession(group)
    while True:
        print_couplet(session)
        token = read_line("  Choice: ")
        if token is None:
            return EXIT_OK
        result = session.answer(token)
        if result == "quit":
            emit("  Key ended - nothing was written to the tally sheet.")
            return EXIT_OK
        if result == "restart":
            emit(f"  Restarted at couplet 1 of the {group} key with an empty trail.")
            continue
        if result == "back":
            emit("  Stepped back one couplet; the earlier answer is shown as the trail.")
            continue
        if result == "invalid":
            node = session.current_node()
            emit(f"  Enter a number 1-{len(node.choices)}, or b to go back, "
                 "r to restart, q to quit.")
            continue
        if result == "species":
            species = SPECIES_BY_ID.get(session.resolved_species_id)
            if species is None:
                emit(rule("Result unavailable"))
                emit("  The key leaf points at a species missing from the dataset; "
                     "press r to retry.")
                continue
            print_result_card(species, session.trail)
            action = read_line("  a) add to tally   c) species card   r) key again   "
                               "m) menu: ")
            if action is None:
                return EXIT_OK
            choice_token = action.strip().lower()
            if choice_token == "a":
                interactive_tally_add(prefill=species)
                return EXIT_OK
            if choice_token == "c":
                print_species_card(species)
                read_line("  Press Enter to continue: ")
                return EXIT_OK
            if choice_token == "r":
                session.restart()
                continue
            return EXIT_OK


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------

def command_identify(args) -> int:
    group = args.group
    if group is None:
        if not sys.stdin.isatty():
            emit("error: --group sedge|grass is required when input is not a terminal")
            emit("usage: sedgeid.py identify --group sedge [--answers 1,2,1]")
            return EXIT_USAGE
        emit("Which group are you keying?")
        emit("  1) sedge   2) grass   3) not sure")
        token = read_line("  Group (1-3, q to quit): ")
        if token is None:
            return EXIT_OK
        answer = token.strip().lower()
        if answer in ("q", "quit"):
            return EXIT_OK
        if answer in ("1", "sedge", "s"):
            group = "sedge"
        elif answer in ("2", "grass", "g"):
            group = "grass"
        elif answer in ("3", "not sure", "n", "?"):
            group = screen_group()
            if group is None:
                return EXIT_OK
        else:
            emit("  Enter 1 for sedge, 2 for grass, or 3 if you are not sure.")
            return EXIT_USAGE

    ok, reason = key_available(group)
    if not ok:
        emit(f"error: the {group} key is disabled - {reason}")
        return EXIT_USAGE

    if args.answers is not None:
        tokens = [part for part in args.answers.replace(",", " ").split() if part]
        return run_key_scripted(group, tokens)
    if not sys.stdin.isatty():
        tokens = sys.stdin.read().split()
        return run_key_scripted(group, tokens)
    return run_key_interactive(group)


def command_wetland(args) -> int:
    if args.species:
        matches = find_species(args.species)
        if not matches:
            emit(f"no match for '{args.species}' in the bundled dataset.")
            suggestions = suggest_species_names(args.species)
            if suggestions:
                emit("closest dataset names: " + "; ".join(suggestions))
            emit("SedgeID bundles a starter subset only - absence here is not evidence")
            emit("about a plant's indicator status. Try: sedgeid.py species --query <text>")
            return EXIT_NO_MATCH
        for index, species in enumerate(matches[:8]):
            if index:
                emit("")
            emit(rule(species.scientific_name))
            emit(f"  {species.scientific_name}  ({species.common_name}) - {species.family}")
            emit(f"  National status  {accent(species.wetland_code)}")
            emit(f"  Meaning          {WETLAND_CODES[species.wetland_code]}")
            emit("  Regional sublists")
            print_table(["Code", "Region"], [[code, REGIONS[code]] for code in species.regions])
        if len(matches) > 8:
            emit(f"  (+{len(matches) - 8} further matches - narrow the name)")
        return EXIT_OK

    if args.code:
        code = args.code.upper()
        hits = [species for species in SPECIES if species.wetland_code == code]
        emit(rule(f"{code} - {WETLAND_CODES[code]}"))
        if not hits:
            emit(f"no bundled species carry {code}.")
            return EXIT_NO_MATCH
        print_table(["Scientific name", "Family", "Regions"],
                    [[s.scientific_name, s.family, ", ".join(s.regions)] for s in hits])
        emit(f"  {len(hits)} bundled species carry {code}.")
        return EXIT_OK

    emit(rule("National wetland indicator status"))
    print_code_table()
    emit("")
    print_region_legend()
    emit("")
    emit("Look up one species:   sedgeid.py wetland --species \"Carex stricta\"")
    emit("List a code's species: sedgeid.py wetland --code OBL")
    return EXIT_OK


def command_species(args) -> int:
    if args.name or args.id:
        lookup = args.id or args.name
        matches = find_species(lookup)
        if not matches:
            emit(f"no match for '{lookup}' in the bundled dataset.")
            suggestions = suggest_species_names(lookup)
            if suggestions:
                emit("closest dataset names: " + "; ".join(suggestions))
            return EXIT_NO_MATCH
        for index, species in enumerate(matches[:5]):
            if index:
                emit("")
            print_species_card(species)
        if len(matches) > 5:
            emit(f"  (+{len(matches) - 5} further matches - narrow the name)")
        return EXIT_OK

    query = args.query or ""
    code = args.code.upper() if args.code else None
    results = []
    for species in SPECIES:
        if query and not (query.lower() in species.scientific_name.lower()
                          or query.lower() in species.common_name.lower()):
            continue
        if args.family and species.family != args.family:
            continue
        if code and species.wetland_code != code:
            continue
        results.append(species)

    if args.json:
        emit(json.dumps([{
            "id": s.id, "scientific_name": s.scientific_name, "common_name": s.common_name,
            "family": s.family, "genus": s.genus, "wetland_code": s.wetland_code,
            "regions": list(s.regions),
        } for s in results], indent=2))
        return EXIT_OK if results else EXIT_NO_MATCH

    filters = []
    if query:
        filters.append(f"name contains \"{query}\"")
    if args.family:
        filters.append(f"family {args.family}")
    if code:
        filters.append(f"code {code}")
    heading = "Filters: " + ", ".join(filters) if filters else "All bundled species"
    if not results:
        emit(f"no matches ({heading}).")
        emit("Filter syntax: sedgeid.py species [--query TEXT] "
             "[--family Cyperaceae|Poaceae] [--code OBL|FACW|FAC|FACU|UPL|NI]")
        emit("Open a detail card: sedgeid.py species \"Carex stricta\"")
        return EXIT_NO_MATCH
    emit(f"{len(results)} species match ({heading}).")
    print_species_list(results)
    emit("Detail card:  sedgeid.py species \"<scientific name>\"")
    return EXIT_OK


def _resolve_species_or_none(name: str):
    matches = find_species(name)
    return matches[0] if matches else None


def build_entry(species: Species, count: int, site: str, observed_on: str,
                region: str, method: str, note: str, observer: str,
                entries) -> TallyEntry:
    return TallyEntry(
        entry_id=next_entry_id(entries),
        observed_on=observed_on,
        observer=observer,
        species_id=species.id,
        species_name=species.scientific_name,
        count=count,
        site=site,
        region=region,
        method=method,
        note=note,
    )


def _validate_count(raw) -> tuple:
    try:
        count = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, "the count must be a positive whole number (for example --count 3)"
    if count < 1:
        return None, "the count must be a positive whole number (for example --count 3)"
    return count, ""


def _validate_date(raw: str) -> tuple:
    text = (raw or "").strip()
    if not text:
        return date.today().isoformat(), ""
    try:
        return date.fromisoformat(text).isoformat(), ""
    except ValueError:
        return None, "the date must be ISO YYYY-MM-DD (for example --date 2025-06-14)"


def _pick_by_number(ordered, token: str):
    if not token.isdigit() or not 1 <= int(token) <= len(ordered):
        emit("  That is not one of the numbers shown; nothing was written.")
        return None
    return ordered[int(token) - 1]


def interactive_pick_species():
    """Numbered dataset picker used when no species was given at a terminal."""
    emit("")
    emit(rule("Pick a species"))
    ordered = sorted(SPECIES, key=lambda s: s.scientific_name)
    page_size = 15
    for page in range(0, len(ordered), page_size):
        chunk = ordered[page:page + page_size]
        for offset, species in enumerate(chunk, start=page + 1):
            emit(f"  {offset:>3}) {species.scientific_name} ({species.common_name}) "
                 f"[{species.family[:5]}, {species.wetland_code}]")
        if page + page_size < len(ordered):
            more = read_line("  Enter for more, a number to choose, q to cancel: ")
            if more is None:
                return None
            if more.strip().lower() in ("q", "quit"):
                return None
            if more.strip():
                return _pick_by_number(ordered, more.strip())
    token = read_line("  Species number (q to cancel): ")
    if token is None or token.strip().lower() in ("q", "quit", ""):
        return None
    return _pick_by_number(ordered, token.strip())


def interactive_tally_add(prefill=None) -> int:
    """Add-to-tally prompt, pre-filled from the identification result card."""
    species = prefill
    if species is None:
        typed = read_line("  Species name (or Enter to pick from the list): ")
        if typed is None:
            return EXIT_OK
        if not typed.strip():
            species = interactive_pick_species()
        else:
            species = _resolve_species_or_none(typed.strip())
            if species is None:
                emit(f"  no match for '{typed.strip()}'; nothing was written.")
                return EXIT_NO_MATCH
    if species is None:
        return EXIT_OK

    emit(f"  Recording {species.scientific_name} ({species.common_name}), "
         f"{accent(species.wetland_code)}")
    entries, _ = read_tally()
    config, config_note = load_config()
    notes = [config_note] if config_note else []
    observer = config.observer
    if not observer:
        observer, extra = ensure_observer(True)
        notes.extend(extra)

    typed = read_line("  Count (positive whole number, default 1): ")
    if typed is None:
        return EXIT_OK
    count, message = _validate_count(typed if typed.strip() else "1")
    if count is None:
        emit(f"  {message}; nothing was written.")
        return EXIT_USAGE

    default_site = config.default_site
    typed = read_line("  Site or plot label"
                      f"{f' [{default_site}]' if default_site else ''}: ")
    if typed is None:
        return EXIT_OK
    site = typed.strip() or default_site

    typed = read_line(f"  Date [{date.today().isoformat()}]: ")
    if typed is None:
        return EXIT_OK
    observed_on, date_error = _validate_date(typed)
    if observed_on is None:
        emit(f"  {date_error}; nothing was written.")
        return EXIT_USAGE

    typed = read_line("  Note (optional, Enter to skip): ")
    if typed is None:
        return EXIT_OK
    note = typed.strip()

    entry = build_entry(species, count, site, observed_on, "", "", note, observer, entries)
    try:
        path, total = append_entry(entry)
    except OSError as error:
        emit(f"  error: could not write the tally sheet - {error}")
        emit("  Your entry was not lost:")
        emit(f"  {entry.observed_on}  {entry.species_name}  count {entry.count}  "
             f"site {entry.site or '-'}")
        return EXIT_IO_ERROR
    for line in notes:
        emit(f"  {line}")
    emit(f"  added {entry.entry_id}: {entry.species_name} x{entry.count}; "
         f"{path} now holds {total} rows.")
    return EXIT_OK


def command_tally_add(args) -> int:
    interactive = sys.stdin.isatty() and not args.species
    entries, _ = read_tally()

    species = None
    if args.species:
        species = _resolve_species_or_none(args.species)
        if species is None:
            emit(f"no match for species '{args.species}' in the bundled dataset.")
            suggestions = suggest_species_names(args.species)
            if suggestions:
                emit("closest dataset names: " + "; ".join(suggestions))
            emit("List the dataset with: sedgeid.py species")
            return EXIT_NO_MATCH
    elif interactive:
        species = interactive_pick_species()
        if species is None:
            return EXIT_OK
    else:
        emit("error: a species is required when input is not a terminal")
        emit("usage: sedgeid.py tally add \"Carex stricta\" --count 3 --site \"Plot A\"")
        return EXIT_USAGE

    config, config_note = load_config()
    notes = [config_note] if config_note else []
    observer = config.observer
    if not observer:
        observer, extra = ensure_observer(interactive)
        notes.extend(extra)

    count = args.count
    if count is None:
        if not interactive:
            emit("error: --count is required when input is not a terminal")
            return EXIT_USAGE
        typed = read_line("  Count (positive whole number, default 1): ")
        if typed is None:
            return EXIT_OK
        count, message = _validate_count(typed if typed.strip() else "1")
    else:
        count, message = _validate_count(count)
    if count is None:
        emit(f"error: {message}; nothing was written.")
        return EXIT_USAGE

    observed_on, date_error = _validate_date(args.date or "")
    if observed_on is None:
        emit(f"error: {date_error}; nothing was written.")
        return EXIT_USAGE

    region = (args.region or config.default_region or "").strip().upper()
    if region and region not in REGIONS:
        emit(f"error: unknown region {region!r}; use one of {', '.join(REGIONS)}.")
        return EXIT_USAGE

    method = (args.method or ("key" if args.from_key else "")).strip().lower()
    if method and method not in ("visual", "key", "voucher"):
        emit("error: --method must be visual, key or voucher.")
        return EXIT_USAGE

    site = args.site
    if site is None and interactive:
        default_site = config.default_site
        typed = read_line("  Site or plot label"
                          f"{f' [{default_site}]' if default_site else ''}: ")
        if typed is None:
            return EXIT_OK
        site = typed.strip() or default_site
    site = (site or config.default_site or "").strip()

    note = args.note or ""
    if interactive and not note:
        typed = read_line("  Note (optional, Enter to skip): ")
        if typed is None:
            return EXIT_OK
        note = typed.strip()

    entry = build_entry(species, count, site, observed_on, region, method, note,
                        observer, entries)
    try:
        path, total = append_entry(entry)
    except OSError as error:
        emit(f"error: could not write the tally sheet - {error}")
        emit("  The row was not lost: copy the entry below into a writable directory.")
        emit(f"  {entry.observed_on}  {entry.species_name}  count {entry.count}  "
             f"site {entry.site or '-'}  observer {entry.observer or '-'}")
        return EXIT_IO_ERROR

    for line in notes:
        emit(line)
    emit(f"added {entry.entry_id}: {entry.species_name} x{entry.count}"
         + (f" at {entry.site}" if entry.site else "")
         + f" on {entry.observed_on}"
         + (f" observer {entry.observer}" if entry.observer else " (no observer recorded)"))
    emit(f"tally sheet {path} now holds {total} row{'s' if total != 1 else ''}.")
    return EXIT_OK


def command_tally_list(args) -> int:
    entries, skipped = read_tally()
    if args.site:
        entries = [entry for entry in entries if entry.site.lower() == args.site.lower()]
    if args.since:
        try:
            cutoff = date.fromisoformat(args.since).isoformat()
        except ValueError:
            emit("error: --since must be ISO YYYY-MM-DD.")
            return EXIT_USAGE
        entries = [entry for entry in entries if entry.observed_on >= cutoff]
    if skipped:
        emit(f"note: skipped {skipped} malformed row{'s' if skipped != 1 else ''} "
             "in the tally sheet.")
    if not entries:
        emit(f"no tally rows yet in {tally_path()}.")
        emit("add the first row with:  sedgeid.py tally add \"Carex stricta\" "
             "--count 1 --site \"Plot A\"")
        return EXIT_NO_MATCH

    rows = [[entry.entry_id, entry.observed_on, entry.species_name[:32], entry.count,
             entry.site[:16] or "-", entry.region[:5] or "-",
             entry.observer[:14] or "-"] for entry in entries]
    emit(f"tally sheet {tally_path()}")
    print_table(["Id", "Date", "Species", "N", "Site", "Rgn", "Observer"], rows,
                aligns=["<", "<", "<", ">", "<", "<", "<"])
    total = sum(entry.count for entry in entries)
    emit(f"{len(entries)} row{'s' if len(entries) != 1 else ''}, "
         f"{total} plant{'s' if total != 1 else ''} in total.")
    return EXIT_OK


def command_tally_undo(args) -> int:
    try:
        removed, remaining = undo_last()
    except OSError as error:
        emit(f"error: could not rewrite the tally sheet - {error}")
        return EXIT_IO_ERROR
    if removed is None:
        emit(f"the tally sheet {tally_path()} has no rows to undo.")
        return EXIT_NO_MATCH
    emit(f"removed {removed.entry_id}: {removed.species_name} x{removed.count} "
         f"from {removed.observed_on}.")
    emit(f"{tally_path()} now holds {remaining} row{'s' if remaining != 1 else ''}.")
    return EXIT_OK


def _entry_code(entry: TallyEntry) -> str:
    species = SPECIES_BY_ID.get(entry.species_id)
    if species is not None:
        return species.wetland_code
    return entry.region or "unknown"


def summary_tables(entries) -> list:
    """Three count tables: (title, headers, rows)."""
    species_counts, site_counts, code_counts = {}, {}, {}
    species_rows_count, site_rows_count, code_rows_count = {}, {}, {}
    for entry in entries:
        species_counts[entry.species_name] = species_counts.get(entry.species_name, 0) + entry.count
        species_rows_count[entry.species_name] = species_rows_count.get(entry.species_name, 0) + 1
        site = entry.site or "(no site)"
        site_counts[site] = site_counts.get(site, 0) + entry.count
        site_rows_count[site] = site_rows_count.get(site, 0) + 1
        code = _entry_code(entry)
        code_counts[code] = code_counts.get(code, 0) + entry.count
        code_rows_count[code] = code_rows_count.get(code, 0) + 1

    def ordered(counts):
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    return [
        ("Rows per species", ["Species", "Rows", "Plants"],
         [[name, species_rows_count[name], count] for name, count in ordered(species_counts)]),
        ("Rows per site", ["Site", "Rows", "Plants"],
         [[name, site_rows_count[name], count] for name, count in ordered(site_counts)]),
        ("Rows per wetland code", ["Code", "Rows", "Plants"],
         [[name, code_rows_count[name], count] for name, count in ordered(code_counts)]),
    ]


def command_tally_summary(args) -> int:
    entries, skipped = read_tally()
    if not entries:
        emit(f"no tally rows yet in {tally_path()}.")
        emit("add the first row with:  sedgeid.py tally add \"Carex stricta\" "
             "--count 1 --site \"Plot A\"")
        return EXIT_OK
    if skipped:
        emit(f"note: skipped {skipped} malformed row{'s' if skipped != 1 else ''} "
             "in the tally sheet.")
    tables = summary_tables(entries)
    total_rows = len(entries)
    total_plants = sum(entry.count for entry in entries)
    emit(rule("Tally summary"))
    emit(f"  {total_rows} row{'s' if total_rows != 1 else ''}, "
         f"{total_plants} plant{'s' if total_plants != 1 else ''}, from {tally_path()}")
    for title, headers, rows in tables:
        emit("")
        emit(f"  {title}")
        print_table(headers, rows, aligns=["<", ">", ">"])

    if args.csv:
        try:
            with open(args.csv, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["section", "label", "rows", "plants"])
                for title, headers, rows in tables:
                    for row in rows:
                        writer.writerow([title, row[0], row[1], row[2]])
        except OSError as error:
            emit(f"error: could not write {args.csv} - {error}")
            return EXIT_IO_ERROR
        emit("")
        emit(f"summary written to {os.path.abspath(args.csv)}")
    return EXIT_OK


def command_me(args) -> int:
    config, note = load_config()
    if args.set or args.site is not None or args.region is not None:
        if args.set:
            config.observer = args.set.strip()
        if args.site is not None:
            config.default_site = args.site.strip()
        if args.region is not None:
            region = args.region.strip().upper()
            if region and region not in REGIONS:
                emit(f"error: unknown region {region!r}; use one of {', '.join(REGIONS)}.")
                return EXIT_USAGE
            config.default_region = region
        ok, save_note = save_config(config)
        if not ok:
            emit(save_note)
            return EXIT_IO_ERROR
        emit(f"saved {config_path()}")
    if args.json:
        emit(json.dumps({"observer": config.observer, "default_site": config.default_site,
                         "default_region": config.default_region,
                         "dataset_version": DATASET_VERSION}, indent=2))
        return EXIT_OK
    emit(rule("Observer"))
    emit(f"  Observer        {config.observer or '(none recorded)'}")
    emit(f"  Default site    {config.default_site or '(none)'}")
    emit(f"  Default region  {config.default_region or '(none)'}")
    emit(f"  Config file     {config_path()}"
         + ("" if os.path.exists(config_path()) else " (not created yet)"))
    emit(f"  Dataset         {DATASET_VERSION} (tool {VERSION})")
    if note:
        emit(f"  {note}")
    return EXIT_OK


def command_data(args) -> int:
    report = dataset_report()
    if args.json:
        emit(json.dumps(report, indent=2, default=str))
        return EXIT_OK
    if not args.check:
        emit(rule(f"Dataset {report['dataset_version']}"))
        emit(f"  {report['species_total']} species, {report['couplet_total']} couplets")
        emit("  Run 'sedgeid.py data --check' for the full integrity report.")
        return EXIT_OK

    emit(rule(f"Dataset integrity {report['dataset_version']}"))
    emit(f"  Species            {report['species_total']}")
    for family in sorted(report["by_family"]):
        emit(f"    {family:<16} {report['by_family'][family]}")
    emit("  Wetland codes")
    for code in WETLAND_CODES:
        emit(f"    {code:<5} {report['by_code'].get(code, 0)}")
    emit(f"  Couplets           {report['couplet_total']}")
    for group in KEY_GROUPS:
        emit(f"    {group:<16} {report['couplets'][group]} couplets "
             f"(max depth {report['max_depth'][group]}, limit {MAX_COUPLET_DEPTH})")
    emit(f"  Key coverage       {report['keyed_species']} of {report['species_total']} "
         "species reachable by a key path")
    if report["unkeyed_species"]:
        emit(f"    browsable only:  {', '.join(report['unkeyed_species'])}")
    emit(f"  {report['dangling_references']} dangling references")
    if report["problems"]:
        emit(f"  {len(report['problems'])} integrity problem(s):")
        for problem in report["problems"][:10]:
            emit(f"    - {problem}")
        return EXIT_NO_MATCH
    emit("  no integrity problems found")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Interactive menu (the no-argument launch view).
# ---------------------------------------------------------------------------

MENU_ITEMS = (
    ("identify", "Identify a plant", "walk the dichotomous sedge/grass key"),
    ("wetland", "Wetland status", "indicator codes, meanings and regions"),
    ("species", "Species", "browse and search the bundled dataset"),
    ("tally", "Tally", "record observations in sedgeid_tally.csv"),
    ("me", "Observer", "who is recording this survey"),
    ("data", "Dataset", "integrity check and coverage"),
    ("help", "Help", "usage for every command"),
)


def interactive_wetland() -> None:
    emit("")
    emit(rule("National wetland indicator status"))
    print_code_table()
    emit("")
    typed = read_line("  Species name fragment, or a code (Enter for the region legend): ")
    if typed is None:
        return
    text = typed.strip()
    if not text:
        print_region_legend()
        return
    upper = text.upper()
    if upper in WETLAND_CODES and len(text) <= 4:
        command_wetland(argparse.Namespace(species=None, code=upper))
        return
    matches = find_species(text)
    if not matches:
        emit(f"no match for '{text}'.")
        suggestions = suggest_species_names(text)
        if suggestions:
            emit("closest dataset names: " + "; ".join(suggestions))
        return
    for species in matches[:3]:
        emit("")
        emit(f"  {species.scientific_name} ({species.common_name}) - "
             f"{accent(species.wetland_code)} - {WETLAND_CODES[species.wetland_code]}")
        emit(f"  Regions: {', '.join(species.regions)}")


def _species_args(query=None, family=None):
    return argparse.Namespace(name=None, id=None, query=query, family=family,
                              code=None, json=False)


def interactive_species() -> None:
    emit("")
    typed = read_line("  Name fragment, or a family (Poaceae / Cyperaceae); Enter lists all: ")
    if typed is None:
        return
    text = typed.strip()
    if text in ("Cyperaceae", "Poaceae"):
        command_species(_species_args(family=text))
        return
    if not text:
        command_species(_species_args())
        return
    matches = find_species(text)
    if not matches:
        emit(f"no matches for \"{text}\".")
        return
    emit(f"{len(matches)} species match name contains \"{text}\".")
    print_species_list(matches)
    typed = read_line("  Enter a number for the full card, or Enter to continue: ")
    if typed is None or not typed.strip():
        return
    chosen = _pick_by_number(matches, typed.strip())
    if chosen is not None:
        print_species_card(chosen)


def interactive_tally() -> None:
    while True:
        emit("")
        emit(rule("Tally sheet"))
        emit(f"  File: {tally_path()}")
        entries, _ = read_tally()
        if entries:
            rows = [[entry.entry_id, entry.observed_on, entry.species_name[:34], entry.count,
                     entry.site[:16] or "-"] for entry in entries[-10:]]
            print_table(["Id", "Date", "Species", "N", "Site"], rows,
                        aligns=["<", "<", "<", ">", "<"])
            total = sum(entry.count for entry in entries)
            emit(f"  {len(entries)} rows, {total} plants recorded in this sheet.")
        else:
            emit("  No rows yet - record the plant you just keyed, or pick one from the list.")
        emit("  1) add a row   2) or u) undo the last row   3) summary   "
             "4) observer   w) write the summary CSV   Enter) back")
        token = read_line("  Choice: ")
        if token is None:
            return
        answer = token.strip().lower()
        if answer in ("", "q", "quit", "b", "m"):
            return
        if answer == "1":
            interactive_tally_add()
        elif answer in ("2", "u"):
            command_tally_undo(argparse.Namespace())
        elif answer == "3":
            command_tally_summary(argparse.Namespace(csv=None))
        elif answer == "4":
            command_me(argparse.Namespace(set=None, site=None, region=None, json=False))
        elif answer == "w":
            target = read_line("  Write the three tables to which CSV path? ")
            if target is not None and target.strip():
                command_tally_summary(argparse.Namespace(csv=target.strip()))
        else:
            emit("  Enter 1-4, u to undo, w to write the summary, or Enter to go back.")


def interactive_menu() -> int:
    while True:
        emit("")
        emit(rule())
        emit(banner())
        for index, (_key, title, description) in enumerate(MENU_ITEMS, start=1):
            emit(f"  {index}) {title:<18} {description}")
        emit("  q) Quit")
        if DATA_PROBLEMS:
            emit(f"  data warning: {DATA_PROBLEMS[0]}")
        token = read_line("  Select 1-7, q to quit: ")
        if token is None:
            return EXIT_OK
        answer = token.strip().lower()
        if answer in ("", "q", "quit", "exit"):
            return EXIT_OK
        if answer.isdigit() and 1 <= int(answer) <= len(MENU_ITEMS):
            key = MENU_ITEMS[int(answer) - 1][0]
        elif answer in [item[0] for item in MENU_ITEMS]:
            key = answer
        else:
            emit("  That is not one of the menu numbers; enter 1-7 or q to quit.")
            continue
        if key == "identify":
            command_identify(argparse.Namespace(group=None, answers=None))
        elif key == "wetland":
            interactive_wetland()
        elif key == "species":
            interactive_species()
        elif key == "tally":
            interactive_tally()
        elif key == "me":
            command_me(argparse.Namespace(set=None, site=None, region=None, json=False))
        elif key == "data":
            command_data(argparse.Namespace(check=True, json=False))
        elif key == "help":
            build_parser().print_help()


# ---------------------------------------------------------------------------
# Argument parsing and entry point.
# ---------------------------------------------------------------------------

KNOWN_COMMANDS = ("identify", "wetland", "species", "tally", "me", "data")

EPILOG = """examples:
  sedgeid.py                                    interactive menu
  sedgeid.py identify --group sedge             walk the sedge key
  sedgeid.py identify --group grass --answers 1,2,1,4
  sedgeid.py wetland --species "Carex stricta"  indicator status and regions
  sedgeid.py wetland --code OBL                 every species carrying OBL
  sedgeid.py species --query carex              search the bundled dataset
  sedgeid.py species "Spartina pectinata"       full detail card
  sedgeid.py tally add "Carex stricta" --count 3 --site "Plot A"
  sedgeid.py tally list --site "Plot A"
  sedgeid.py tally undo
  sedgeid.py tally summary --csv summary.csv
  sedgeid.py me --set "R. Okafor"
  sedgeid.py data --check

exit codes: 0 success, 1 no match or no rows, 2 usage error, 3 file could not be written
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sedgeid",
        description="SedgeID - sedge and grass identification by traits, wetland "
                    "indicator status lookup, and a botanical survey tally sheet.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"SedgeID {VERSION} (dataset {DATASET_VERSION})")
    parser.add_argument("--no-color", action="store_true",
                        help="disable ANSI colour even when stdout is a terminal")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_identify = sub.add_parser(
        "identify", help="walk the dichotomous key to a species",
        description="Walk the dichotomous key for a group.  With no terminal, pipe the "
                    "answer numbers in or pass --answers.")
    p_identify.add_argument("--group", choices=("sedge", "grass"),
                            help="which key to walk (grass or sedge)")
    p_identify.add_argument("--answers",
                            help="comma or space separated choice numbers, e.g. 1,2,1")
    p_identify.set_defaults(handler=command_identify)

    p_wetland = sub.add_parser(
        "wetland", help="wetland indicator status and region sublists",
        description="Look up a national wetland indicator code, its meaning and the "
                    "regional sublists a species appears on.")
    p_wetland.add_argument("--species", metavar="NAME",
                           help="scientific or common name fragment to look up")
    p_wetland.add_argument("--code", metavar="CODE",
                           choices=tuple(WETLAND_CODES),
                           help="list every bundled species carrying this code")
    p_wetland.set_defaults(handler=command_wetland)

    p_species = sub.add_parser(
        "species", help="browse, search and open species cards",
        description="List the bundled dataset, filter it, or open one species card.")
    p_species.add_argument("name", nargs="?", default=None,
                           help="scientific or common name to open as a detail card")
    p_species.add_argument("--id", help="dataset id to open as a detail card")
    p_species.add_argument("-q", "--query", help="name fragment filter")
    p_species.add_argument("--family", choices=FAMILIES, help="family filter")
    p_species.add_argument("--code", choices=tuple(WETLAND_CODES), help="wetland code filter")
    p_species.add_argument("--json", action="store_true", help="machine readable output")
    p_species.set_defaults(handler=command_species)

    p_tally = sub.add_parser(
        "tally", help="record and report survey observations",
        description="Append observations to sedgeid_tally.csv, list them, undo the last row "
                    "and count them.")
    p_tally.add_argument("--site", help="only list rows for this site or plot label")
    p_tally.add_argument("--since", help="only list rows on or after this ISO date")
    tally_sub = p_tally.add_subparsers(dest="tally_command", metavar="ACTION")

    p_list = tally_sub.add_parser("list", help="list the rows in the tally sheet",
                                  description="List the rows in sedgeid_tally.csv with a "
                                              "running total.")
    p_list.add_argument("--site", help="only rows for this site or plot label")
    p_list.add_argument("--since", help="only rows on or after this ISO date")
    p_list.set_defaults(handler=command_tally_list)

    p_add = tally_sub.add_parser("add", help="append one observation row",
                                 description="Append one observation row to sedgeid_tally.csv.")
    p_add.add_argument("species", nargs="?", default=None,
                       help="scientific or common name (pre-filled from the key)")
    p_add.add_argument("--count", help="positive whole number of plants")
    p_add.add_argument("--site", help="site or plot label")
    p_add.add_argument("--date", help="ISO YYYY-MM-DD observation date (default today)")
    p_add.add_argument("--region", help="region sublist code, one of "
                       + ", ".join(REGIONS))
    p_add.add_argument("--method", choices=("visual", "key", "voucher"),
                       help="how the plant was identified")
    p_add.add_argument("--note", help="free-text note")
    p_add.add_argument("--from-key", action="store_true",
                       help="stamp the row with method=key")
    p_add.set_defaults(handler=command_tally_add)

    p_undo = tally_sub.add_parser("undo", help="remove the last appended row",
                                  description="Remove the last appended row from the sheet.")
    p_undo.set_defaults(handler=command_tally_undo)

    p_summary = tally_sub.add_parser("summary", help="counts by species, site and code",
                                     description="Count the tally sheet by species, site and "
                                                 "wetland code.")
    p_summary.add_argument("--csv", metavar="PATH",
                           help="write the same three tables to this CSV file")
    p_summary.set_defaults(handler=command_tally_summary)
    p_tally.set_defaults(handler=command_tally_list)

    p_me = sub.add_parser(
        "me", help="show or set the local observer",
        description="Show the observer recorded in .sedgeid_config.json, or set it.")
    p_me.add_argument("--set", metavar="NAME", help="observer name or initials to store")
    p_me.add_argument("--site", help="default site or plot label")
    p_me.add_argument("--region", help="default region code, one of "
                       + ", ".join(REGIONS))
    p_me.add_argument("--json", action="store_true", help="machine readable output")
    p_me.set_defaults(handler=command_me)

    p_data = sub.add_parser(
        "data", help="dataset integrity report",
        description="Report species counts by family and wetland code, couplet counts and "
                    "dangling references.")
    p_data.add_argument("--check", action="store_true", help="print the full integrity report")
    p_data.add_argument("--json", action="store_true", help="machine readable output")
    p_data.set_defaults(handler=command_data)

    return parser


def _preflight(argv) -> None:
    """Suggest a command for a typo before argparse prints a bare usage error."""
    if not argv:
        return
    first = argv[0]
    if first.startswith("-") or first in KNOWN_COMMANDS:
        return
    suggestions = difflib.get_close_matches(first, KNOWN_COMMANDS, n=1, cutoff=0.4)
    hint = f" Did you mean '{suggestions[0]}'?" if suggestions else ""
    print(f"sedgeid: unknown command '{first}'.{hint}", file=sys.stderr)
    print("Known commands: " + ", ".join(KNOWN_COMMANDS)
          + ". Run 'sedgeid.py --help' for usage.", file=sys.stderr)
    raise SystemExit(EXIT_USAGE)


def main(argv=None) -> int:
    global USE_COLOR
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if "--no-color" in argv:
        USE_COLOR = False
        argv.remove("--no-color")
    elif os.environ.get("NO_COLOR") is not None:
        USE_COLOR = False
    _preflight(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if DATA_PROBLEMS:
        print(f"sedgeid: data warning: {DATA_PROBLEMS[0]} "
              "(run 'sedgeid.py data --check')", file=sys.stderr)
    try:
        if args.command is None:
            return interactive_menu()
        return args.handler(args)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass
        return EXIT_OK
    except KeyboardInterrupt:
        print("")
        return EXIT_OK
    except OSError as error:
        print(f"sedgeid: {error}", file=sys.stderr)
        return EXIT_IO_ERROR


if __name__ == "__main__":
    sys.exit(main())
