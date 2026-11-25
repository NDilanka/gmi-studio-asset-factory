"""Deterministic verification engine for the DATUM-01 asset factory.

Standard library plus Pillow. Nothing in this module touches the network.

Public API
    validate_workflow_json(path) -> dict
    srgb_to_lab(r, g, b) -> (L, a, b)                 sRGB 0..255 -> CIELAB (D65)
    delta_e76(lab_a, lab_b) -> float
    analyze_image_brand_fit(image_path, palette_hex_dict, expected_aspect_str) -> dict
    compare_baseline_vs_factory(baseline_metrics, factory_metrics) -> dict
    parse_llm_spec(raw_text, expected_slot_ids) -> dict
    validate_brief(brief) -> list[str]
    load_workflow_parameter(path, node_type, name) -> value
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from PIL import Image

RGB = Tuple[int, int, int]
Lab = Tuple[float, float, float]

# ---------------------------------------------------------------------------
# Locked brand constants
# ---------------------------------------------------------------------------
LOCKED_PALETTE: Dict[str, str] = {
    "alabaster": "#F5F3EE",
    "stone": "#E8E3D9",
    "aluminum": "#D8D4CC",
    "ink": "#18181B",
    "terra": "#C84B31",
}
TERRACOTTA_HEX = LOCKED_PALETTE["terra"]

SLOT_IDS: Tuple[str, ...] = (
    "anchor_hardware",
    "anchor_interface",
    "asset_app_icon",
    "asset_dashboard_shell",
    "asset_packaging",
    "asset_social_banner",
)
EXPECTED_STAGE: Dict[str, str] = {
    "anchor_hardware": "anchor",
    "anchor_interface": "anchor",
    "asset_app_icon": "remix",
    "asset_dashboard_shell": "remix",
    "asset_packaging": "remix",
    "asset_social_banner": "remix",
}
BASELINE_SLOT_IDS: Tuple[str, ...] = ("baseline_dashboard", "baseline_packaging")
BASELINE_POLICY: Dict[str, Any] = {
    "prompt_source": "raw_baseline_brief",
    "llm_style_lock": False,
    "reference_batch": False,
}

# Brand-fit thresholds used by analyze_image_brand_fit.
ASPECT_TOLERANCE = 0.02
PALETTE_DELTA_E_MAX = 18.0
CHROMA_OUTLIER_MIN_CHROMA = 32.0
CHROMA_OUTLIER_MIN_DELTA_E = 20.0
SAMPLE_LONG_EDGE = 160
DOMINANT_TOP_N = 5
DOMINANT_BUCKET = 32

# GMI Studio graph vocabulary (see workflows/*.studio.json).
ROLE_BY_NODE_TYPE: Dict[str, str] = {
    "LLM Node": "llm",
    "Reve Create": "anchor",
    "Seedream 5.0 Pro Create": "anchor",
    "GMI Image Upload": "upload",
    "GMI Batch Images": "batch",
    "Reve Remix": "remix",
    "Seedream 5.0 Pro Remix": "remix",
    "Save Image": "save",
}
FAMILY_BY_NODE_TYPE: Dict[str, str] = {
    "Reve Create": "reve",
    "Reve Remix": "reve",
    "Seedream 5.0 Pro Create": "seedream",
    "Seedream 5.0 Pro Remix": "seedream",
}
REQUIRED_ROLE_MINIMUM: Dict[str, int] = {
    "llm": 1,
    "anchor": 2,
    "upload": 2,
    "batch": 1,
    "remix": 4,
    "save": 6,
}
REQUIRED_TEXT_PARAMETERS: Dict[str, Tuple[str, ...]] = {
    "llm": ("prompt", "system_prompt"),
    "anchor": ("prompt",),
    "remix": ("prompt",),
    "upload": ("image",),
    "save": ("filename_prefix",),
}

# D65 reference white (CIE 1931 2-degree), normalised so Y = 1.
D65_WHITE = (0.95047, 1.0, 1.08883)
# IEC 61966-2-1 linear sRGB -> CIE XYZ matrix (D65).
SRGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
_LAB_DELTA = 6.0 / 29.0
_LAB_EPSILON = _LAB_DELTA ** 3

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")
_ASPECT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$")
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)\r?\n?```$", re.DOTALL)


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------
def hex_to_rgb(value: str) -> RGB:
    """Parse '#RRGGBB' or 'RRGGBB' into an (r, g, b) tuple of 0..255 ints."""
    match = _HEX_RE.match(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"not a 6-digit hex colour: {value!r}")
    digits = match.group(1)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def _linearise(channel: float) -> float:
    c = channel / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _lab_f(t: float) -> float:
    if t > _LAB_EPSILON:
        return t ** (1.0 / 3.0)
    return t / (3.0 * _LAB_DELTA ** 2) + 4.0 / 29.0


def srgb_to_lab(r: float, g: float, b: float) -> Lab:
    """sRGB (0..255 per channel) -> CIELAB (L*, a*, b*) under D65.

    Steps: IEC 61966-2-1 transfer function, linear RGB -> XYZ (sRGB/D65
    matrix), normalise by the D65 white point, then the CIE L*a*b* nonlinearity.
    """
    for label, value in (("r", r), ("g", g), ("b", b)):
        if not 0.0 <= float(value) <= 255.0:
            raise ValueError(f"sRGB channel {label}={value!r} is outside 0..255")
    rl = _linearise(float(r))
    gl = _linearise(float(g))
    bl = _linearise(float(b))
    m = SRGB_TO_XYZ
    x = m[0][0] * rl + m[0][1] * gl + m[0][2] * bl
    y = m[1][0] * rl + m[1][1] * gl + m[1][2] * bl
    z = m[2][0] * rl + m[2][1] * gl + m[2][2] * bl
    fx = _lab_f(x / D65_WHITE[0])
    fy = _lab_f(y / D65_WHITE[1])
    fz = _lab_f(z / D65_WHITE[2])
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


@lru_cache(maxsize=65536)
def _lab_of_rgb(rgb: RGB) -> Lab:
    return srgb_to_lab(rgb[0], rgb[1], rgb[2])


def delta_e76(lab_a: Lab, lab_b: Lab) -> float:
    """CIE 1976 colour difference (Euclidean distance in CIELAB)."""
    return math.dist(lab_a, lab_b)


def parse_aspect(value: Any) -> float:
    """'16:9' -> 1.7777... Raises ValueError for anything else."""
    text = value.strip() if isinstance(value, str) else ""
    match = _ASPECT_RE.match(text)
    if match is None:
        raise ValueError(f"aspect must look like '16:9', got {value!r}")
    width, height = float(match.group(1)), float(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError(f"aspect parts must be positive, got {value!r}")
    return width / height


def _r(value: float, places: int = 6) -> float:
    return round(float(value), places)


def _palette_lab(palette_hex: Mapping[str, str]) -> List[Tuple[str, Lab]]:
    if not palette_hex:
        raise ValueError("palette_hex_dict is empty")
    return [(name, _lab_of_rgb(hex_to_rgb(hex_value))) for name, hex_value in sorted(palette_hex.items())]


def _dominant_hex(buckets: Counter) -> List[str]:
    ranked = sorted(buckets.items(), key=lambda item: (-item[1], item[0]))[:DOMINANT_TOP_N]
    swatches = []
    for (qr, qg, qb), _count in ranked:
        centre = tuple(min(255, q * DOMINANT_BUCKET + DOMINANT_BUCKET // 2) for q in (qr, qg, qb))
        swatches.append("#%02X%02X%02X" % centre)
    return swatches


# ---------------------------------------------------------------------------
# Brand fit on images
# ---------------------------------------------------------------------------
def analyze_image_brand_fit(image_path, palette_hex_dict, expected_aspect_str) -> Dict[str, Any]:
    """Measure one image against the locked palette.

    The image is converted to RGB and downsampled so its long edge is at most
    SAMPLE_LONG_EDGE pixels with BILINEAR resampling. Every sampled pixel is then
    measured in CIELAB (D65):

    - mean_delta_e: mean over pixels of the minimum delta E76 to any palette swatch.
    - palette_adherence_pct: percent of pixels with that minimum <= 18.0.
    - chroma_outlier_pct: percent of pixels with chroma C* > 32 AND delta E76 > 20
      from the terracotta accent. Off-brand saturated colour (neon cyan, magenta)
      counts here. Blends between the accent and other swatches can also count,
      because they are saturated and far from the accent.
    - mean_lightness_l: mean L* over sampled pixels.
    - aspect_ok: |actual/expected - 1| <= 2 percent.
    """
    path = Path(image_path)
    expected = parse_aspect(expected_aspect_str)
    palette = _palette_lab(palette_hex_dict)
    terra_lab = _lab_of_rgb(hex_to_rgb(TERRACOTTA_HEX))

    with Image.open(path) as opened:
        rgb = opened.convert("RGB")
    width, height = rgb.size
    rgb.thumbnail((SAMPLE_LONG_EDGE, SAMPLE_LONG_EDGE), Image.Resampling.BILINEAR)
    data = rgb.tobytes()
    pixels = list(zip(data[0::3], data[1::3], data[2::3]))
    total = len(pixels)

    nearest_sum = 0.0
    palette_hits = 0
    chroma_outliers = 0
    terra_hits = 0
    lightness_sum = 0.0
    buckets: Counter = Counter()
    for pixel in pixels:
        lab = _lab_of_rgb(pixel)
        nearest = min(delta_e76(lab, p_lab) for _name, p_lab in palette)
        nearest_sum += nearest
        if nearest <= PALETTE_DELTA_E_MAX:
            palette_hits += 1
        chroma = math.hypot(lab[1], lab[2])
        terra_de = delta_e76(lab, terra_lab)
        if chroma > CHROMA_OUTLIER_MIN_CHROMA and terra_de > CHROMA_OUTLIER_MIN_DELTA_E:
            chroma_outliers += 1
        if chroma > 25.0 and terra_de <= 25.0:
            terra_hits += 1
        lightness_sum += lab[0]
        buckets[(pixel[0] // DOMINANT_BUCKET, pixel[1] // DOMINANT_BUCKET, pixel[2] // DOMINANT_BUCKET)] += 1

    actual = width / height
    deviation = abs(actual / expected - 1.0)
    return {
        "image_path": str(path),
        "width": width,
        "height": height,
        "sample_width": rgb.width,
        "sample_height": rgb.height,
        "sampled_pixels": total,
        "actual_aspect": _r(actual),
        "expected_aspect": _r(expected),
        "aspect_deviation_pct": _r(100.0 * deviation),
        "aspect_ok": deviation <= ASPECT_TOLERANCE,
        "mean_delta_e": _r(nearest_sum / total),
        "palette_adherence_pct": _r(100.0 * palette_hits / total),
        "chroma_outlier_pct": _r(100.0 * chroma_outliers / total),
        "terra_accent_pct": _r(100.0 * terra_hits / total),
        "has_terra_accent": terra_hits > 0,
        "mean_lightness_l": _r(lightness_sum / total),
        "dominant_hex": _dominant_hex(buckets),
    }


# ---------------------------------------------------------------------------
# Baseline versus factory
# ---------------------------------------------------------------------------
_COMPARED_KEYS = ("mean_delta_e", "palette_adherence_pct", "chroma_outlier_pct", "mean_lightness_l")


def _aggregate(metrics, label: str) -> Dict[str, Any]:
    items = list(metrics)
    if not items:
        raise ValueError(f"{label}: at least one image metric is required")
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}[{index}] must be a metrics object")
        missing = [key for key in _COMPARED_KEYS if key not in item]
        if missing:
            raise ValueError(f"{label}[{index}] is missing {missing}")
    column = {key: [float(item[key]) for item in items] for key in _COMPARED_KEYS}
    lightness = column["mean_lightness_l"]
    return {
        "n": len(items),
        "mean_delta_e": statistics.fmean(column["mean_delta_e"]),
        "mean_palette_adherence_pct": statistics.fmean(column["palette_adherence_pct"]),
        "mean_chroma_outlier_pct": statistics.fmean(column["chroma_outlier_pct"]),
        "mean_lightness_l": statistics.fmean(lightness),
        "lightness_std_dev": statistics.stdev(lightness) if len(lightness) > 1 else 0.0,
        "aspect_ok_count": sum(1 for item in items if item.get("aspect_ok") is True),
        "terra_accent_present_count": sum(1 for item in items if item.get("has_terra_accent") is True),
    }


def _round_all(mapping: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: (_r(value) if isinstance(value, float) else value) for key, value in mapping.items()}


def compare_baseline_vs_factory(baseline_metrics, factory_metrics) -> Dict[str, Any]:
    """Aggregate per-image metrics (from analyze_image_brand_fit) for each group.

    Deltas are factory minus baseline. reduction_pct is (baseline - factory) / baseline * 100
    and is None when the baseline value is zero. measured_better_on lists only the
    metrics where the factory value is actually better. Nothing is rounded before
    aggregation; output values are rounded to 6 decimal places.
    """
    base = _aggregate(baseline_metrics, "baseline")
    fact = _aggregate(factory_metrics, "factory")
    delta_keys = ("mean_delta_e", "mean_palette_adherence_pct", "mean_chroma_outlier_pct", "mean_lightness_l", "lightness_std_dev")
    delta = {key: fact[key] - base[key] for key in delta_keys}
    reduction = {}
    for key in ("mean_delta_e", "mean_chroma_outlier_pct", "lightness_std_dev"):
        reduction[key] = None if base[key] == 0 else 100.0 * (base[key] - fact[key]) / base[key]
    better = []
    if fact["mean_delta_e"] < base["mean_delta_e"]:
        better.append("mean_delta_e (lower)")
    if fact["mean_palette_adherence_pct"] > base["mean_palette_adherence_pct"]:
        better.append("mean_palette_adherence_pct (higher)")
    if fact["mean_chroma_outlier_pct"] < base["mean_chroma_outlier_pct"]:
        better.append("mean_chroma_outlier_pct (lower)")
    if base["n"] > 1 and fact["n"] > 1 and fact["lightness_std_dev"] < base["lightness_std_dev"]:
        better.append("lightness_std_dev (lower)")
    if (fact["terra_accent_present_count"] / fact["n"]) > (base["terra_accent_present_count"] / base["n"]):
        better.append("terra_accent_coverage (higher)")
    return {
        "baseline": _round_all(base),
        "factory": _round_all(fact),
        "delta_factory_minus_baseline": _round_all(delta),
        "reduction_pct": {key: (None if value is None else _r(value)) for key, value in reduction.items()},
        "measured_better_on": better,
        "note": (
            "Measured on a 160 px BILINEAR sample. lightness_std_dev is the sample standard "
            "deviation of per-image mean L*. Deltas are factory minus baseline."
        ),
    }


# ---------------------------------------------------------------------------
# LLM reply parsing
# ---------------------------------------------------------------------------
def parse_llm_spec(raw_text, expected_slot_ids) -> Dict[str, Any]:
    """Parse the LLM reply into {slot_id: {"prompt": ..., ...}}.

    Accepts a bare JSON object or one wrapped in a single markdown fence. Raises
    ValueError when the reply is not JSON, is not an object, is missing an expected
    slot, has an unexpected key, or has an empty prompt for any slot.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("LLM reply is empty")
    expected = list(expected_slot_ids)
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("expected_slot_ids must be a non-empty list of unique ids")

    stripped = raw_text.strip()
    fenced = False
    fence = _FENCE_RE.match(stripped)
    if fence is not None:
        fenced = True
        stripped = fence.group(1).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM reply is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})") from exc
    if not isinstance(data, dict):
        raise ValueError("LLM reply must be a JSON object keyed by slot id")

    missing = [slot for slot in expected if slot not in data]
    if missing:
        raise ValueError("LLM reply is missing slots: " + ", ".join(missing))
    unexpected = sorted(str(key) for key in data if key not in expected)
    if unexpected:
        raise ValueError("LLM reply has unexpected keys: " + ", ".join(unexpected))

    slots: Dict[str, Dict[str, Any]] = {}
    for slot in expected:
        value = data[slot]
        if isinstance(value, str):
            if not value.strip():
                raise ValueError(f"slot {slot} has no non-empty prompt")
            slots[slot] = {"prompt": value.strip()}
        elif isinstance(value, dict):
            prompt = value.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"slot {slot} has no non-empty prompt")
            slots[slot] = dict(value, prompt=prompt.strip())
        else:
            raise ValueError(f"slot {slot} must be a JSON object or string")
    return {"slots": slots, "fenced": fenced, "slot_count": len(slots)}


# ---------------------------------------------------------------------------
# Workflow graph validation
# ---------------------------------------------------------------------------
def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def node_parameters(node: Mapping[str, Any]) -> Dict[str, Any]:
    """Named widget values, from properties.parameter_names zipped with widgets_values."""
    props = node.get("properties")
    names = props.get("parameter_names") if isinstance(props, dict) else None
    values = node.get("widgets_values")
    if not isinstance(names, list) or not isinstance(values, list) or len(names) != len(values):
        return {}
    if not all(isinstance(name, str) for name in names):
        return {}
    return dict(zip(names, values))


def _role_of(node: Mapping[str, Any]):
    node_type = node.get("type")
    return ROLE_BY_NODE_TYPE.get(node_type) if isinstance(node_type, str) else None


def _family_of(node: Mapping[str, Any]):
    node_type = node.get("type")
    return FAMILY_BY_NODE_TYPE.get(node_type) if isinstance(node_type, str) else None


def load_workflow_parameter(path, node_type: str, name: str):
    """Return one named parameter from the first node of the given type."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for node in _as_list(data.get("nodes")):
        if isinstance(node, dict) and node.get("type") == node_type:
            params = node_parameters(node)
            if name not in params:
                raise ValueError(f"{node_type} node has no parameter {name!r}")
            return params[name]
    raise ValueError(f"no {node_type} node in {path}")


def validate_workflow_json(path) -> Dict[str, Any]:
    """Check a ComfyUI / GMI Studio UI-format graph.

    Checks: every link's origin and target node exists and its slot index is in
    range; every link is listed by its origin output and referenced by its target
    input; no node claims a link that does not point at it; no cycles; the required
    stages are present (see REQUIRED_ROLE_MINIMUM); every remix node takes its IMAGE
    from GMI Batch Images; every upload feeds the batch node; every Save Image is fed
    by a generation node; required text parameters are non-empty; parameter_names
    line up with widgets_values; and extra.studio_notes exists.

    Returns a report dict. It does not raise on bad graphs; check report["ok"].
    """
    report: Dict[str, Any] = {
        "path": str(path),
        "ok": False,
        "node_count": 0,
        "link_count": 0,
        "dangling_link_count": 0,
        "dangling_links": [],
        "errors": [],
        "stage_summary": {},
        "generation_family": None,
        "remix_fanout_from_batch": False,
        "has_studio_notes": False,
    }
    errors: List[str] = report["errors"]
    dangling: List[str] = report["dangling_links"]

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"cannot read workflow JSON: {exc}")
        return report
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("links"), list):
        errors.append("workflow must be an object with list fields 'nodes' and 'links'")
        return report

    nodes: Dict[int, Dict[str, Any]] = {}
    for raw in data["nodes"]:
        node_id = raw.get("id") if isinstance(raw, dict) else None
        if type(node_id) is not int:
            errors.append(f"node without an integer id: {str(raw)[:80]}")
            continue
        if node_id in nodes:
            errors.append(f"duplicate node id {node_id}")
            continue
        nodes[node_id] = raw

    links: Dict[int, Tuple[int, int, int, int]] = {}
    for raw in data["links"]:
        well_formed = isinstance(raw, list) and len(raw) == 6 and all(type(v) is int for v in raw[:5])
        if not well_formed:
            dangling.append(f"malformed link record: {str(raw)[:80]}")
            continue
        link_id, origin, origin_slot, target, target_slot, _link_type = raw
        if link_id in links:
            errors.append(f"duplicate link id {link_id}")
            continue
        problems = []
        origin_node = nodes.get(origin)
        target_node = nodes.get(target)
        if origin_node is None:
            problems.append(f"origin node {origin} does not exist")
        elif not 0 <= origin_slot < len(_as_list(origin_node.get("outputs"))):
            problems.append(f"origin slot {origin_slot} is out of range on node {origin}")
        if target_node is None:
            problems.append(f"target node {target} does not exist")
        elif not 0 <= target_slot < len(_as_list(target_node.get("inputs"))):
            problems.append(f"target slot {target_slot} is out of range on node {target}")
        if problems:
            dangling.append(f"link {link_id}: " + "; ".join(problems))
            continue
        links[link_id] = (origin, origin_slot, target, target_slot)

    claimed_outputs = set()
    claimed_inputs = set()
    for node_id, node in nodes.items():
        for slot_index, output in enumerate(_as_list(node.get("outputs"))):
            out_links = _as_list(output.get("links")) if isinstance(output, dict) else []
            for link_id in out_links:
                spec = links.get(link_id) if type(link_id) is int else None
                if spec is None or spec[0] != node_id or spec[1] != slot_index:
                    dangling.append(f"node {node_id} output {slot_index} lists link {link_id}, which does not start there")
                else:
                    claimed_outputs.add(link_id)
        for slot_index, inp in enumerate(_as_list(node.get("inputs"))):
            link_id = inp.get("link") if isinstance(inp, dict) else None
            if link_id is None:
                continue
            spec = links.get(link_id) if type(link_id) is int else None
            if spec is None or spec[2] != node_id or spec[3] != slot_index:
                dangling.append(f"node {node_id} input {slot_index} points at link {link_id}, which does not end there")
            else:
                claimed_inputs.add(link_id)
    for link_id in links:
        if link_id not in claimed_outputs:
            dangling.append(f"link {link_id} is not listed by its origin output")
        if link_id not in claimed_inputs:
            dangling.append(f"link {link_id} is not referenced by its target input")

    roles: Counter = Counter()
    families = set()
    for node_id, node in nodes.items():
        if not isinstance(node.get("type"), str) or not node.get("type"):
            errors.append(f"node {node_id} has no type")
            continue
        role = _role_of(node)
        if role is not None:
            roles[role] += 1
        family = _family_of(node)
        if family is not None:
            families.add(family)
        props = node.get("properties")
        names = props.get("parameter_names") if isinstance(props, dict) else None
        widgets = node.get("widgets_values")
        if names is not None and (not isinstance(names, list) or not isinstance(widgets, list) or len(names) != len(widgets)):
            errors.append(f"node {node_id} ({node.get('type')}): parameter_names and widgets_values do not line up")
        params = node_parameters(node)
        for name in REQUIRED_TEXT_PARAMETERS.get(role, ()):
            if not _nonempty(params.get(name)):
                errors.append(f"node {node_id} ({node.get('type')}): '{name}' is empty")

    report["stage_summary"] = {role: roles[role] for role in sorted(roles)}
    for role, minimum in REQUIRED_ROLE_MINIMUM.items():
        if roles[role] < minimum:
            errors.append(f"missing required stage '{role}': found {roles[role]}, need at least {minimum}")
    if len(families) > 1:
        errors.append("workflow mixes generation families: " + ", ".join(sorted(families)))
    elif families:
        report["generation_family"] = next(iter(families))

    ids_by_role: Dict[str, set] = {role: set() for role in REQUIRED_ROLE_MINIMUM}
    for node_id, node in nodes.items():
        role = _role_of(node)
        if role in ids_by_role:
            ids_by_role[role].add(node_id)

    incoming: Dict[int, set] = {node_id: set() for node_id in nodes}
    outgoing: Dict[int, set] = {node_id: set() for node_id in nodes}
    edges: List[Tuple[int, int]] = []
    for origin, _origin_slot, target, _target_slot in links.values():
        incoming[target].add(origin)
        outgoing[origin].add(target)
        edges.append((origin, target))

    batch_ids = ids_by_role["batch"]
    remix_ids = ids_by_role["remix"]
    generation_ids = ids_by_role["anchor"] | remix_ids
    fanout = bool(remix_ids) and all(incoming[node_id] & batch_ids for node_id in remix_ids)
    report["remix_fanout_from_batch"] = fanout
    if remix_ids and not fanout:
        errors.append("not every remix node takes its reference IMAGE from GMI Batch Images")
    for upload_id in ids_by_role["upload"]:
        if not outgoing[upload_id] & batch_ids:
            errors.append(f"upload node {upload_id} does not feed GMI Batch Images")
    for save_id in ids_by_role["save"]:
        if not incoming[save_id] & generation_ids:
            errors.append(f"save node {save_id} is not fed by an anchor or remix node")

    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    report["has_studio_notes"] = isinstance(extra.get("studio_notes"), dict)
    if not report["has_studio_notes"]:
        errors.append("extra.studio_notes is missing")

    indegree = {node_id: 0 for node_id in nodes}
    adjacency: Dict[int, List[int]] = {node_id: [] for node_id in nodes}
    for origin, target in edges:
        indegree[target] += 1
        adjacency[origin].append(target)
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for nxt in adjacency[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if visited != len(nodes):
        errors.append("workflow graph contains a cycle")

    last_node = data.get("last_node_id")
    if type(last_node) is int and nodes and last_node < max(nodes):
        errors.append(f"last_node_id {last_node} is below the largest node id {max(nodes)}")
    last_link = data.get("last_link_id")
    link_ids = [raw[0] for raw in data["links"] if isinstance(raw, list) and raw and type(raw[0]) is int]
    if type(last_link) is int and link_ids and last_link < max(link_ids):
        errors.append(f"last_link_id {last_link} is below the largest link id {max(link_ids)}")

    report["node_count"] = len(nodes)
    report["link_count"] = len(data["links"])
    report["dangling_link_count"] = len(dangling)
    report["ok"] = not errors and not dangling
    return report


# ---------------------------------------------------------------------------
# Brief validation
# ---------------------------------------------------------------------------
def _check_slot(slot_id: str, entry: Any, expected_stage) -> List[str]:
    if not isinstance(entry, Mapping):
        return [f"slot {slot_id} must be an object"]
    problems: List[str] = []
    if expected_stage is not None and entry.get("stage") != expected_stage:
        problems.append(f"slot {slot_id} stage must be {expected_stage!r}")
    if not _nonempty(entry.get("label")):
        problems.append(f"slot {slot_id} needs a label")
    aspect = entry.get("aspect_ratio")
    size = entry.get("size")
    try:
        expected = parse_aspect(aspect)
    except ValueError as exc:
        return problems + [f"slot {slot_id}: {exc}"]
    match = _SIZE_RE.match(size) if isinstance(size, str) else None
    if match is None:
        return problems + [f"slot {slot_id}: size must look like 2720x1536"]
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return problems + [f"slot {slot_id}: size must be positive"]
    deviation = abs((width / height) / expected - 1.0)
    if deviation > ASPECT_TOLERANCE:
        problems.append(f"slot {slot_id}: size {size} is {deviation * 100:.2f}% off aspect {aspect}")
    return problems


def validate_brief(brief: Any) -> List[str]:
    """Return a list of problems with brand_brief.json. Empty list means valid."""
    if not isinstance(brief, Mapping):
        return ["brief must be a JSON object"]
    problems: List[str] = []
    if brief.get("brand_name") != "DATUM-01":
        problems.append("brand_name must be 'DATUM-01'")
    for key in ("tagline", "raw_baseline_brief", "lighting", "negative_constraints"):
        if not _nonempty(brief.get(key)):
            problems.append(f"{key} must be a non-empty string")
    materials = brief.get("materials")
    if not isinstance(materials, list) or not materials or not all(_nonempty(item) for item in materials):
        problems.append("materials must be a non-empty list of strings")

    palette = brief.get("palette")
    if not isinstance(palette, Mapping) or set(palette) != set(LOCKED_PALETTE):
        problems.append(f"palette must have exactly the keys {sorted(LOCKED_PALETTE)}")
    else:
        for name, locked in LOCKED_PALETTE.items():
            entry = palette[name]
            got = entry.get("hex") if isinstance(entry, Mapping) else None
            if not isinstance(got, str) or got.upper() != locked:
                problems.append(f"palette {name} must be {locked}, found {got!r}")
            if not isinstance(entry, Mapping) or not _nonempty(entry.get("role")):
                problems.append(f"palette {name} needs a role description")

    slots = brief.get("slots")
    if not isinstance(slots, Mapping) or set(slots) != set(SLOT_IDS):
        problems.append(f"slots must have exactly the ids {list(SLOT_IDS)}")
    else:
        for slot_id in SLOT_IDS:
            problems.extend(_check_slot(slot_id, slots[slot_id], EXPECTED_STAGE[slot_id]))

    baseline = brief.get("baseline_slots")
    if not isinstance(baseline, Mapping) or set(baseline) != set(BASELINE_SLOT_IDS):
        problems.append(f"baseline_slots must have exactly the ids {list(BASELINE_SLOT_IDS)}")
    else:
        for slot_id in BASELINE_SLOT_IDS:
            problems.extend(_check_slot(slot_id, baseline[slot_id], None))

    if brief.get("baseline_policy") != BASELINE_POLICY:
        problems.append("baseline_policy must be prompt_source raw_baseline_brief, no style lock, no reference batch")
    return problems
