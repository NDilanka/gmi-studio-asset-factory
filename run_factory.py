#!/usr/bin/env python3
"""DATUM-01 asset factory CLI. Standard library plus Pillow.

Exactly one mode is required:

  --dry-run               Offline. Validates both Studio workflow graphs and the brief, runs the
                          brand-fit engine on synthetic images in a temp directory, and prints a
                          JSON summary. No network, no API calls, $0.00.
  --expand                One GMI Inference chat call (LLM Node model, temperature 0.0) that
                          expands brand_brief.json into six slot prompts and writes
                          outputs/expanded_prompts.json. Needs GMI_API_KEY. This is a paid call.
  --verify-manifest PATH  Offline brand-fit verification of real generated images listed in a
                          manifest JSON. Writes outputs/factory_results.json. No API calls.

Exit codes: 0 pass, 1 a check failed, 2 configuration or usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from factory_core import (
    BASELINE_SLOT_IDS,
    LOCKED_PALETTE,
    SLOT_IDS,
    analyze_image_brand_fit,
    compare_baseline_vs_factory,
    hex_to_rgb,
    load_workflow_parameter,
    parse_aspect,
    parse_llm_spec,
    validate_brief,
    validate_workflow_json,
)

ROOT = Path(__file__).resolve().parent
BRIEF_PATH = ROOT / "brand_brief.json"
MAIN_WORKFLOW = ROOT / "workflows" / "brand-asset-factory.studio.json"
SEEDREAM_WORKFLOW = ROOT / "workflows" / "brand-asset-factory-seedream.studio.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs"

GMI_CHAT_URL = "https://api.gmi-serving.com/v1/chat/completions"
API_KEY_ENV = "GMI_API_KEY"
USER_AGENT = "day6-studio-asset-factory/1.0"
LLM_MODEL = "zai-org/GLM-5.3-Flash"
TEMPERATURE = 0.0
MAX_TOKENS = 2048
REASONING_EFFORT = "low"
TIMEOUT_S = 60.0
MAX_RETRIES = 1
RETRY_BACKOFF_S = 2.0

SYNTHETIC_LONG_EDGE = 320
METRIC_FIELDS = (
    "width",
    "height",
    "aspect_ok",
    "aspect_deviation_pct",
    "mean_delta_e",
    "palette_adherence_pct",
    "chroma_outlier_pct",
    "terra_accent_pct",
    "has_terra_accent",
    "mean_lightness_l",
    "dominant_hex",
)


class GMICallError(RuntimeError):
    def __init__(self, kind: str, message: str, retryable: bool):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _read_brief() -> Tuple[Dict[str, Any], str]:
    raw = BRIEF_PATH.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _palette_from_brief(brief: Dict[str, Any]) -> Dict[str, str]:
    return {name: entry["hex"] for name, entry in brief["palette"].items()}


def _slot_aspect(brief: Dict[str, Any], slot_id: str) -> str:
    if slot_id in brief["slots"]:
        return brief["slots"][slot_id]["aspect_ratio"]
    return brief["baseline_slots"][slot_id]["aspect_ratio"]


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _measure(path: Path, palette: Dict[str, str], aspect: str, group: str, slot_id: str) -> Dict[str, Any]:
    metrics = analyze_image_brand_fit(path, palette, aspect)
    row: Dict[str, Any] = {"group": group, "slot_id": slot_id, "aspect_ratio": aspect}
    for key in METRIC_FIELDS:
        row[key] = metrics[key]
    return row


def _workflow_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "file": _display(Path(report["path"])),
        "ok": report["ok"],
        "node_count": report["node_count"],
        "link_count": report["link_count"],
        "dangling_link_count": report["dangling_link_count"],
        "stage_summary": report["stage_summary"],
        "generation_family": report["generation_family"],
        "remix_fanout_from_batch": report["remix_fanout_from_batch"],
        "errors": report["errors"],
    }


def _canvas_for_aspect(aspect: str) -> Tuple[int, int]:
    ratio = parse_aspect(aspect)
    width = SYNTHETIC_LONG_EDGE
    return width, max(1, round(width / ratio))


# ---------------------------------------------------------------------------
# Synthetic images (dry run and tests). Pure Pillow drawing, no randomness.
# ---------------------------------------------------------------------------
def synthetic_locked_image(path: Path, size: Tuple[int, int]) -> None:
    """Warm-alabaster sheet built only from exact locked palette colours."""
    width, height = size
    canvas = Image.new("RGB", (width, height), hex_to_rgb(LOCKED_PALETTE["alabaster"]))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        [int(width * 0.08), int(height * 0.12), int(width * 0.56), int(height * 0.88)],
        fill=hex_to_rgb(LOCKED_PALETTE["stone"]),
    )
    draw.rectangle(
        [int(width * 0.60), int(height * 0.20), int(width * 0.64), int(height * 0.80)],
        fill=hex_to_rgb(LOCKED_PALETTE["aluminum"]),
    )
    draw.rectangle(
        [int(width * 0.68), int(height * 0.30), int(width * 0.90), int(height * 0.62)],
        fill=hex_to_rgb(LOCKED_PALETTE["ink"]),
    )
    radius = max(2, int(min(width, height) * 0.04))
    cx, cy = int(width * 0.79), int(height * 0.74)
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=hex_to_rgb(LOCKED_PALETTE["terra"]))
    canvas.save(path, format="PNG")


def synthetic_drift_image(path: Path, size: Tuple[int, int]) -> None:
    """Neon cyan field with a magenta disc on near-black: the off-brand drift case."""
    width, height = size
    canvas = Image.new("RGB", (width, height), (10, 10, 18))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, width // 2, height], fill=(0, 255, 255))
    radius = int(min(width, height) * 0.3)
    cx, cy = int(width * 0.72), height // 2
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(255, 0, 255))
    canvas.save(path, format="PNG")


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------
def run_dry_run() -> int:
    try:
        brief, _sha = _read_brief()
    except (OSError, ValueError) as exc:
        print(f"cannot read brand brief: {exc}", file=sys.stderr)
        return 2
    brief_problems = validate_brief(brief)
    reports = [validate_workflow_json(MAIN_WORKFLOW), validate_workflow_json(SEEDREAM_WORKFLOW)]
    try:
        load_workflow_parameter(MAIN_WORKFLOW, "LLM Node", "system_prompt")
        prompt_ok = True
    except ValueError:
        prompt_ok = False

    palette = _palette_from_brief(brief)
    rows: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="datum01-dryrun-") as tmp:
        tmp_dir = Path(tmp)
        drift_path = tmp_dir / "baseline_drift_neon.png"
        synthetic_drift_image(drift_path, _canvas_for_aspect("16:9"))
        rows.append(_measure(drift_path, palette, "16:9", "baseline", "baseline_drift_neon"))
        for slot_id in SLOT_IDS:
            aspect = brief["slots"][slot_id]["aspect_ratio"]
            locked_path = tmp_dir / f"factory_{slot_id}.png"
            synthetic_locked_image(locked_path, _canvas_for_aspect(aspect))
            rows.append(_measure(locked_path, palette, aspect, "factory", slot_id))

    baseline_rows = [row for row in rows if row["group"] == "baseline"]
    factory_rows = [row for row in rows if row["group"] == "factory"]
    comparison = compare_baseline_vs_factory(baseline_rows, factory_rows)
    separation_ok = (
        baseline_rows[0]["mean_delta_e"] > max(row["mean_delta_e"] for row in factory_rows)
        and baseline_rows[0]["chroma_outlier_pct"] > max(row["chroma_outlier_pct"] for row in factory_rows)
    )
    checks = {
        "workflows_ok": all(report["ok"] for report in reports),
        "brief_ok": not brief_problems,
        "llm_system_prompt_ok": prompt_ok,
        "synthetic_aspects_ok": all(row["aspect_ok"] for row in rows),
        "drift_separated_from_locked": separation_ok,
    }
    summary = {
        "mode": "dry-run",
        "api_calls": 0,
        "cost_usd": 0.0,
        "synthetic": True,
        "fixture": {
            "baseline": "1 drifted neon-cyan and magenta image (spec fixture)",
            "factory": "6 locked warm-alabaster images, one per slot, built only from exact palette colours",
        },
        "brief": {
            "brand_name": brief.get("brand_name"),
            "slot_count": len(SLOT_IDS),
            "baseline_slot_count": len(BASELINE_SLOT_IDS),
            "problems": brief_problems,
        },
        "workflows": [_workflow_summary(report) for report in reports],
        "images": rows,
        "comparison": comparison,
        "checks": checks,
        "checks_passed": all(checks.values()),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["checks_passed"] else 1


# ---------------------------------------------------------------------------
# --expand (paid GMI Inference call)
# ---------------------------------------------------------------------------
def _redact(text: str, secret: str) -> str:
    cleaned = text.replace(secret, "[redacted]") if secret else text
    return cleaned[:300]


def _post_chat_once(api_key: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": REASONING_EFFORT,
    }
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    request = urllib.request.Request(
        GMI_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        finally:
            exc.close()
        status = exc.code
        raise GMICallError(f"HTTP {status}", _redact(detail, api_key), status == 429 or status >= 500) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise GMICallError("timeout", "request timed out", True) from None
        raise GMICallError("network", _redact(str(exc.reason), api_key), False) from None
    except TimeoutError:
        raise GMICallError("timeout", "request timed out", True) from None

    try:
        body = json.loads(raw)
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise GMICallError("bad_response", "response had no usable choices[0].message.content", False) from None
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return {
        "content": content if isinstance(content, str) else "",
        "finish_reason": choice.get("finish_reason"),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def call_llm(api_key: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """One chat call with the project retry policy (retry 429, 5xx, timeouts once)."""
    attempts = 0
    started = time.perf_counter()
    while True:
        attempts += 1
        try:
            result = _post_chat_once(api_key, messages)
            break
        except GMICallError as exc:
            if not exc.retryable or attempts > MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_S)
    result["attempts"] = attempts
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def run_expand(output_dir: Path) -> int:
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(f"{API_KEY_ENV} is not set. --expand makes one paid GMI call and needs it.", file=sys.stderr)
        return 2
    try:
        brief, brief_sha = _read_brief()
    except (OSError, ValueError) as exc:
        print(f"cannot read brand brief: {exc}", file=sys.stderr)
        return 2
    problems = validate_brief(brief)
    if problems:
        print("brand brief is invalid: " + "; ".join(problems), file=sys.stderr)
        return 1
    system_prompt = load_workflow_parameter(MAIN_WORKFLOW, "LLM Node", "system_prompt")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(brief, indent=2, ensure_ascii=False)},
    ]
    try:
        reply = call_llm(api_key, messages)
    except GMICallError as exc:
        print(f"GMI call failed ({exc.kind}): {exc.message}", file=sys.stderr)
        return 1

    try:
        spec = parse_llm_spec(reply["content"], SLOT_IDS)
    except ValueError as exc:
        rejected = output_dir / "expanded_prompts.rejected.txt"
        output_dir.mkdir(parents=True, exist_ok=True)
        rejected.write_text(reply["content"], encoding="utf-8")
        print(f"LLM reply rejected: {exc}. Raw reply saved to {_display(rejected)}.", file=sys.stderr)
        return 1

    record = {
        "brand_name": brief["brand_name"],
        "brief_sha256": brief_sha,
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": LLM_MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": REASONING_EFFORT,
        "attempts": reply["attempts"],
        "latency_ms": reply["latency_ms"],
        "finish_reason": reply["finish_reason"],
        "usage": reply["usage"],
        "cost_note": "List-price cost is not computed here. Check the GMI console for billed amounts.",
        "slots": spec["slots"],
    }
    target = output_dir / "expanded_prompts.json"
    _write_json(target, record)
    print(json.dumps({
        "written": _display(target),
        "model": LLM_MODEL,
        "slots": spec["slot_count"],
        "fenced_reply": spec["fenced"],
        "attempts": reply["attempts"],
        "latency_ms": reply["latency_ms"],
        "prompt_tokens": reply["usage"]["prompt_tokens"],
        "completion_tokens": reply["usage"]["completion_tokens"],
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# --verify-manifest (offline)
# ---------------------------------------------------------------------------
def _load_manifest(manifest_path: Path) -> Dict[str, Dict[str, str]]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"baseline", "factory"}:
        raise ValueError("manifest must have exactly the keys 'baseline' and 'factory'")
    for group in ("baseline", "factory"):
        mapping = data[group]
        if not isinstance(mapping, dict) or not all(isinstance(value, str) and value for value in mapping.values()):
            raise ValueError(f"manifest.{group} must map slot ids to image paths")
    return data


def run_verify(manifest_path: Path, output_dir: Path) -> int:
    try:
        manifest = _load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        print(f"cannot read manifest: {exc}", file=sys.stderr)
        return 2
    try:
        brief, brief_sha = _read_brief()
    except (OSError, ValueError) as exc:
        print(f"cannot read brand brief: {exc}", file=sys.stderr)
        return 2

    coverage_problems: List[str] = []
    expected_ids = {"baseline": set(BASELINE_SLOT_IDS), "factory": set(SLOT_IDS)}
    for group in ("baseline", "factory"):
        got = set(manifest[group])
        if got != expected_ids[group]:
            coverage_problems.append(
                f"{group}: missing {sorted(expected_ids[group] - got)}, unexpected {sorted(got - expected_ids[group])}"
            )
    if coverage_problems:
        print("manifest coverage failed: " + "; ".join(coverage_problems), file=sys.stderr)
        return 1

    palette = _palette_from_brief(brief)
    base_dir = manifest_path.resolve().parent
    rows: List[Dict[str, Any]] = []
    missing_files: List[str] = []
    for group, slot_ids in (("baseline", BASELINE_SLOT_IDS), ("factory", SLOT_IDS)):
        for slot_id in slot_ids:
            image = Path(manifest[group][slot_id])
            if not image.is_absolute():
                image = base_dir / image
            if not image.is_file():
                missing_files.append(f"{group}/{slot_id}: {image}")
                continue
            aspect = _slot_aspect(brief, slot_id)
            row = _measure(image, palette, aspect, group, slot_id)
            row["path"] = _display(image)
            rows.append(row)
    if missing_files:
        print("image files not found: " + "; ".join(missing_files), file=sys.stderr)
        return 1

    baseline_rows = [row for row in rows if row["group"] == "baseline"]
    factory_rows = [row for row in rows if row["group"] == "factory"]
    comparison = compare_baseline_vs_factory(baseline_rows, factory_rows)
    workflows = [validate_workflow_json(MAIN_WORKFLOW), validate_workflow_json(SEEDREAM_WORKFLOW)]
    integrity_ok = all(report["ok"] for report in workflows) and not validate_brief(brief)
    results = {
        "brand_name": brief["brand_name"],
        "brief_sha256": brief_sha,
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest": _display(manifest_path),
        "api_calls": 0,
        "cost_usd": 0.0,
        "integrity_ok": integrity_ok,
        "workflows": [_workflow_summary(report) for report in workflows],
        "baseline": baseline_rows,
        "factory": factory_rows,
        "comparison": comparison,
    }
    target = output_dir / "factory_results.json"
    _write_json(target, results)
    print(json.dumps({
        "written": _display(target),
        "integrity_ok": integrity_ok,
        "comparison": comparison,
    }, indent=2, sort_keys=True))
    return 0 if integrity_ok else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="DATUM-01 asset factory (offline checks and one paid expansion call).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="offline checks on synthetic images; no API calls")
    mode.add_argument("--expand", action="store_true", help="one paid GMI LLM call; needs GMI_API_KEY")
    mode.add_argument("--verify-manifest", metavar="PATH", help="offline brand-fit check of generated images listed in PATH")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="where --expand and --verify-manifest write results")
    args = parser.parse_args(argv)

    if args.dry_run:
        return run_dry_run()
    output_dir = Path(args.output_dir)
    if args.expand:
        return run_expand(output_dir)
    return run_verify(Path(args.verify_manifest), output_dir)


if __name__ == "__main__":
    sys.exit(main())
