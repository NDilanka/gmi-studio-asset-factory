"""Offline unit tests for the DATUM-01 asset factory.

Run from this directory:  python -m unittest test_factory.py -v

Nothing here makes a network call or a paid media call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from factory_core import (  # noqa: E402
    BASELINE_SLOT_IDS,
    LOCKED_PALETTE,
    SLOT_IDS,
    analyze_image_brand_fit,
    compare_baseline_vs_factory,
    load_workflow_parameter,
    parse_llm_spec,
    srgb_to_lab,
    validate_brief,
    validate_workflow_json,
)
from run_factory import synthetic_drift_image, synthetic_locked_image  # noqa: E402

MAIN_WORKFLOW = ROOT / "workflows" / "brand-asset-factory.studio.json"
SEEDREAM_WORKFLOW = ROOT / "workflows" / "brand-asset-factory-seedream.studio.json"
BRIEF = ROOT / "brand_brief.json"
RUN_FACTORY = ROOT / "run_factory.py"


def _clean_env() -> dict:
    env = dict(os.environ)
    env.pop("GMI_API_KEY", None)
    return env


def _load_brief() -> dict:
    return json.loads(BRIEF.read_text(encoding="utf-8"))


class WorkflowTests(unittest.TestCase):
    def test_main_workflow_is_valid(self):
        report = validate_workflow_json(MAIN_WORKFLOW)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["dangling_links"], [])
        self.assertTrue(report["ok"])
        self.assertEqual(report["node_count"], 16)
        self.assertEqual(report["link_count"], 12)
        self.assertEqual(report["stage_summary"]["remix"], 4)
        self.assertEqual(report["stage_summary"]["save"], 6)
        self.assertTrue(report["remix_fanout_from_batch"])
        self.assertEqual(report["generation_family"], "reve")
        self.assertTrue(report["has_studio_notes"])

    def test_seedream_workflow_is_valid(self):
        report = validate_workflow_json(SEEDREAM_WORKFLOW)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["dangling_links"], [])
        self.assertTrue(report["ok"])
        self.assertEqual(report["node_count"], 16)
        self.assertEqual(report["link_count"], 12)
        self.assertEqual(report["generation_family"], "seedream")
        self.assertTrue(report["remix_fanout_from_batch"])

    def test_validator_flags_dangling_link(self):
        data = json.loads(MAIN_WORKFLOW.read_text(encoding="utf-8"))
        data["links"].append([99, 2, 0, 777, 0, "IMAGE"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            report = validate_workflow_json(path)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["dangling_link_count"], 1)

    def test_workflow_prompts_carry_locked_brief(self):
        brief = _load_brief()
        for workflow in (MAIN_WORKFLOW, SEEDREAM_WORKFLOW):
            prompt = load_workflow_parameter(workflow, "LLM Node", "prompt")
            self.assertIn(brief["raw_baseline_brief"], prompt)
            for hex_value in LOCKED_PALETTE.values():
                self.assertIn(hex_value, prompt)

    def test_seedream_sizes_within_api_bounds(self):
        data = json.loads(SEEDREAM_WORKFLOW.read_text(encoding="utf-8"))
        checked = 0
        for node in data["nodes"]:
            if not node["type"].startswith("Seedream"):
                continue
            params = dict(zip(node["properties"]["parameter_names"], node["widgets_values"]))
            width, height = (int(part) for part in params["size"].split("x"))
            self.assertTrue(3.6e6 <= width * height <= 10.4e6, params["size"])
            self.assertTrue(1 / 16 <= width / height <= 16, params["size"])
            self.assertEqual(params["output_format"], "png")
            checked += 1
        self.assertEqual(checked, 6)


class BriefTests(unittest.TestCase):
    def test_brief_is_valid(self):
        self.assertEqual(validate_brief(_load_brief()), [])

    def test_brief_rejects_palette_drift(self):
        brief = _load_brief()
        brief["palette"]["terra"]["hex"] = "#FF00FF"
        problems = validate_brief(brief)
        self.assertTrue(any("terra" in problem for problem in problems))


class ColourTests(unittest.TestCase):
    def test_white_and_black_are_exact(self):
        for got, want in zip(srgb_to_lab(255, 255, 255), (100.0, 0.0, 0.0)):
            self.assertAlmostEqual(got, want, delta=0.01)
        for got, want in zip(srgb_to_lab(0, 0, 0), (0.0, 0.0, 0.0)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_d65_primaries_match_published_values(self):
        cases = {
            "red": ((255, 0, 0), (53.24, 80.09, 67.20)),
            "green": ((0, 255, 0), (87.73, -86.18, 83.18)),
            "blue": ((0, 0, 255), (32.30, 79.19, -107.86)),
        }
        for name, (rgb, want) in cases.items():
            for got, expected in zip(srgb_to_lab(*rgb), want):
                self.assertAlmostEqual(got, expected, delta=0.05, msg=name)

    def test_terracotta_regression_value(self):
        # #C84B31 = (200, 75, 49). Regression anchor from the same formula; the primaries
        # above are the independent accuracy check.
        for got, want in zip(srgb_to_lab(200, 75, 49), (48.9297, 48.3787, 40.9345)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_out_of_range_channel_rejected(self):
        with self.assertRaises(ValueError):
            srgb_to_lab(256, 0, 0)


class BrandFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="datum01-tests-")
        tmp = Path(cls._tmp.name)
        cls.on_brand = tmp / "on_brand.png"
        cls.neon = tmp / "neon.png"
        cls.wide = tmp / "wide_on_brand.png"
        synthetic_locked_image(cls.on_brand, (160, 90))
        synthetic_drift_image(cls.neon, (160, 90))
        synthetic_locked_image(cls.wide, (320, 180))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_on_brand_image_is_on_palette(self):
        m = analyze_image_brand_fit(self.on_brand, LOCKED_PALETTE, "16:9")
        self.assertEqual((m["width"], m["height"]), (160, 90))
        self.assertLess(m["mean_delta_e"], 1.0)
        self.assertGreaterEqual(m["palette_adherence_pct"], 99.0)
        self.assertEqual(m["chroma_outlier_pct"], 0.0)
        self.assertTrue(m["aspect_ok"])

    def test_neon_drift_is_flagged(self):
        m = analyze_image_brand_fit(self.neon, LOCKED_PALETTE, "16:9")
        self.assertGreater(m["mean_delta_e"], 20.0)
        self.assertGreater(m["chroma_outlier_pct"], 20.0)

    def test_aspect_check_uses_two_percent_tolerance(self):
        self.assertTrue(analyze_image_brand_fit(self.on_brand, LOCKED_PALETTE, "16:9")["aspect_ok"])
        self.assertFalse(analyze_image_brand_fit(self.on_brand, LOCKED_PALETTE, "1:1")["aspect_ok"])

    def test_downsampling_bounds_the_sample(self):
        m = analyze_image_brand_fit(self.wide, LOCKED_PALETTE, "16:9")
        self.assertLessEqual(max(m["sample_width"], m["sample_height"]), 160)
        self.assertEqual((m["width"], m["height"]), (320, 180))

    def test_compare_reports_measured_improvement(self):
        base = [analyze_image_brand_fit(self.neon, LOCKED_PALETTE, "16:9")]
        fact = [analyze_image_brand_fit(self.on_brand, LOCKED_PALETTE, "16:9")]
        comparison = compare_baseline_vs_factory(base, fact)
        self.assertLess(comparison["delta_factory_minus_baseline"]["mean_delta_e"], 0.0)
        self.assertIn("mean_delta_e (lower)", comparison["measured_better_on"])
        self.assertGreater(comparison["reduction_pct"]["mean_delta_e"], 0.0)

    def test_compare_rejects_empty_group(self):
        with self.assertRaises(ValueError):
            compare_baseline_vs_factory([], [])


class ParseTests(unittest.TestCase):
    CLEAN = {slot: {"prompt": f"prompt for {slot}"} for slot in SLOT_IDS}

    def test_clean_json_parses(self):
        result = parse_llm_spec(json.dumps(self.CLEAN), SLOT_IDS)
        self.assertFalse(result["fenced"])
        self.assertEqual(result["slot_count"], 6)
        self.assertEqual(result["slots"]["asset_packaging"]["prompt"], "prompt for asset_packaging")

    def test_fenced_json_parses(self):
        body = json.dumps(self.CLEAN, indent=2)
        for wrapped in (f"```json\n{body}\n```", f"```\n{body}\n```"):
            result = parse_llm_spec(wrapped, SLOT_IDS)
            self.assertTrue(result["fenced"])
            self.assertEqual(result["slot_count"], 6)

    def test_missing_slot_is_rejected(self):
        partial = dict(self.CLEAN)
        del partial["asset_social_banner"]
        with self.assertRaisesRegex(ValueError, "missing slots: asset_social_banner"):
            parse_llm_spec(json.dumps(partial), SLOT_IDS)

    def test_empty_prompt_is_rejected(self):
        broken = json.loads(json.dumps(self.CLEAN))
        broken["anchor_hardware"]["prompt"] = "   "
        with self.assertRaisesRegex(ValueError, "anchor_hardware has no non-empty prompt"):
            parse_llm_spec(json.dumps(broken), SLOT_IDS)

    def test_unexpected_key_is_rejected(self):
        extra = dict(self.CLEAN, notes={"prompt": "x"})
        with self.assertRaisesRegex(ValueError, "unexpected keys: notes"):
            parse_llm_spec(json.dumps(extra), SLOT_IDS)

    def test_string_slot_values_parse(self):
        flat = {slot: f"prompt for {slot}" for slot in SLOT_IDS}
        result = parse_llm_spec(json.dumps(flat), SLOT_IDS)
        self.assertEqual(result["slot_count"], 6)
        self.assertEqual(result["slots"]["anchor_hardware"]["prompt"], "prompt for anchor_hardware")

    def test_non_json_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            parse_llm_spec("Here are your prompts: ...", SLOT_IDS)


class CliTests(unittest.TestCase):
    def test_dry_run_exits_zero_offline(self):
        proc = subprocess.run(
            [sys.executable, "-B", str(RUN_FACTORY), "--dry-run"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=_clean_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertTrue(summary["checks_passed"])
        self.assertEqual(summary["api_calls"], 0)
        self.assertEqual(summary["cost_usd"], 0.0)

    def test_expand_without_key_exits_two(self):
        proc = subprocess.run(
            [sys.executable, "-B", str(RUN_FACTORY), "--expand"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=_clean_env(),
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("GMI_API_KEY is not set", proc.stderr)

    @staticmethod
    def _manifest_fixture(tmp: Path, factory_ids=SLOT_IDS) -> Path:
        sizes = {"16:9": (160, 90), "1:1": (120, 120), "3:2": (150, 100)}
        brief = _load_brief()
        manifest = {"baseline": {}, "factory": {}}
        for slot_id in BASELINE_SLOT_IDS:
            name = f"{slot_id}.png"
            synthetic_drift_image(tmp / name, sizes[brief["baseline_slots"][slot_id]["aspect_ratio"]])
            manifest["baseline"][slot_id] = name
        for slot_id in factory_ids:
            name = f"{slot_id}.png"
            synthetic_locked_image(tmp / name, sizes[brief["slots"][slot_id]["aspect_ratio"]])
            manifest["factory"][slot_id] = name
        path = tmp / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    def test_verify_manifest_writes_results(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            manifest = self._manifest_fixture(tmp)
            out = tmp / "out"
            proc = subprocess.run(
                [sys.executable, "-B", str(RUN_FACTORY), "--verify-manifest", str(manifest), "--output-dir", str(out)],
                cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=_clean_env(),
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            results = json.loads((out / "factory_results.json").read_text(encoding="utf-8"))
        self.assertTrue(results["integrity_ok"])
        self.assertEqual(results["api_calls"], 0)
        self.assertEqual(results["comparison"]["baseline"]["n"], 2)
        self.assertEqual(results["comparison"]["factory"]["n"], 6)
        self.assertIn("mean_delta_e (lower)", results["comparison"]["measured_better_on"])

    def test_verify_manifest_rejects_missing_slot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            manifest = self._manifest_fixture(tmp, factory_ids=SLOT_IDS[:-1])
            out = tmp / "out"
            proc = subprocess.run(
                [sys.executable, "-B", str(RUN_FACTORY), "--verify-manifest", str(manifest), "--output-dir", str(out)],
                cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=_clean_env(),
            )
            self.assertEqual(proc.returncode, 1)
            self.assertFalse((out / "factory_results.json").exists())


if __name__ == "__main__":
    unittest.main()
