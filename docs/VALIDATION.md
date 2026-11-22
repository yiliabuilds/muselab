# Validation Status

What has actually been run, what it proved, and what it did not.

**Rule for this document:** a test that did not run is never counted as a pass, and a
generated image that failed its acceptance check is never described as accepted.

Last full run: **2026-11-22 02:07 (local)** · platform `darwin` · Python 3, Pillow + numpy.
Raw output: [`tests/RESULTS.md`](../tests/RESULTS.md).

---

## 1. Test suite result

```
PASS 83 | FAIL 0 | NOT-RUN 7 | ENV-UNSUPPORTED 0 | total 90
```

| Group | Covers |
|---|---|
| 01 | Subject cutout with real alpha, colour preservation, subject localisation, missing-input handling |
| 02 | Pixel-level palette extraction, area share, mood analysis, swatch render, measured-vs-estimated honesty |
| 03 | White/transparent export, JPEG+alpha rejection, soft-edge after resize, `--keep-frame`, pixel-integrity, mask audit & repair |
| 04–06 | Project state, visual profile, design registration, state machine (`draft → selected → approved`), frozen-design protection, patch revisions |
| 07 | Image task registration, fabricated-output rejection, graceful degradation, fidelity notes, project verification |
| 08 | JSON Schema validation |
| 10 | Generation gate: background spec, tone-vs-material separation, relighting compensation, defocus → inconclusive, absent accessory → hard fail, `--strict` exit codes, code/vision/human boundary |
| 11 | Makeup spec validation, makeup revision isolation, five measurable checks, soft vs hard failures, identity non-testability |
| 12 | Eyelid-band checks: coverage, climb height, three-signal balance, under-lid sparkle as observation, ROI provenance |
| 13 | Prompt single-source: derivation on `design-add`, mirror following `revise`, drift detection, `prompt-sync`, authorization scope |

### The 7 NOT-RUN cases — not passes

| Case | Why it did not run |
|---|---|
| 09 photo → three bracelets (with real render) | needs the host model's image tool + credits |
| 09b collection design (multi-image consistency) | needs 4 render credits |
| 09c virtual try-on on a person | needs reference-image editing + a model photo |
| 09d news-driven design | needs live web search; the scripts never invent news |
| 12 makeup real ImageGen render | the suite does not spend credits — run manually, see §2 |
| 12m makeup reference-image route | needs a real face photo + editing ability; none was supplied |
| 12j real-render regression | gated behind `MUSELAB_REAL_RENDER`; not set in the default run |

Two of these (12, 12m) are the entire reference-image route for makeup. **The reference-image
route has never been executed end to end.**

---

## 2. Real image generations that were executed

Four ImageGen calls across three projects. Each was a single call, no automatic retry.

| # | Project | Design | Result | Outcome |
|---|---|---|---|---|
| 1 | `beach-collection-20261118` | `jw_beach_C` v5 (jewelry, creative) | `eddy_v5_product_shot.png` 1024² | **Failed gate** — background off-spec (max ΔE 24.4), teal stones 9–10 L\* dark; structure, bead count and material type correct |
| 2 | `fidelity-test-20261121` | `jw_fid_2912` v2 (product fidelity) | `fid_2912_v2_shot.png` 1024² | **Failed** — the model invented the part of the bracelet that was out of frame, closed the loop, added 2 beads, redrew the clasp and butterfly |
| 3 | `makeup-test-20261121` | `mk_beach_tidal` v11 | `makeup_tidal_v11_real_01.png` 1024² | **Failed** — eyeshadow reached the full lid and the lower lid was coloured; the spec still had stale text |
| 4 | `makeup-test-20261121` | `mk_beach_tidal` v13 | `makeup_tidal_v13_real_01.png` 1024² | **Failed** — hue drifted 35–45° into grey-blue; range, lower lid and skin texture improved |

**0 of 4 real renders passed acceptance.** That is the honest headline. What passed is the
machinery around them: the gates caught the deviations in all four cases, and three of the four
failures produced a concrete spec or prompt fix.

---

## 3. What is verified (automated tests only)

| Capability | Evidence |
|---|---|
| Cutout keeps product colour intact | 01b: 92,158 fully opaque pixels, max channel deviation 0 |
| Colour extraction measures real pixels, never guesses | 02: 5/5 known patches hit, `method=pixel_kmeans`; 02e forces `visual_estimate` when there is no measurement |
| Export never fakes alpha | 03c rejects JPEG+alpha with exit 5; 03d keeps 13,838 semi-transparent pixels after resize |
| Composite has not been secretly retouched | 03g detects post-composite recolouring, 03h detects unintended cropping |
| Approved designs cannot be silently edited | 05f/06 exit 6 without `--consent`; 06c proves v1 is untouched after v2 exists |
| Prompt has one source of truth | 13a derives the mirror, 13b follows revisions, 13c flags hand-edited drift, 13d/e sync and keep history, 13f refuses to excuse a mirror-only edit |
| Gate separates tone shifts from material errors | 10b `tone_shift` vs 10c `material_mismatch`; 10d removes a colour cast before judging |
| Gate refuses to guess | 10f defocus → `unverifiable`, 10i sampling point on background → `inconclusive_low_separation` |
| Eyelid coverage is measured, not assumed | 12a symmetric lower-half → pass, 12b climb 0.425 vs 0.45 drawn, 12c full lid → hard fail |
| Cross-field audit catches contradictions | 12g, 12k, 11l–11n: stale `concept` / `image_prompt` / undeclared colours → exit 7 |

## 4. Beta — implemented, thresholds only loosely calibrated

| Area | Status | Calibration basis |
|---|---|---|
| Eyelid-band thresholds | Beta | 4 synthetic fixtures + 2 real renders. Inner-fold and hooded eyes are not covered |
| `eyelid_balance` three-signal rule | Beta | Requires ≥2 of 3 signals to exceed thresholds; one stray ROI can fake one signal, not three |
| Under-lid specular detection | Beta | Records, never fails — a natural aegyo-sal highlight is pixel-identical to a pearl band |
| Spec audit (`makeup_spec_audit.py`) | Beta | Dictionary + structural diff, **not** semantic auditing. It catches "said the opposite", not "said it differently" |
| Generation gate colour model | Beta | Relighting compensation validated on synthetic casts only |
| Makeup hue fidelity | Beta (weak) | Two renders drifted 35–45° in the same direction. Adding adjectives did not help; the reference-image route is the untested fix |

## 5. Not verified at all

- Reference-image makeup route (tests 12, 12m) — never executed.
- Virtual try-on / on-model wear (09c) — never executed.
- Collection-level multi-image consistency (09b) — never executed.
- Outfit and IP-derivative modules — interface conventions only, no implementation.
- Identity preservation — not automatable with the current toolset; always listed as
  `not_code_testable`.
- News-driven inspiration (09d) — depends on live web search, never run in the suite.

## 6. Environment-unsupported

| Feature | Why | Fallback |
|---|---|---|
| Face landmarks / auto-ROI | no `cv2`, `mediapipe`, `dlib`, `face_recognition` in the test environment | every ROI is declared by a human or a vision model; `--capabilities` reports `available: []` and `roi_source: human_or_vision_declared` |
| Full JSON Schema validation | `jsonschema` optional | falls back to required-field checks |
| Native Vision alpha cutout | macOS 14+ only | PIL-based fallback path |

## 7. How to reproduce

```bash
python3 tests/run_tests.py                     # 90 cases, ~60 s
MUSELAB_REAL_RENDER=/path/to/render.png python3 tests/run_tests.py   # adds the real-render regression
```

`MUSELAB_REAL_RENDER` is a path, not a flag: the suite will not pick a render for you, because
choosing which render counts as "the" regression baseline is a judgement call.
