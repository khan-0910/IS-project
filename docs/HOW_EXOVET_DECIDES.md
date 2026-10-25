# How EXOVET filters a light curve and reaches a conclusion

Every threshold below is the value in `backend/exovet/config/settings.py`
(or the module named). Worked example throughout: **TIC 261136679, Pi Mensae**,
host of the confirmed transiting super-Earth Pi Men c (P ≈ 6.268 d,
R ≈ 2.0 R⊕). Numbers marked *run 80402ef6fe5b* come from the stored report
`data/results/80402ef6fe5b.json`, produced on 2026-10-25.

The pipeline is a funnel. Each stage throws away a specific kind of thing
that is *not* a planet, and records what it threw away.

---

## 1 · Getting real data — what is accepted at the door

| Filter | Rule | Why |
|---|---|---|
| Source | TESS light curves from MAST via lightkurve; authors preferred SPOC → TESS-SPOC → QLP; cadence preferred 20 s → 2 min → 10 min → 30 min | Use the best-calibrated photometry available |
| Sector cap | at most 6 sectors fetched | Bounded download / compute |
| Quality flags | keep only cadences with `quality == 0` | Drops momentum dumps, scattered light, cosmic rays flagged by the mission |
| Minimum data | refuse below 200 usable points (`insufficient_data`) | A periodic search on fewer points is meaningless |

Errors here are typed (`no_tess_data`, `download_failed`, `insufficient_data`,
`invalid_input`) and shown in the UI as *what happened / why / what to do*.

*Pi Men: 6 sectors (1, 4, 8, 11, 12, 13), 103,773 raw cadences.*

## 2 · Cleaning — removing everything slower or brighter than a transit

1. **NaNs removed**, then each sector is **median-normalized** (flux ≈ 1.0).
2. **Upward-only sigma clipping at 6σ.** Bright outliers (cosmic rays,
   flares) are removed; *downward* points are never clipped, because a transit
   is a downward excursion.
3. **Detrending with wotan's robust biweight filter, window 0.75 d**, with a
   0.5 d edge buffer. The window is far longer than any transit searched for,
   so the trend follows spacecraft drift and stellar rotation but cannot "eat"
   a few-hour dip. The flux is divided by the trend.

*Pi Men: 88,378 cadences kept; ~1,000 non-finite samples per sector removed at
sector edges (recorded as warnings).*

## 3 · Detection — is there a repeating dip at all?

**Transit Least Squares (TLS)** slides a limb-darkened transit template over
trial periods **0.5–30 d** (oversampling 3) and records how much better than
"no transit" each one fits.

Filters inside the search:

| Filter | Rule |
|---|---|
| Search window | longest contiguous block ≤ 90 d and ≤ 6 sectors (TLS cost explodes with baseline); the choice is written to the report |
| Decimation | above 30,000 points, keep every N-th point uniformly (recorded) |
| Depth floor | dips shallower than 100 ppm are not fitted; if nothing fits, the floor is lowered step-wise and the lowering is recorded |
| **Significance** | **SDE ≥ 7** — Signal Detection Efficiency, how many standard deviations the periodogram peak stands above the periodogram's own noise |

Measured at the best period: period, epoch T0, duration, depth, SNR (depth
over its robust uncertainty in the fold), false-alarm probability, odd/even
depth mismatch, number of transits.

*Pi Men: P = 6.26742 d, depth 305 ppm, SDE 33.6, SNR 33.0, FAP 8×10⁻⁵, 11
transits in the 82-day window.*

## 4 · Period checks — is this the *true* period?

TLS can lock onto P/2, P/3 … of the real period: every real transit still
lines up in the fold, so the folded curve looks perfect. Two checks guard
against that.

**Event realization** (`tls_search.event_realization`). For every epoch the
period predicts *and the data cover*, take the mean flux in the central half
of the transit window. The epoch "realizes" if that mean sits at least half
the depth below 1, and also above 3× the noise of that mean. Epochs that fall
in data gaps are skipped, not counted as misses. A true period realizes nearly
every covered epoch; an alias P/N realizes about 1/N of them. If single
transits are too shallow to see one at a time, the test returns *not
testable*; it does not raise a false alarm.

**Harmonic scan.** For multiples 2P … 12P, switch the period only if *all*
of these hold:
- the periodogram has ≥ 50% of the main peak's power there;
- ≥ 3 transits fit in the data;
- the current period realizes ≤ 70% of its epochs;
- the candidate realizes ≥ 80% of its epochs, a gain of ≥ 0.15.

A realization below 30% with no better multiple flags the period
**suspect** in the report.

*Pi Men (run 80402ef6fe5b): realization 91% (10 of 11 covered epochs) →
period consistent. Before the 2026-10-25 fix, the per-point noise floor sat
deeper than the 305 ppm transit and gap epochs counted as misses. That gave
31% and a false "period requires review".*

**Folded duration.** The TLS duration grid can run short, so T14 is also
measured on the binned fold, as one contiguous run of bins below half depth.
*Pi Men: 2.86 h (literature ≈ 2.9–3.0 h). Before the fix, counting unbinned
noisy points gave 12.8 h.*

## 5 · Features — 33 numbers that describe the dip and the star

Served at `/api/features`, each with definition, unit and source. The ones
that discriminate most:

| Feature | Planet looks like | Impostor looks like |
|---|---|---|
| `depth_ppm_log10` | shallow (≲ 1%) | EBs: deep, often > 1% |
| `odd_even_sigma` | ≲ 3σ | **EB**: alternating depths (two different stars) |
| `secondary_ratio` | ≈ 0 | **EB**: visible dip at phase 0.5 |
| `v_shape` (w20/w80 → 0..1) | ≈ 0, flat-bottomed | **Grazing EB**: ≈ 1, V-shaped |
| `duration_hours` | a few hours | grazing / stellar eclipses last systematically longer |
| `contratio`, `numcont`, `Tmag` | low contamination | **Blend**: crowded field, light from neighbours |
| `corr_noise_ratio`, `scatter_6h_ppm`, `coverage`, `fhigh_frac` | white noise | **Systematic**: red noise, edge-of-sector events |

*Pi Men: odd/even 0.55σ, secondary −0.01, v_shape 0.14, contamination
3.7×10⁻⁴. All three EB tests point to a planet.*

## 6 · Classification — five explanations ranked

- **Inputs:** three resampled views (global light curve 2,048 points; local
  window around each transit 256; phase fold 256) plus the 33 standardized
  features.
- **Model:** a small Transformer (d = 128, 4 heads, 4 layers, dropout 0.2),
  softmax over **planet candidate · eclipsing binary · grazing EB ·
  contamination/blend · instrumental/systematic**.
- **Ensemble:** probabilities blended with a logistic-regression head on the
  features — currently 20% Transformer / 80% logistic head. The weight was
  tuned on the validation split only and frozen (`data/models/ensemble.json`).
- **Every feature is a classifier input,** including brightness (`Tmag`) and
  distance (`dist_pc`). Removing them was tried and made the 16-target batch
  clearly worse (see §9), so they stay, guarded by the flag below.
- **Out-of-distribution flag:** any input outside the training range and more
  than 3σ from the training mean is listed in the report, and the class
  probabilities are labelled an extrapolation. *Pi Men: Tmag 5.1 against a
  training range of 6.8–16.0 (z = −3.5) → flagged.*
- **Training protocol:** target-disjoint split (seed 13), class-weighted
  cross-entropy, early stopping on validation macro-F1. The test split is
  touched once, at the end.

## 7 · Uncertainty — when the model must say "I'm not sure"

MC-Dropout keeps dropout switched on and runs **60 stochastic passes**. From
the spread:

| Flag | Rule |
|---|---|
| **Ambiguous** | top-1 − top-2 mean probability < **0.10** |
| **Unstable** | any class's std across passes > **0.15** |
| **Confident** | neither |

It also reports predictive entropy (total uncertainty; max ln 5 = 1.609) and
mutual information (the model's own, epistemic uncertainty). These flags are
never renamed: an ambiguous answer is displayed as ambiguous.

## 8 · Physical fit — what the dip says about the object

A **batman** quadratic-limb-darkened transit model is sampled with **emcee**
MCMC (32 walkers × 3,000 steps, 1,000 burn-in; period fixed at the TLS
value; per-event baselines). It returns Rp/R★, a/R★, impact parameter, T0
offset and limb darkening, each as median with 16th/84th-percentile errors.

*Pi Men: Rp/R★ = 0.0165 (−0.00014 / +0.00019), b = 0.15. With the TIC stellar
radius of 1.149 R☉ that is ≈ 2.1 R⊕, against the published ≈ 2.0 R⊕.*

## 9 · The conclusion — and how much to trust each part

The report combines the stages but does not overrule them. Read it in this
order:

1. **Is there a signal?** SDE ≥ 7 and a period whose predicted transits are
   actually observed → a real periodic dip. *This part is reliable.*
2. **What is it physically?** Odd/even, secondary, V-shape, depth and the
   fitted radius ratio are direct measurements.
3. **What does the AI think?** A ranking plus a confidence flag — a vetting
   hint, not a verdict.

### Honest state of the classifier (2026-10-25)

- **V-shape repaired and model retrained (2026-10-25).** The old
  `v_shape_metric` read the depth off the single lowest bin, so on shallow
  noisy transits it saturated at 1.0: 68% of training planets were labelled
  "triangular". It now fits a trapezoid to the binned fold and returns "not
  measurable" when the dip is under 3× the bin noise. Planets at 1.0: 68% →
  1%. The repair also corrected the EB / grazing-EB labels, which are defined
  by that feature (train EB 11 → 35).
- Retrained checkpoint, test macro-F1 on the same 45 held-out targets
  (±0.1 from sample size alone): Transformer alone **0.221**, logistic head
  **0.314**, **served blend 0.318** (Transformer weight 0.2, tuned on
  validation only). The previous checkpoint scored 0.174 / 0.505 / 0.350, but
  its test labels contained the V-shape artifact, so the numbers are not
  directly comparable.
- The fair before/after is the 16-target ExoFOP batch (same targets, same
  catalog labels): strict accuracy **44% → 62.5%**, relaxed **81% → 75%**,
  macro-F1 **0.47 → 0.54**, ambiguous predictions **19% → 44%**
  (`verify_91b00b3ce159` → `verify_a1ee24d497f0`).
- After the V-shape fix Pi Mensae was still called an eclipsing binary (46%,
  "confident"): a brightness shortcut. In training, eclipsing binaries are
  brighter than planet candidates on average (a selection effect); Pi Men
  (Tmag 5.1) is 3.5σ brighter than any training star, and the logistic head
  turned that one feature into +3.0 logits toward EB.
- **Brightness and distance removed from the classifier** (tried, then
  dropped). Pi Mensae became ambiguous (eclipsing binary 31.7% vs planet
  candidate 22.3%), but the 16-target batch got clearly worse:

  | model | strict | relaxed | macro-F1 | ambiguous |
  |---|---|---|---|---|
  | original | 44% | 81% | 0.47 | 19% |
  | V-shape fixed | 62.5% | 75% | 0.54 | 44% |
  | + brightness/distance removed | 44% | 56% | 0.36 | 50% |
  | V-shape fixed + OOD flag (**served**) | 62.5% | 75% | 0.54 | 37.5% |

  Removing them missed all four planets in the batch. Brightness is a
  shortcut that genuinely helps on stars like the training set and badly
  misleads on stars unlike it. With 16 targets each one moves accuracy ~6
  points, so these differences are suggestive, not definitive.
- **What is served:** the V-shape-fixed model, which uses brightness, plus
  the out-of-distribution flag. Pi Mensae is still labelled an eclipsing
  binary (45.7%), but the report now marks it **OUT OF DISTRIBUTION**
  because it is brighter than every training star, so the label is shown as
  an extrapolation, not silently trusted. The served model finds 3 of the 4
  batch planets.

**Bottom line:** trust EXOVET's detection, period validation and physical fit
as measurements. Treat its class label as a hint, and the ambiguity flag as
the most important part of that hint.
