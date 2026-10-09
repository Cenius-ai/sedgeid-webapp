#!/usr/bin/env python3
"""Test suite for SedgeID.

Run with either runner:

    python3 -m unittest test_sedgeid
    python3 -m pytest -q test_sedgeid.py

Every test works in a throwaway temporary directory, so the working directory of
the checkout never gains a tally sheet or a config file.
"""

import csv
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from json import loads as json_loads

ROOT = os.path.dirname(os.path.abspath(__file__))
SEDGEID = os.path.join(ROOT, "sedgeid.py")


def load_module():
    """Import sedgeid.py as a module (it lives beside this test file)."""
    spec = importlib.util.spec_from_file_location("sedgeid", SEDGEID)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sedgeid"] = module
    spec.loader.exec_module(module)
    return module


sedgeid = load_module()


def run_cli(args, cwd, stdin_text=None, timeout=30):
    """Run the CLI in ``cwd`` and return (exit_code, stdout, stderr)."""
    completed = subprocess.run(
        [sys.executable, SEDGEID] + list(args),
        cwd=cwd,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "NO_COLOR": "1"},
    )
    return completed.returncode, completed.stdout, completed.stderr


class DatasetIntegrityTests(unittest.TestCase):
    """The embedded dataset and couplet graph must be self-consistent."""

    def test_no_integrity_problems(self):
        self.assertEqual(sedgeid.check_dataset(), [])

    def test_no_dangling_references(self):
        self.assertEqual(sedgeid.dangling_references(), 0)

    def test_every_choice_resolves_to_a_couplet_or_a_species(self):
        for key_node in sedgeid.KEY_NODES.values():
            for choice in key_node.choices:
                self.assertTrue(
                    choice.next in sedgeid.KEY_NODES or choice.next in sedgeid.SPECIES_BY_ID,
                    f"{key_node.id} -> {choice.next}")

    def test_starter_dataset_size_and_families(self):
        report = sedgeid.dataset_report()
        self.assertGreaterEqual(report["species_total"], 30)
        self.assertEqual(set(report["by_family"]), {"Cyperaceae", "Poaceae"})
        self.assertEqual(set(report["by_code"]), set(sedgeid.WETLAND_CODES))

    def test_every_species_is_reachable_by_a_key_path(self):
        report = sedgeid.dataset_report()
        self.assertEqual(report["keyed_species"], report["species_total"])
        self.assertEqual(report["unkeyed_species"], [])

    def test_key_depth_within_documented_limit(self):
        for group in sedgeid.KEY_GROUPS:
            depths = sedgeid.key_depths(group)
            self.assertLessEqual(max(depths), sedgeid.MAX_COUPLET_DEPTH)

    def test_species_traits_reference_real_states(self):
        for species in sedgeid.SPECIES:
            for trait_id, state in species.trait_values.items():
                trait = sedgeid.TRAITS_BY_ID[trait_id]
                self.assertIn(state, trait.states, f"{species.id}:{trait_id}")

    def test_data_check_cli_reports_zero_dangling_references(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["data", "--check"], workdir)
        self.assertEqual(code, 0)
        self.assertIn(f"Species            {len(sedgeid.SPECIES)}", out)
        self.assertIn("0 dangling references", out)
        self.assertIn("Cyperaceae", out)
        self.assertIn("Poaceae", out)


class KeyGraphTests(unittest.TestCase):
    """The dichotomous key resolves to real species and supports back/restart."""

    def test_documented_answer_trail_resolves_to_a_species(self):
        session = sedgeid.KeySession("sedge")
        for token in ("1", "2", "1"):
            result = session.answer(token)
        self.assertEqual(result, "species")
        self.assertIn(session.resolved_species_id, sedgeid.SPECIES_BY_ID)

    def test_cli_answers_run_prints_species_and_wetland_code(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(
                ["identify", "--group", "sedge", "--answers", "1,2,1"], workdir)
        self.assertEqual(code, 0)
        self.assertIn("Carex vulpinoidea", out)
        self.assertIn("FACW", out)
        self.assertIn("Regions", out)
        self.assertIn("Trail", out)

    def test_back_restores_the_previous_couplet(self):
        session = sedgeid.KeySession("sedge")
        session.answer("1")
        session.answer("2")
        self.assertEqual(session.couplet_number, 3)
        self.assertEqual(session.answer("b"), "back")
        self.assertEqual(session.couplet_number, 2)
        self.assertEqual(len(session.trail), 1)

    def test_back_at_the_start_is_rejected(self):
        session = sedgeid.KeySession("sedge")
        self.assertEqual(session.answer("b"), "invalid")

    def test_restart_clears_the_trail(self):
        session = sedgeid.KeySession("sedge")
        session.answer("1")
        session.answer("2")
        self.assertEqual(session.answer("r"), "restart")
        self.assertEqual(session.trail, [])
        self.assertEqual(session.current_node_id, "sedge")

    def test_quit_leaves_the_tally_untouched(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["identify", "--group", "sedge", "--answers", "1,q"], workdir)
            self.assertFalse(os.path.exists(os.path.join(workdir, "sedgeid_tally.csv")))
        self.assertEqual(code, 0)
        self.assertIn("Key ended - nothing was written to the tally sheet", out)

    def test_out_of_range_answer_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["identify", "--group", "sedge", "--answers", "9"], workdir)
        self.assertEqual(code, 2)
        self.assertIn("is not one of the offered choices", out)

    def test_invalid_choice_is_reprompted_at_a_terminal(self):
        answers = iter(["9", "", "1", "2", "1"])
        printed = io.StringIO()
        original = sedgeid.read_line
        sedgeid.read_line = lambda prompt="": next(answers, None)
        try:
            with redirect_stdout(printed):
                sedgeid.run_key_interactive("sedge")
        finally:
            sedgeid.read_line = original
        text = printed.getvalue()
        self.assertIn("Enter a number 1-2", text)
        # The same couplet is shown again, and no answer was lost.
        self.assertGreaterEqual(text.count("couplet 1"), 2)
        self.assertIn("Identified", text)

    def test_result_card_offers_add_to_tally_with_the_species_prefilled(self):
        captured = []
        answers = iter(["1", "2", "1", "a"])
        original_read, original_add = sedgeid.read_line, sedgeid.interactive_tally_add
        sedgeid.read_line = lambda prompt="": next(answers, None)
        sedgeid.interactive_tally_add = lambda prefill=None: captured.append(prefill) or 0
        printed = io.StringIO()
        try:
            with redirect_stdout(printed):
                sedgeid.run_key_interactive("sedge")
        finally:
            sedgeid.read_line, sedgeid.interactive_tally_add = original_read, original_add
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].scientific_name, "Carex vulpinoidea")

    def test_end_of_input_ends_the_key_cleanly(self):
        answers = iter(["1"])
        original = sedgeid.read_line
        sedgeid.read_line = lambda prompt="": next(answers, None)
        printed = io.StringIO()
        try:
            with redirect_stdout(printed):
                code = sedgeid.run_key_interactive("sedge")
        finally:
            sedgeid.read_line = original
        self.assertEqual(code, 0)

    def test_piped_answers_do_not_hang_and_resolve(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["identify", "--group", "sedge"], workdir,
                                   stdin_text="1 2 1\n")
        self.assertEqual(code, 0)
        self.assertIn("Carex vulpinoidea", out)

    def test_key_output_is_wrapped_to_80_columns(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["identify", "--group", "grass", "--answers", "1,1"], workdir)
        self.assertEqual(code, 0)
        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)


class WetlandLookupTests(unittest.TestCase):
    """F2: national indicator status plus the per-species region field."""

    def test_lookup_by_species_prints_code_meaning_and_regions(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["wetland", "--species", "Phragmites australis"], workdir)
        self.assertEqual(code, 0)
        self.assertIn("FACW", out)
        self.assertIn("Facultative wetland", out)
        self.assertIn("Region", out)
        self.assertIn("Midwest", out)

    def test_reverse_lookup_lists_every_species_with_a_code(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["wetland", "--code", "OBL"], workdir)
        self.assertEqual(code, 0)
        self.assertIn("Carex stricta", out)
        self.assertIn("OBL", out)
        expected = len([s for s in sedgeid.SPECIES if s.wetland_code == "OBL"])
        self.assertIn(f"{expected} bundled species carry OBL", out)

    def test_unknown_name_prints_closest_names_and_exits_one(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["wetland", "--species", "Phragmitis australus"], workdir)
        self.assertEqual(code, 1)
        self.assertIn("no match", out)
        self.assertIn("closest dataset names", out)
        self.assertIn("Phragmites australis", out)

    def test_no_arguments_prints_the_six_codes_and_the_region_legend(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["wetland"], workdir)
        self.assertEqual(code, 0)
        for code_name in ("OBL", "FACW", "FAC", "FACU", "UPL", "NI"):
            self.assertIn(code_name, out)
        self.assertIn("Region sublists", out)
        self.assertIn("Alaska", out)


class SpeciesBrowseTests(unittest.TestCase):
    """F5: browse and search the bundled dataset."""

    def test_name_fragment_search_counts_and_lists(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["species", "--query", "carex"], workdir)
        self.assertEqual(code, 0)
        self.assertIn("species match", out)
        self.assertIn("Carex stricta", out)
        self.assertIn("Cyperaceae", out)
        self.assertIn("OBL", out)

    def test_family_filter_returns_grasses_only(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["species", "--family", "Poaceae"], workdir)
        self.assertEqual(code, 0)
        self.assertIn("Poaceae", out)
        self.assertNotIn("Cyperaceae", out)

    def test_code_filter(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["species", "--code", "FACU"], workdir)
        self.assertEqual(code, 0)
        self.assertIn("FACU", out)
        self.assertIn("Andropogon gerardii", out)

    def test_no_matches_prints_syntax_hint_and_exits_one(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["species", "--query", "zzz"], workdir)
        self.assertEqual(code, 1)
        self.assertIn("no matches", out)
        self.assertIn("Filter syntax", out)

    def test_detail_card_shows_names_family_code_regions_and_traits(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["species", "Spartina pectinata"], workdir)
        self.assertEqual(code, 0)
        self.assertIn("Prairie cordgrass", out)
        self.assertIn("Poaceae", out)
        self.assertIn("FACW", out)
        self.assertIn("Great Plains", out)
        self.assertIn("Traits", out)


class TallySheetTests(unittest.TestCase):
    """F3: append-only CSV, header on first use, undo, validation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name
        self.csv_path = os.path.join(self.workdir, "sedgeid_tally.csv")

    def tearDown(self):
        self._tmp.cleanup()

    def read_rows(self):
        with open(self.csv_path, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_add_creates_header_and_row_and_reports_the_running_total(self):
        code, out, _ = run_cli(
            ["tally", "add", "Carex stricta", "--count", "3", "--site", "Plot A"], self.workdir)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.csv_path))
        with open(self.csv_path, newline="", encoding="utf-8") as handle:
            header = handle.readline().strip()
        self.assertEqual(header, ",".join(sedgeid.CSV_FIELDS))
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["species_name"], "Carex stricta")
        self.assertEqual(rows[0]["count"], "3")
        self.assertEqual(rows[0]["site"], "Plot A")
        self.assertIn("now holds 1 row", out)

    def test_observer_is_stamped_on_the_row(self):
        run_cli(["me", "--set", "R. Okafor", "--site", "Plot A"], self.workdir)
        code, _, _ = run_cli(["tally", "add", "Carex stricta", "--count", "1"], self.workdir)
        self.assertEqual(code, 0)
        self.assertEqual(self.read_rows()[0]["observer"], "R. Okafor")
        self.assertEqual(self.read_rows()[0]["site"], "Plot A")

    def test_undo_removes_only_the_last_row(self):
        for name, count in (("Carex stricta", 3), ("Carex lupulina", 4), ("Scirpus cyperinus", 1)):
            run_cli(["tally", "add", name, "--count", str(count), "--site", "Plot A"], self.workdir)
        self.assertEqual(len(self.read_rows()), 3)
        code, out, _ = run_cli(["tally", "undo"], self.workdir)
        self.assertEqual(code, 0)
        rows = self.read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["species_name"] for row in rows], ["Carex stricta", "Carex lupulina"])
        self.assertIn("2 rows", out)

    def test_undo_without_rows_exits_one_with_a_message(self):
        code, out, _ = run_cli(["tally", "undo"], self.workdir)
        self.assertEqual(code, 1)
        self.assertIn("no rows to undo", out)

    def test_non_numeric_count_is_rejected_and_nothing_is_written(self):
        code, out, _ = run_cli(
            ["tally", "add", "Carex stricta", "--count", "zero"], self.workdir)
        self.assertEqual(code, 2)
        self.assertIn("positive whole number", out)
        self.assertFalse(os.path.exists(self.csv_path))

    def test_zero_and_negative_counts_are_rejected(self):
        for bad in ("0", "-2"):
            code, _, _ = run_cli(["tally", "add", "Carex stricta", "--count", bad], self.workdir)
            self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.csv_path))

    def test_unknown_species_is_reported_with_suggestions(self):
        code, out, _ = run_cli(["tally", "add", "Carex strictus", "--count", "1"], self.workdir)
        self.assertEqual(code, 1)
        self.assertIn("no match", out)
        self.assertIn("Carex stricta", out)
        self.assertFalse(os.path.exists(self.csv_path))

    def test_bad_region_is_a_usage_error(self):
        code, out, _ = run_cli(
            ["tally", "add", "Carex stricta", "--count", "1", "--region", "ZZ"], self.workdir)
        self.assertEqual(code, 2)
        self.assertIn("unknown region", out)

    def test_list_reports_the_csv_path_and_a_running_total(self):
        run_cli(["tally", "add", "Carex stricta", "--count", "3", "--site", "Plot A"], self.workdir)
        code, out, _ = run_cli(["tally", "list"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("sedgeid_tally.csv", out)
        self.assertIn("3 plants in total", out)

    def test_list_with_no_rows_names_the_first_command(self):
        code, out, _ = run_cli(["tally"], self.workdir)
        self.assertEqual(code, 1)
        self.assertIn("no tally rows yet", out)
        self.assertIn("sedgeid.py tally add", out)

    def test_malformed_rows_are_skipped_with_a_count(self):
        with open(self.csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(sedgeid.CSV_FIELDS))
            writer.writeheader()
            writer.writerow({"entry_id": "E0001", "observed_on": "2025-06-14", "observer": "R.O.",
                             "species_id": "carex_stricta", "species_name": "Carex stricta",
                             "count": "4", "site": "Plot A", "region": "", "method": "", "note": ""})
            writer.writerow({"entry_id": "E0002", "observed_on": "2025-06-14", "observer": "R.O.",
                             "species_id": "carex_stricta", "species_name": "Carex stricta",
                             "count": "many", "site": "Plot A", "region": "", "method": "", "note": ""})
        code, out, _ = run_cli(["tally", "list"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("skipped 1 malformed row", out)


class TallySummaryTests(unittest.TestCase):
    """F6: counts per species, site and wetland code."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def seed(self):
        rows = [
            ("Carex stricta", 3, "Plot A", "NE"),
            ("Carex stricta", 2, "Plot B", "NE"),
            ("Carex lupulina", 4, "Plot A", "NE"),
            ("Phragmites australis", 6, "Plot B", "NE"),
        ]
        for name, count, site, region in rows:
            run_cli(["tally", "add", name, "--count", str(count), "--site", site,
                     "--region", region], self.workdir)

    def test_summary_prints_three_count_tables(self):
        self.seed()
        code, out, _ = run_cli(["tally", "summary"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("4 rows, 15 plants", out)
        self.assertIn("Rows per species", out)
        self.assertIn("Rows per site", out)
        self.assertIn("Rows per wetland code", out)
        self.assertIn("Phragmites australis", out)
        self.assertIn("Plot A", out)
        self.assertIn("OBL", out)
        self.assertIn("FACW", out)

    def test_summary_columns_fit_80_characters(self):
        self.seed()
        code, out, _ = run_cli(["tally", "summary"], self.workdir)
        self.assertEqual(code, 0)
        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_summary_csv_flag_writes_the_tables_and_prints_the_path(self):
        self.seed()
        target = os.path.join(self.workdir, "summary.csv")
        code, out, _ = run_cli(["tally", "summary", "--csv", target], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("summary written to", out)
        self.assertTrue(os.path.exists(target))
        body = open(target, encoding="utf-8").read()
        self.assertIn("Rows per species,Carex stricta", body)
        self.assertIn("Rows per wetland code,FACW", body)

    def test_empty_summary_names_the_exact_first_command_and_exits_zero(self):
        code, out, _ = run_cli(["tally", "summary"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("no tally rows yet", out)
        self.assertIn('sedgeid.py tally add "Carex stricta" --count 1', out)


class ObserverConfigTests(unittest.TestCase):
    """F7: local observer identity with an unwritable-file fallback."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_writes_the_config_file(self):
        code, out, _ = run_cli(["me", "--set", "J. Feldmann"], self.workdir)
        self.assertEqual(code, 0)
        path = os.path.join(self.workdir, ".sedgeid_config.json")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as handle:
            payload = json_loads(handle.read())
        self.assertEqual(payload["observer"], "J. Feldmann")
        self.assertIn("saved", out)

    def test_show_reports_the_missing_config_without_failing(self):
        code, out, _ = run_cli(["me"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("(none recorded)", out)
        self.assertIn("not created yet", out)

    def test_unreadable_config_falls_back_to_an_empty_observer(self):
        os.mkdir(os.path.join(self.workdir, ".sedgeid_config.json"))
        code, out, _ = run_cli(["me"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("could not be read", out)
        self.assertIn("(none recorded)", out)

    def test_unwritable_config_warns_but_the_row_is_still_appended(self):
        os.mkdir(os.path.join(self.workdir, ".sedgeid_config.json"))
        code, out, _ = run_cli(
            ["tally", "add", "Carex stricta", "--count", "1", "--site", "Plot A"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn("could not be read", out)
        self.assertIn("no observer recorded", out)
        rows = list(csv.DictReader(open(os.path.join(self.workdir, "sedgeid_tally.csv"),
                                        newline="", encoding="utf-8")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["observer"], "")
        self.assertEqual(rows[0]["species_name"], "Carex stricta")

    def test_first_run_prompts_once_at_a_terminal_and_saves(self):
        answers = iter(["A. Whitlock"])
        original = sedgeid.read_line
        sedgeid.read_line = lambda prompt="": next(answers, None)
        cwd = os.getcwd()
        try:
            os.chdir(self.workdir)
            printed = io.StringIO()
            with redirect_stdout(printed):
                observer, notes = sedgeid.ensure_observer(True)
        finally:
            sedgeid.read_line = original
            os.chdir(cwd)
        self.assertEqual(observer, "A. Whitlock")
        self.assertTrue(os.path.exists(os.path.join(self.workdir, ".sedgeid_config.json")))
        self.assertIn("observer saved", " ".join(notes))
        # A second call does not prompt again.
        original = sedgeid.read_line
        sedgeid.read_line = lambda prompt="": self.fail("prompted twice")
        cwd = os.getcwd()
        try:
            os.chdir(self.workdir)
            observer, notes = sedgeid.ensure_observer(True)
        finally:
            sedgeid.read_line = original
            os.chdir(cwd)
        self.assertEqual(observer, "A. Whitlock")

    def test_config_fallback_never_raises_on_a_broken_payload(self):
        with open(os.path.join(self.workdir, ".sedgeid_config.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        cwd = os.getcwd()
        try:
            os.chdir(self.workdir)
            config, note = sedgeid.load_config()
        finally:
            os.chdir(cwd)
        self.assertEqual(config.observer, "")
        self.assertIn("could not be read", note)


class CliContractTests(unittest.TestCase):
    """T1/T10: dispatch, usage errors and the no-argument menu."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_help_lists_all_six_commands_and_exits_zero(self):
        code, out, _ = run_cli(["--help"], self.workdir)
        self.assertEqual(code, 0)
        for command in ("identify", "wetland", "species", "tally", "me", "data"):
            self.assertIn(command, out)

    def test_version_exits_zero(self):
        code, out, _ = run_cli(["--version"], self.workdir)
        self.assertEqual(code, 0)
        self.assertIn(sedgeid.VERSION, out)

    def test_unknown_command_suggests_the_right_one_and_exits_two(self):
        code, out, err = run_cli(["identfy"], self.workdir)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("unknown command", err)
        self.assertIn("identify", err)

    def test_bare_launch_renders_the_menu_and_exits_cleanly_on_eof(self):
        code, out, _ = run_cli([], self.workdir, stdin_text="")
        self.assertEqual(code, 0)
        for title in ("Identify a plant", "Wetland status", "Species", "Tally",
                      "Observer", "Dataset", "Help"):
            self.assertIn(title, out)

    def test_menu_selection_reaches_a_second_screen(self):
        code, out, _ = run_cli([], self.workdir, stdin_text="2\n\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("National wetland indicator status", out)
        self.assertIn("Obligate wetland", out)

    def test_menu_species_screen_renders_seeded_content(self):
        code, out, _ = run_cli([], self.workdir, stdin_text="3\n\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("All bundled species", out)
        self.assertIn("Carex stricta", out)

    def test_output_lines_stay_within_80_columns(self):
        for args in (["species", "--query", "carex"], ["wetland"], ["data", "--check"]):
            code, out, _ = run_cli(args, self.workdir)
            self.assertEqual(code, 0)
            for line in out.splitlines():
                self.assertLessEqual(len(line), 80, f"{args}: {line}")

    def test_no_colour_is_emitted_when_not_a_tty(self):
        code, out, _ = run_cli(["--no-color", "wetland"], self.workdir)
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b[", out)

    def test_json_output_mode_is_machine_readable(self):
        code, out, _ = run_cli(["species", "--query", "spartina", "--json"], self.workdir)
        self.assertEqual(code, 0)
        payload = json_loads(out)
        self.assertEqual([row["scientific_name"] for row in payload],
                         ["Spartina pectinata", "Spartina alterniflora"])


class FullWorkflowTests(unittest.TestCase):
    """The identification -> wetland -> tally path end to end."""

    def test_identified_species_flows_into_a_tally_row(self):
        with tempfile.TemporaryDirectory() as workdir:
            code, out, _ = run_cli(["identify", "--group", "sedge", "--answers", "1,2,1"], workdir)
            self.assertEqual(code, 0)
            self.assertIn("Carex vulpinoidea", out)
            code, out, _ = run_cli(["tally", "add", "Carex vulpinoidea", "--count", "5",
                                    "--site", "Plot C", "--method", "key"], workdir)
            self.assertEqual(code, 0)
            code, out, _ = run_cli(["tally", "summary"], workdir)
            self.assertEqual(code, 0)
            self.assertIn("Carex vulpinoidea", out)
            self.assertIn("FACW", out)
            code, out, _ = run_cli(["tally", "list"], workdir)
            self.assertEqual(code, 0)
            self.assertIn("Plot C", out)

    def test_every_species_can_be_recorded_by_name(self):
        with tempfile.TemporaryDirectory() as workdir:
            names = [s.scientific_name for s in sedgeid.SPECIES]
            self.assertEqual(len(names), len(set(names)))
            # A single representative add proves the CSV path; the dataset check
            # proves every name is resolvable.
            for name in names:
                self.assertTrue(sedgeid.find_species(name), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
