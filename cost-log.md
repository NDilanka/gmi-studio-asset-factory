# Day 6 Cost Log: Reusable Brand & UI Asset Factory in GMI Studio

**GMI-hosted models only, not the whole market.** This log does not compare GMI Cloud against any other provider.

Media list prices, token-priced LLM estimates, and observed GMI account credit deltas are reported in separate sections below. They are different measurements and are never added together. Cash balance (`balance_usd`) and promotion credit (`promotion_credit_usd`) are separate pools and are never summed. Session-recorded MCP snapshots and request IDs come from live MCP/API responses during the `2026-11-25` run.

## 1. Itemized media list prices (GMI MCP)

Every paid media call followed the order `get_model` -> `estimate_generation` -> user spend cap approval (`$9.50` cap) -> `submit_generation`.

### A. Asset Factory live run (`seedream-5.0-pro`, 8 successful images)

`reve-create-20250915` and `reve-remix-20250915` reported `available: false` in the multimodal API during this run, and a single probe against `reve-2-1` failed with HTTP 503 (`upstream_unavailable`, not charged). Therefore the live API-parity run executed on `seedream-5.0-pro` (supports text-to-image anchors and multi-image reference remixing with up to 14 input images at `$0.085` list price per image).

| # | Output | Stage | Size | Request ID | Status | List price (USD) |
|---|---|---|---|---|---|---:|
| 1 | `assets/baseline/baseline_dashboard.png` | Baseline (unbatched) | `2720x1536` | `d68abf5f-04eb-4382-a452-f7f00aec228a` | `success` | `0.085` |
| 2 | `assets/baseline/baseline_packaging.png` | Baseline (unbatched) | `2496x1664` | `611b845d-7b9c-4537-87ec-f44bc8396dca` | `success` | `0.085` |
| 3 | `assets/factory/anchor_hardware.png` | Factory Stage 1 (Anchor) | `2720x1536` | `15336599-763b-4110-9ab3-36becb3e0661` | `success` | `0.085` |
| 4 | `assets/factory/anchor_interface.png` | Factory Stage 1 (Anchor) | `2720x1536` | `e3c3f58c-7a94-421a-b793-0dd5e814b519` | `success` | `0.085` |
| 5 | `assets/factory/asset_app_icon.png` | Factory Stage 2 (2-ref Remix) | `2048x2048` | `0ae6960f-2c53-4e7b-aedd-198eebb319ac` | `success` | `0.085` |
| 6 | `assets/factory/asset_dashboard_shell.png` | Factory Stage 2 (2-ref Remix) | `2720x1536` | `dc13f013-774a-4109-a1eb-fdd6c5e03b42` | `success` | `0.085` |
| 7 | `assets/factory/asset_packaging.png` | Factory Stage 2 (2-ref Remix) | `2496x1664` | `800becaf-baf4-4ed0-9e7b-8b1b4f01df54` | `success` | `0.085` |
| 8 | `assets/factory/asset_social_banner.png` | Factory Stage 2 (2-ref Remix) | `2720x1536` | `c0dcda51-0672-496e-9324-00d6a6b27a9f` | `success` | `0.085` |

- Factory 6-image subtotal (#3 to #8): **`$0.510` USD (list price)**
- Baseline 2-image subtotal (#1 to #2): **`$0.170` USD (list price)**
- All 8 Asset Factory images subtotal: **`$0.680` USD (list price)**

### B. Launch video, music, and hero still (6 successful generations)

| # | Output | Model | Requested settings (delivered format) | Request ID | Status | List price (USD) |
|---|---|---|---|---|---|---:|
| 9 | `video/raw/clip1_anchor_object.mp4` | `veo-3.1-generate-001` | 1080p, 8 s, 16:9, `generateAudio: false` (`1920x1080`, 24 fps, 8.0 s) | `04ca7cd2-469d-41da-8172-69b8af821c7c` | `success` | `1.600` |
| 10 | `video/raw/clip2_style_drift.mp4` | `veo-3.1-generate-001` | 1080p, 8 s, 16:9, `generateAudio: false` (`1920x1080`, 24 fps, 8.0 s) | `4c5e15a2-0df8-4c68-8a58-021916542188` | `success` | `1.600` |
| 11 | `video/raw/clip3_precision_grid.mp4` | `veo-3.1-generate-001` | 1080p, 8 s, 16:9, `generateAudio: false` (`1920x1080`, 24 fps, 8.0 s) | `a6549420-1b0e-4c42-8806-972b290f22d6` | `success` | `1.600` |
| 12 | `video/raw/clip4_calm_showcase.mp4` | `veo-3.1-generate-001` | 1080p, 8 s, 16:9, `generateAudio: false` (`1920x1080`, 24 fps, 8.0 s) | `d2423afb-a22f-45d7-bf5d-05e0358979b6` | `success` | `1.600` |
| 13 | `video/raw/progressive_house.mp3` | `minimax-music-3.0` | 44.1 kHz, 256 kbps MP3 (`159.81 s`, stereo; `36s-96s` used in final video) | `d1bcbddc-35a8-425f-a9ac-81c4af0a1456` | `success` | `0.150` |
| 14 | `video/raw/post2_hero_raw.png` | `gemini-3-pro-image` | Requested `image_size: "2K"`, `aspect_ratio: "16:9"`, PNG (delivered `1376x768` PNG) | `f34dbfc1-e0c1-4c31-ac81-d4232420bd27` | `success` | `0.134` |

Note on `veo-3.1-generate-001` rate: GMI's public model card lists `$0.40/s` (the audio-enabled rate). Live `gmi___estimate_generation` for `veo-3.1-generate-001` with `durationSeconds: 8`, `resolution: "1080p"`, `aspectRatio: "16:9"`, `generateAudio: false` returned `price_micro_usd: 1600000` (`$1.60`, i.e. `$0.20/s` with audio disabled), which is consistent with the observed promotion credit delta below.

### C. Failed requests (claimed unbilled; not list prices)

| Model | Purpose | Request ID | Failure reason | Billed charge (USD) |
|---|---|---|---|---:|
| `reve-2-1` | Availability probe | `12ae487a-edcd-47a8-a5ea-8793b2799227` | `upstream_unavailable` (HTTP 503) | `0.000` |
| `seedream-5.0-pro` | `asset_packaging` attempt 1 | `26fa8f06-dce1-43f1-ab98-1713255ed7cc` | Transient backend error (HTTP 400); retry succeeded (`800becaf-baf4-4ed0-9e7b-8b1b4f01df54`) | `0.000` |

Subtotals (14 successful media generations):
- Veo 3.1 video (4 generations, #9 to #12, `generateAudio: false` at `$1.600` MCP list estimate each): `$6.400`
- Asset Factory + Baseline images (`seedream-5.0-pro`, 8 generations, #1 to #8): `$0.680`
- Music (`minimax-music-3.0`, 1 generation, #13): `$0.150`
- Hero still (`gemini-3-pro-image`, 1 generation, #14; archived locally, real baseline-vs-factory comparison used in `factory-card.png`): `$0.134`
- **Media total (14 successful generations): `$7.364` USD (MCP list price estimate)**

## 2. Live LLM Node expansion (token-priced estimate)

Pricing verified via `gmi___get_model(model_id="zai-org/GLM-5.3-Flash")` on `2026-11-25`: prompt `$0.000000150/token`, completion `$0.000000500/token`, organization `discount_to_user = 0.50` (50%).

| Call | Model | Prompt tokens | Completion tokens | Total tokens | List estimate (USD) | After-discount estimate (USD) | Outcome |
|---|---|---:|---:|---:|---:|---:|---|
| Attempt 1 | `zai-org/GLM-5.3-Flash` | not logged | not logged | not logged | not logged | not logged | Rejected before usage persistence (valid JSON with flat string values per slot; prompted parser update in `factory_core.py`) |
| Accepted (`outputs/expanded_prompts.json`) | `zai-org/GLM-5.3-Flash` | `1,069` | `968` | `2,037` | `$0.000644` | `$0.000322` | `finish_reason: "stop"`, `latency_ms: 51711`, 6/6 slots parsed |

## 3. Observed GMI account-window credit deltas

Account snapshots read via `gmi___get_account_summary` on `2026-11-25` (reported to the cent; cash balance and promotion credit are separate pools and are never summed):

| Snapshot | `balance_usd` | `promotion_credit_usd` | Window delta |
|---|---:|---:|---:|
| Before Day 6 media and LLM calls | `0.00` | `16.27` | — |
| After all Day 6 media and LLM calls | `0.00` | `9.24` | `-7.03` USD |

- Observed Day 6 promotion credit delta (`-$7.03`) is **`$2.47` under** the user-approved `$9.50` cap.
- The difference between the itemized media list total (`$7.364`) and the observed credit delta (`$7.03`) is not reconciled against a per-request server invoice; plausible explanations include organization discounts or billing settlement timing on the final image calls.

## 4. Local offline checks (zero API spend)

- `python -m unittest test_factory.py -v`: 28 offline unit tests (`$0.00`).
- `python run_factory.py --dry-run`: workflow graph validation + synthetic CIELAB checks (`$0.00`).
- `python run_factory.py --verify-manifest outputs/asset_manifest.json`: offline CIELAB measurement on the 8 real generated PNGs (`$0.00`).
- Local card and video rendering (`scripts/render_card.py`, `scripts/render_video.py`): `$0.00`.
