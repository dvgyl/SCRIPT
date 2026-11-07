# Consequence experiment protocol: first SCRIPT paper

Status: implementation protocol. Approve and timestamp it before the bench results arrive.
Source baseline: repository main, commit `e94cdd4`.
This document specifies the experiment. It does not report experiment results.

## 1. Scope and question

How much does a device difference change apparent model performance?
How much of that change does the bench correction remove?

The experimental unit is a generated participant. A record is one generated session.
The target population is the specified synthetic distribution.
The experiment represents measurement-chain differences only.
It does not represent a person's response to a different surface.
It makes no clinical or mental health claim.

The primary analyses use one feature at a time.
They cover all six features in BENCH_PROTOCOL section 9.1.
A separate sensitivity analysis uses all six features.
A single-feature equivalence limit does not bound a joint model's AUC.

The source requirements are PAPER_PLAN sections 2.1–2.4 and BENCH_PROTOCOL sections 1, 9–12, and 16.
DESIGN sections 7–8 define the reusable evaluation and generator components.
INTENDED_USE limits all interpretation to research.

Every discretionary choice has a judgment identifier, J1–J7 and J9–J12.
Section 12 gives one alternative for each choice.
Source requirements, mathematical definitions, and leakage safeguards are not discretionary choices.

## 2. Estimands and paired comparisons

Generate three versions of each cohort using the same participants, labels, splits, and random numbers.

- `reference`: every record uses the P observation model.
- `uncorrected`: records use the assigned P or W observation model.
- `corrected`: apply the published W-to-P correction to W records only.

Refit the prediction procedure in each version.
Correct both training and test W records before fitting any preprocessing step.
Do not recalibrate on test labels.
The reference uses the same device assignments for defining splits, even though its feature observations all use P.
Thus, device holdout contrasts use identical people and folds in all versions.

For metric M, define these paired contrasts:

```
D_raw = M_uncorrected - M_reference
D_corrected = M_corrected - M_reference
removed = D_raw - D_corrected
absolute_reduction = abs(D_raw) - abs(D_corrected)
```

`removed` gives the signed change caused by correction.
`absolute_reduction` gives movement toward the reference performance.
A negative absolute reduction means that correction increased the discrepancy.
Report these contrasts separately for each metric.
Do not express removal as a percentage when the denominator can approach zero.

Also compare record-wise and participant-wise results on each observation version.
This difference estimates apparent inflation from allowing the same participant in training and testing.
It does not estimate the performance of a deployable model.

The primary summaries are as follows [J1].

| Scenario | Primary summary |
| --- | --- |
| One device for everyone | Participant-wise `D_raw` and `absolute_reduction` in AUC, for all-W against all-P. |
| Train W, test P | W-to-P external-test `D_raw` and `absolute_reduction` in calibration intercept. |
| Device associated with case status | Participant-wise `D_raw` and `absolute_reduction` in AUC under the null effect. |

Area under the receiver operating characteristic curve is abbreviated AUC.
Calibration intercept measures systematic probability error on the log-odds scale.
Report all other metrics alongside these primary summaries.
Report null and signal results even when they oppose the expected pattern.

## 3. Input contract and program selection

### 3.1 Measured input

Read `bench/results/deliverables.json` as a read-only input.
The producer is `scriptkin.bench.outputs.deliverables`.
`analysis.feature_result` computes its bias, components, and correction.
The following structure describes the current producer.
Angle brackets denote typed placeholders, not literal JSON values.

```text
{
  protocol: {version: str},
  lock: {commit: str | null},
  postHoc: null | {changedFiles: list[str]},
  library: str,
  analysis: "main",
  constants: {excludedPrograms: list[str], bandPowerPrograms: list[str]},
  features: {
    <feature_name>: {
      limit: float,
      scale: "ln" | "absolute",
      values: {
        bias: "camera_referenced" | "raw_device",
        correction: "raw_device"
      },
      featureP: float,
      equivalent: {adjusted: bool, unadjusted: bool},
      programs: {
        <program_id>: {
          bias: {estimate: float | null, lower: float | null, upper: float | null},
          se: float | null,
          factor: null | {estimate: float | null, lower: float | null, upper: float | null},
          components: {
            P: {run: float | null, mount: float | null},
            W: {run: float | null, mount: float | null}
          }
        }
      },
      correction: null | {
        model: "covariates" | "slope" | "shift",
        coefficients: {
          <alpha|beta|gamma_u|gamma_v>: {
            estimate: float | null, se: float | null, fitted: bool
          }
        },
        residual: {
          <program_id>: {
            estimate: float | null, lower: float | null,
            upper: float | null, withinLimit: bool
          }
        }
      },
      range: {
        geometries: list[str], scales: list[float],
        speedsMMPerS: list[float], oscillationHz: list[float]
      }
    }
  }
}
```

`run` and `mount` are variances on the comparison scale.
They are not standard deviations or variances of participant means.
The bias sign is P minus W.
For logarithmic features, `factor` equals exp(bias), which represents P divided by W.
W therefore receives the inverse factor when generating W from a P reference.

Validate the protocol version, analysis name, feature set, limits, scales, and value bases.
Require nonnegative finite variances and ordered finite intervals for every selected program.
Require positive factors and agreement between factors and exponentiated biases within relative tolerance `1e-10`.
Require finite correction terms and the section 11.5 fixed-term values.
A nonfinite correction makes only the corrected version unavailable.
A missing value makes that feature-program cell unavailable.
Do not replace missing inputs with zero or another program's result.
Select from the registered feature program set in BENCH_PROTOCOL section 9.1.
Remove the exclusions named in deliverable `constants.excludedPrograms` or an explicitly supplied bench registry.
For band power, also restrict the set to the named `bandPowerPrograms`.
When both sources are supplied, apply both restrictions.
Never use the presence of a result key to define program eligibility.
Require the selected program entry and its bias, interval, factor, SE, and variance values.
A missing selected entry or value gives `unavailable` with reason `missing_selected_program_value`.
Older deliverables without constants use the full registered set unless a registry is supplied with `--registry`.

Keep non-equivalent features in the analysis.
Equivalence status is an output descriptor, not a feature selection rule.
If `postHoc` is non-null, retain the run but label every measured result `post_hoc_bench`.
Do not present that run as confirmation of an unchanged bench protocol.
Hash the deliverable bytes, selected program center values, and eligible program identifiers together.
Keep the original deliverable text in the manifest and the selected centers in `consequence_inputs.csv`.
A change to a selected center must change the measured input digest.

Deliverables do not contain coefficient covariance, raw feature support, or joint-feature noise covariance.
Do not invent these quantities.
The program registry supplies program geometry, scale, speed, and oscillation frequency.
Record its digest separately.

### 3.2 Closest-program rule

Choose programs before joining labels or viewing model performance [J2].
Use the feature's registered program set only.
First match task geometry exactly.
Then minimize this distance:

```
d = abs(ln(target_scale / program_scale))
  + abs(ln(target_speed / program_speed))
```

For line programs, first minimize the absolute oscillation-frequency difference.
Resolve remaining ties by program identifier in ascending order.
Do not select a program because its bias is largest or its correction works best.

The synthetic target is loops at scale 1.0 and 20 mm/s for the first five features.
The band-power target is a line at 20 mm/s with 8 Hz oscillation.
Use the exact registered frequency nearest 8 Hz.
Derive comparison-scale centers from the selected program's noiseless trajectory.
Use the registered preprocessing and the bench feature definitions.
Do not substitute whole-trial library features for bench features.

Use this fixed program per feature for every participant and replicate.
Do not use observed W speed to choose its own correction program.
This prevents device noise from changing the assigned correction.

Report the target, selected program, scale-and-speed distance, absolute frequency difference, and range status.
Put `frequency_distance_hz` next to `distance` in the input table.
A target of 8 Hz and a selected 5 Hz or 11 Hz program has a frequency difference of 3 Hz.
Its scale-and-speed distance can still be zero.
An out-of-range target is a marked extrapolation sensitivity analysis.
It cannot support a claim within the tested range.

### 3.3 Grid before the bench

Use the registered rounded limits [J3]:

| Feature | Comparison scale | L |
| --- | --- | ---: |
| `bench.motion_s` | ln | 0.036 |
| `bench.path_length_mm` | ln | 0.044 |
| `bench.speed_mean_mm_s` | ln | 0.044 |
| `bench.loop_height_mm` | ln | 0.047 |
| `bench.speed_cv` | absolute | 0.030 |
| `bench.band_power_mm2_s2` | ln | 0.260 |

Set bias `b = k L`, with `k = -2, -1, -0.5, 0, 0.5, 1, 2`.
Cross this grid with these three noise settings.
Each pair gives `(sigma_run / L, sigma_mount / L)`.

- `zero`: P `(0, 0)`; W `(0, 0)`.
- `nominal`: P `(0.25, 0.10)`; W `(0.25, 0.25)`.
- `stress`: P `(0.25, 0.25)`; W `(0.50, 0.50)`.

These settings cover the nominal and stress ranges in BENCH_PROTOCOL section 11.8.
They are assumptions, not predicted bench results.
Store grid inputs in the same typed internal representation as measured inputs.
Mark their source as `grid`.

The grid correction is a known shift: alpha equals b, beta equals 1, and both gamma terms equal zero.
Use the required model name for each program set.
This correction is an oracle control, not a fitted bench result.
It removes the specified shift but does not remove random noise.

Run a grid-only correction-misspecification control at `k = -1, 0, 1` with nominal noise [J3].
Measured mode does not add this control.
Let c be the selected program's noiseless comparison-scale center.
For slope-capable models, set beta to 1.05 and alpha to `b + 0.5 L - 0.05 c`.
For the shift model, keep beta at 1 and set alpha to `b + 0.5 L`.
At correction input c, the error relative to the oracle correction `Z_W + b` is exactly 0.5 L.
At another input z, the slope-capable control adds the error `0.5 L + 0.05 (z - c)`.
Keep gamma terms zero.
Label this sensitivity separately from the oracle correction.

## 4. Feature-level observation model and correction

Represent W at the feature level on the comparison scale [J4].
The bench estimates feature bias and variance, not a unique signal perturbation.
A signal-level implementation would require unmeasured assumptions about sampling, filtering, and position noise.

Let `X_ij` be the ideal comparison-scale value for participant i at session j.
For device D, generate a shared mounting effect and independent session noise:

```
Z_P,ij = X_ij       + M_P,block(i) + E_P,ij
Z_W,ij = X_ij - b_f + M_W,block(i) + E_W,ij
M_D,h ~ Normal(0, variance_mount_D)
E_D,ij ~ Normal(0, variance_run_D)
```

Use six device-specific mounting blocks per cohort.
Assign participants to blocks within site by a seeded random permutation, independent of labels and device assignment.
Keep the block fixed across both sessions.
Draw mounting effects independently between devices and features.
Run-error draws are independent between participants, sessions, and features.
For one session, P and W share a standard-normal run-error draw.
Multiply it by each device's run standard deviation.
Mounting draws remain independent between devices.
Reuse the resulting device observations across the reference, uncorrected, and corrected versions.
This pairing reduces simulation error without changing either marginal observation model.
`test_zero_difference_pair` proves exact agreement only with zero bias, equal run variances, and zero mounting variance.
Do not add P noise again to W observations.
Do not divide run variance by five.
One synthetic session represents one feature observation, not a bench average over five runs.

The four camera-referenced biases represent differential chain error for the same executed movement.
Transferring them to raw synthetic features assumes that movement differences have been removed.
State this assumption explicitly.
The remaining two biases already use raw device features.

For logarithmic features, invert with exp only for tables of physical values.
Models use the comparison-scale values.
For speed CV, the comparison scale is the absolute value.
Do not log-transform its bias or its correction.
Flag negative generated CV values as nonphysical.
Keep them in the primary comparison-scale experiment and report their frequency [J4].
A separate sensitivity floors observed CV at zero before correction.
Do not silently truncate or redraw values.

Apply section 11.5 exactly to raw W features on their comparison scale:

```
u = ln(selected_program_speed / 20)
v = ln(selected_program_scale)
Z_corrected = alpha + beta Z_W + gamma_u u + gamma_v v
```

Use the published full-fit coefficients.
Do not estimate coefficients from the synthetic labels or synthetic test records.
The main-program features use the covariate model.
Loop height and speed CV use the slope model.
Band power uses the shift model.
Fixed terms remain beta 1 and gamma 0 where required.

Apply every finite correction as a diagnostic, including corrections that fail the held-out check [J5].
Mark it `bench_supported` only when every registered program residual passes its limit.
Mark all other corrected results `failed_lack_of_fit` or `unavailable`.
An unavailable correction has no corrected metric.
Never substitute an oracle correction into the measured analysis.

Do not generate W by inverting the fitted correction.
That procedure would make correction success follow from the generator definition.
Our local shift generator can disagree with a fitted slope or covariate model.
Such disagreement is a result to report.
It does not establish that either model transfers to human movement.

The held-out mounting residual interval is a lack-of-fit check.
It is not an independent validation estimate of correction performance.
The loops slope can include speed dependence over the bench range.
Do not interpret it as a device-invariant biological coefficient.

## 5. Synthetic cohort and random-number plan

Use these primary settings [J6].

| Setting | Locked value |
| --- | --- |
| Participants | 600 per cohort |
| Sessions | Two per participant; 14 days apart |
| Sites | Three; equal participant counts |
| Prevalence | Bernoulli probability 0.30; report realized counts |
| Null effect | Standardized case-control mean difference 0 |
| Signal effect | Standardized case-control mean difference 0.8 |
| Participant variance | 0.80 of total ideal-feature variance |
| Session variance | 0.20 of total ideal-feature variance |
| Replicates | 200 independent cohorts per primary cell; 100 per sensitivity cell |
| Primary features | Six separate single-feature experiments |

For feature f, set `s_f = L_f / (sqrt(2) Phi^-1(0.55))`.
Define the generator explicitly:

```
Y_i ~ Bernoulli(0.30)
U_if ~ Normal(0, 1)
V_ijf ~ Normal(0, 1)
X_ijf = mu_f + s_f * (delta * (Y_i - 0.30)
                        + sqrt(0.80) U_if + sqrt(0.20) V_ijf)
delta in {0, 0.8}
```

`mu_f` is the selected program's noiseless comparison-scale value.
Within each class, one ideal session has standard deviation `s_f`.
This variance is a human-scale simulation assumption.
It never comes from the robot's run or mounting variation.
For path length, speed CV, and band power, this scaling has no independent population justification.
State that limitation beside their results.

Under full device confounding, zero signal, and zero measurement noise, one session gives device-only AUC `Phi(abs(b)/(sqrt(2) s_f))`.
At `abs(b) = L`, this equals 0.55.
This is the rule B numerical control.
It is not a bound for repeated-session medians, correlated features, nonnormal features, or corrected predictions.

Sites have no effect on features or labels in the primary experiment.
Device assignment is independent of site.
The joint sensitivity uses independent ideal features and all six case effects of 0.8.
It is a mathematical stress test, not a coherent physical trajectory model.
Path length, duration, and mean speed are physically related.
Do not describe their independently generated values as one executed drawing.

Run these sensitivities at zero bias and `k = -1, 1`, with nominal noise [J6].
Change one setting at a time: N to 200 or 1200; prevalence to 0.08; delta to 0.4.
Also change participant variance share to 0.95, retaining total ideal variance.
Use 100 replicates in each sensitivity.
Sensitivities need less Monte Carlo precision.
At R = 100, the worst-case standard error of a share is 0.05.
For measured inputs, run the same sensitivities without changing their bias or noise.

Use master seed `20261107` [J7].
Use `numpy.random.SeedSequence` with explicit integer keys.
Keys use effect, cohort size, prevalence, replicate, and stream.
The reserved feature and model fields stay at zero.
Streams 1, 2, 3, and 4 identify population, observation, splits, and fitting.
Streams 1000 to 1049 identify permutations.
The population stream creates child streams for labels, traits, sessions, device assignment, and mounting blocks.
For equal effect, size, prevalence, and replicate, single-feature cells share population and noise draws across features and models.
These are common random numbers.
Within a joint cell, feature traits and errors draw independently, conditional on the shared label.
Reuse population and split keys across device settings and observation versions.
Correction controls use fixed coefficients and need no random perturbation stream.
Do not use Python's process-dependent string hash.
Save stream-to-seed values in each unit. The manifest gives the key fields and master seed.
Adding a grid cell must not change existing cells.

The primary generator is a feature-matrix generator. It does not use the `FeatureTable` catalog.

## 6. Scenarios, models, and validation

### 6.1 Device assignment

Each participant keeps one assigned device across sessions [J9].

| Scenario | Assignment and evaluation |
| --- | --- |
| One device | Use the all-W cell against its all-P reference. Do not add all-P cells. |
| W-to-P transfer | Assign P or W with probability 0.5, independently of case status. |
| Device confounding | Assign W with probability 0.85 for cases and 0.15 for controls. |

For confounding sensitivities, use probabilities `(0.5, 0.5)` and `(1, 0)`.
The fully confounded device-held-out result is unavailable because each test device contains only one class.
Do not report AUC, calibration slope, or net benefit as estimable there.
Do not repair this structural failure by moving participants between devices.

### 6.2 Prediction models

Reuse the harness's logistic regression with L2 regularization as the primary model [J10].
Use the fixed `ModelSpec(LOGISTIC_L2, (10.0,))`.
Retain three inner participant-grouped folds for out-of-fold Platt calibration.
A fixed penalty avoids a model-selection dimension in the primary grid.

Use the existing KNN specification `(15, 5, 1)` as a sensitivity.
Run it at zero bias and `k = -1, 1` with nominal noise, and on measured inputs.
Choose the first best inner-fold AUC, as the harness does.

Fit median imputation and standardization inside each training fold.
No primary feature is missing by construction.
Preserve imputation to test the complete procedure.
Do not include participant, device, site, mounting, or label-derived columns as predictors.
Report separate device-only and site-only baselines.

Set `gate=None` and `norms=None` [J10].
Report `reliability_not_established`.
Bench equivalence is not human reliability.
Do not use `reliability_gate` to remove the very bias being studied.
Do not fit population norms to the complete cohort.
If norms are later added, label that analysis exploratory and fit them within training folds.

### 6.3 Splits and leakage rules

Use five outer folds, one repeat, and three inner folds [J10].
Use the same outer split objects for reference, uncorrected, and corrected versions.
Check every split and every fit in every replicate.
Store full membership for replicate 0 of each cell and for smoke runs.
For other replicates, store the compact records in section 9.

- **Record-wise diagnostic:** split session rows with stratification by the fixed participant label.
  Sessions from one participant can cross the outer split.
  Keep inner calibration and search grouped by true participant within the outer training set.
  Report the count and share of test participants also present in training.
  This design permits participant leakage by definition.
- **Participant-wise:** first take each participant's median across their two sessions on the comparison scale.
  Split participants with stratification by label.
  Keep all preprocessing, calibration, and search inside training participants.
  Require the existing `FitWitness` invariant.
- **Device-held-out:** use participant medians and leave one assigned device out.
  Require disjoint participants and devices between training and test.
  Report P-to-W and W-to-P separately.
  Do not average away a directional failure.

A one-device cohort has no device-held-out estimate.
Write `not_applicable_one_device` instead of manufacturing a second device.

In the transfer scenario, participant-wise and record-wise results are mixed-device internal checks.
The primary transfer result is the W-to-P device-held-out direction.
Only W participants train that model; only P participants test it.
No participant appears in both populations.

Use `screening.crossfit`, its model pipelines, and `metrics.fold_auc` through an explicit matrix adapter.
`screening()` itself aggregates participant medians and has no record-wise mode.
`AnalysisPlan` also rejects the exact bench columns.
Therefore, add a separate typed consequence plan and report.
Do not disguise records as new participants to bypass `FitWitness`.

For record-wise diagnostics, record true participant overlap as an expected violation.
Still require disjoint row identifiers and no test-row access during fitting.
For participant and device splits, any participant overlap is a hard error.
For every split, witness imputation, standardization, search, calibration, and final fitting.

The permutation null check is a pipeline diagnostic only [J10].
Run it only in primary cells with bias multiple k = 0 and nominal noise.
Use replicates 0 to 19 and 50 participant-label permutations.
Use only the uncorrected version and participant-wise or device-held-out validation.
Do not run permutations for sensitivity cells, reference or corrected versions, or record-wise validation.
Smoke mode omits this diagnostic.
Permute fixed participant labels, not individual session labels.
Regenerate label-dependent stratified splits for every permutation.
Do not regenerate device assignments or features after permutation.
Use global permutations for the participant-wise diagnostic null.
Use within-device permutations for device-held-out fold AUC checks.
The confounded structural cell remains unavailable.

Use the harness null-mean band and analytic variance floor.
Keep the null check as a pipeline diagnostic.
A refused cell stays refused; do not change seeds until it passes.
Store compact hashes for every completed fit and retain all refusal counts.
Do not use a permutation significance threshold to select paper results.

## 7. Metrics, uncertainty, and material change

Compute AUC with average ranks for ties.
Compare only case-control pairs within one test fold.
Weight fold AUCs by their case-control pair counts.
Do not pool scores from separately fitted outer models for AUC.

For record-wise diagnostics, give each participant total weight one across their tested records.
Use matching weighted pair counts for AUC.
Use participant weights for calibration and decision curves too.
Participant-wise estimates use one median-based probability per participant.

Compute calibration intercept from logistic regression with `logit(p)` as an offset.
Compute calibration slope from a free-intercept regression on `logit(p)`.
Use clipping at `1e-6` and `1 - 1e-6`, as the harness does.
Use unshifted test probabilities and the realized test sample for primary calibration.
Report constant predictions or separation as `calibration_not_estimable`.
Do not replace an undefined slope with 1.
A slope near 1 is not expected when no predictive signal exists.

Report ten-bin calibration-curve data and Brier score as supporting metrics.
Use the same binning algorithm as the harness.
For directional transfer, report the test prevalence with the calibration results.

Use thresholds `0.05, 0.06, ..., 0.30` [J11].
Move each probability from its outer training prevalence to the prespecified target prevalence.
Use the harness's fold-specific prior-shift formula.
Do not use test labels to estimate that shift.
Use target prevalence 0.30 in primary cells and 0.08 in that sensitivity.
State the prior-shift assumption when training and target prevalence differ.

For threshold t, compute:

```
NB(t) = sensitivity(t) * prevalence
      - (1 - specificity(t)) * (1 - prevalence) * t / (1 - t)
NB_all(t) = prevalence - (1 - prevalence) * t / (1 - t)
NB_none(t) = 0
```

Net benefit is abbreviated NB.
A change of 0.01 means one net true-positive equivalent per 100 people under this utility definition.
It is a simulation utility, not a recommendation to treat anyone.
Report each decision curve and its paired differences.
Also report mean NB over the 26 thresholds.

Use these descriptive material-change thresholds [J11].

| Metric | Material change from reference |
| --- | --- |
| AUC | Absolute difference at least 0.05 |
| Calibration intercept | Absolute difference at least 0.20 log-odds |
| Calibration slope | Absolute difference at least 0.20, when both slopes are estimable |
| Mean NB | Absolute difference at least 0.01 |

For decision curves, also report where absolute NB difference reaches 0.01.
Do not select the threshold with the largest observed benefit as the primary result.

For each cell, report means and standard deviations across its planned replicates.
Use 200 for a primary cell and 100 for a sensitivity cell.
Report empirical 2.5th and 97.5th percentiles as simulation distribution intervals.
These intervals describe variation between generated studies.
They are not confidence intervals for measured device parameters.

For paired mean contrasts, report `mean ± t(0.975, R-1) SD/sqrt(R)`.
This is Monte Carlo uncertainty in the simulated mean.
Report the Monte Carlo standard error explicitly.
Calculate a material-change share only for `D_raw` and `D_corrected`, using the metric thresholds above.
Leave the share and its Wilson bounds empty, without a reason, on every other row.
This includes reference and other version rows, `removed`, and `absolute_reduction`.
For permitted material-change shares, give Wilson 95% intervals and denominators.
At R=200, the worst-case Monte Carlo standard error of a share is about 0.035.
Do not add replicates because an interval crosses a preferred conclusion.

Keep feature cells separate; do not claim a family-wide hypothesis test.
No multiplicity-adjusted significance claim is planned.
If fewer than 95 percent of the cell's planned paired replicates remain, label it incomplete [J11].
This requires at least 190 pairs for a primary cell and 95 pairs for a sensitivity cell.
Retain its estimates and all reasons for failure.
A pair contributes only when the required versions both have estimable metrics.
Give metric-specific denominators to expose selective loss.

The measured run conditions on the delivered coefficients and variance components.
Its simulation intervals do not include bench-estimation uncertainty.
Run bias sensitivity using the delivered lower and upper bias bounds, keeping the point correction fixed [J5].
These are endpoint scenarios, not a joint confidence region.
Do not sample independent coefficient errors from marginal jackknife standard errors.
Without coefficient covariance or held-out fits, that procedure has no supported joint distribution.

## 8. Conditional ParkinsonHW example

The paper must stand without this example.
This implementation does not open or analyse a human dataset.

The owner decided on 3 November 2026 that the paper may use the ParkinsonHW labels for this one example (`docs/PAPER_PLAN.md` section 5), but `AGENTS.md` still says that a public CC BY dataset may be used to test readers and features only; that rule must be aligned in writing before any code reads ParkinsonHW labels.
This task adds no ParkinsonHW adapter or tests.

The following is a conditional protocol, not permission to run a model.
Keep the example disabled by default.
Require an explicit written project decision that resolves these restrictions before any model evaluation.
Do not obtain new participant data.
If an editor or reviewer asks for ethics approval, remove the example and all associated claims.
Do not seek approval to retain it.

If the project clears this use, lock the following choices first [J12].
Use only the public anonymised release specified in PAPER_PLAN.
Verify its release identifier, licence, original study citation, and file hashes before reading labels.
Do not read any earlier-project human dataset or any local copy without confirmed public provenance.

Use static spiral trials only.
Count duplicated trials once across all folders.
Add content-based trial deduplication and preserve the duplicate audit.
The current reader hashes files but does not remove duplicate trials.
Keep every trial from one participant in one fold.
Build an audited participant map from public release metadata before model fitting.
The current reader uses content hashes as identifiers.
Those identifiers do not establish that two files belong to different people.
Never treat files as independent people without this map.
Omit the example if the public metadata cannot support participant grouping.
A missing or inconsistent participant label excludes that participant with a recorded reason.

Use one predictor: `parkinsonhw-static-spiral/timing.contact_s`.
Log-transform its positive value before participant aggregation.
Take the median over accepted unique static trials for each participant.
Use the existing `logistic_l2` procedure with penalty grid `(10.0,)`.
Use five participant folds, five repeats, and three inner folds.
Use 200 participant-label permutations and 2000 participant bootstrap draws.
If either class has fewer than ten eligible participants, omit model evaluation.

Do not use path length, physical speed, loop height, or band power in physical units.
Do not use pressure as a bench-qualified feature.
Unknown millimetre scale remains unknown.
A dimensionless feature can still require scale in the current catalog.
Do not override a feature refusal merely because its mathematical formula appears dimensionless.

Static spiral contact duration is a proxy for bench motion duration.
These definitions are not identical.
The example tests sensitivity to an imposed factor; it does not validate cross-device correction in people.

Choose the spiral program at scale 1.0 and 20 mm/s [J12].
Static trials match the spiral task, but their physical speed and scale are unknown.
Use this fixed center-speed assumption and report both unknown quantities.
Also repeat with every eligible spiral program as a sensitivity envelope.
Do not choose a program from the observed AUC or disease labels.

Treat the original public recordings as an empirical reference.
Do not call them measurements from the tested W unit or from P.
Impose a hypothetical W effect by multiplying contact duration by `exp(-b_motion)`.
Use no extra bench noise in the primary example, because the original noise is already present.
Apply the measured raw-scale motion correction at the selected program's u and v.
Report that this is an assumed transfer between unequal feature definitions.

Use all-one-device, random half-device, and 85% case-associated hypothetical assignments.
Use seeds `20261107` with stream family `900` and assignment indices 0–199.
Keep participant folds fixed across observation versions and assignments.
Report participant-wise AUC changes and calibration changes.
Treat net benefit at the observed case-control share as a mathematical sensitivity only.
It has no population or clinical interpretation.

Use a participant-stratified paired bootstrap for the example's conditional metric differences.
This bootstrap conditions on fitted predictions and omits variation from refitting.
Report assignment variation separately from bootstrap uncertainty.
The synthetic experiment remains the sole basis for the main consequence claim.

## 9. Outputs and audit record

Use an explicit `--out` folder.
The grid run writes `bench/results/consequence_grid/` before the lock.
The measured run writes `bench/results/consequence/` after the main runs.

All CSV files use UTF-8, headers, and deterministic row order.
Compress per-replicate metrics and contrasts as `.csv.gz`.
Compress full split and fit audit exports too.
Keep per-cell summaries, figure data, and primary tables as plain CSV.
Retained output for the full grid must remain below 10 GB, including exFAT allocation.
Write unavailable numeric values as empty cells with a separate status and reason.
JSON uses null, never NaN.

Define common key K as:
`source, cell_id, feature, scenario, effect, n, prevalence, noise_id, bias_multiple, model, validation, direction`.
Measured cells leave `bias_multiple` empty and retain the measured bias column.

| File | Rows and required columns |
| --- | --- |
| `consequence_cells.csv` | One row per planned cell. Cell settings, planned replication, correction status, and input digest. |
| `consequence_manifest.json` | Protocol text hash; source commit; package versions; input hashes; grid; judgments; seed keys; timing-log description; deviations; output hashes. |
| `consequence_inputs.csv` | Feature and program; target geometry, scale, speed, frequency; distance; frequency difference; range flag; L; scale; bias and interval; factor; P/W variances. |
| `consequence_corrections.csv` | Feature; model; term; estimate; SE; fitted; program; residual bounds; withinLimit; whole-feature support status. |
| `consequence_replicates.csv.gz` | K; replicate; seed; version; metric; estimate; tested participants; cases; failures; status; reason. |
| `consequence_contrasts.csv.gz` | K; replicate; metric; reference; uncorrected; corrected; D_raw; D_corrected; removed; absolute_reduction; material flags. |
| `consequence_summary.csv` | K; metric; contrast; planned R; usable R; mean; SD; q025; q975; Monte Carlo SE; mean CI bounds; material share and Wilson bounds. |
| `consequence_decision_curves.csv` | Replicate 0 and smoke only. K; replicate; version; threshold; model NB; all NB; none NB; paired NB differences; status. |
| `consequence_calibration.csv` | Replicate 0 and smoke only. K; replicate; version; bin; mean probability; observed share; participant weight; intercept; slope; Brier; status. |
| `consequence_splits.csv.gz` | Full membership for replicate 0 and smoke only. Other membership hashes stay in the restart units. |
| `consequence_fit_audit.jsonl.gz` | Full fit records for replicate 0 and smoke only. Compact fit hashes and all null checks stay in restart units. |
| `consequence_failures.csv` | K; replicate; version; stage; reason code; detail; planned or unexpected failure. |
| `figure_bias_response.csv` | K; metric; contrast; x_bias_over_L; mean; mean CI; simulation interval; correction-support status. |
| `figure_transfer_calibration.csv` | K; version; metric; mean; mean CI; simulation interval; usable R. |
| `figure_decision_curves.csv` | K; version or contrast; threshold; mean NB; pointwise interval; usable R. |
| `table_primary.csv` | Scenario; feature; source; primary metric; raw change; corrected change; absolute reduction; uncertainty; material share; status. |
| `table_validation.csv` | One row per cell, metric, and version; record-wise estimate; participant-wise estimate; W-to-P estimate; P-to-W estimate; usable counts; overlap share. |
| `parkinsonhw_summary.csv` | Optional; release hash; feature; program assumption; assignment; model; validation; version; metric; estimate; paired interval; status. |
| `parkinsonhw_duplicates.csv` | Optional; public file hash; canonical trial hash; retained or duplicate; public participant code. |

Run `audit_fits` for every fit, including permutation fits.
A failed leakage check raises an error regardless of the output detail.
Write full split rows and full fit records only for replicate 0 of each cell and in smoke mode.
Every restart unit contains compact audit records, including replicate 0 and smoke units.
Store full membership separately in compressed detail archives for replicate 0 and smoke only.
For each fold and stage, retain compact hashes, counts, and participant overlap counts.
Hash sorted row IDs with SHA-256 over a compact UTF-8 JSON list.
Store the training row hash, test row hash, training count, test count, and participant overlap count.
For a stage with several inner fits, hash the union of its training rows in the stage record.
Also retain each inner fit's training row hash and the fit count.
The compact outer split record uses stage `outer`.
These output rules change stored detail only. They do not change a leakage check.

Use `--workers N` to run units in a process pool. The default is 1.
Each unit is a compact `.json.gz` member of one ZIP archive per cell.
The ZIP container stores the gzip members without a second compression step.
Use fixed archive metadata and sorted member names.
This avoids one 128 kB exFAT allocation for each of the 232,200 units.
Workers write temporary unit files on the system temporary filesystem.
In smoke mode, temporary files stay in the specified output folder.
After each completed worker batch, publish the cell archive by atomic replacement.
Only then report its units as complete and remove their temporary files.
A restart reuses the committed archive members.
Merge members in sorted cell and replicate order.
A unit stores cell ID, replicate, stream seeds, metric values, failures, and compact audit hashes.
Group values by validation scheme, direction, and observation version.
Store decision-curve values and calibration-bin values as arrays, without repeated row fields.
Keep cell settings once in `consequence_cells.csv`.
`identity.json` holds `code_sha256`, `protocol_sha256`, `plan_sha256`, and `input_sha256`.
The code hash covers the running `scriptkin` package. The protocol hash covers this document.
The plan hash covers the canonical plan, cells, and registered limits.
For measured runs, the input hash covers deliverables, selected centers, and eligible programs.
For grid runs, the folder input identifier is the literal `grid`.
The manifest repeats these identifiers.
`consequence_cells.csv` holds each cell's input digest and cell ID.
The cell ID is the first 16 hexadecimal characters of the SHA-256 of its sorted field JSON.
Each unit member holds that cell ID, an integer replicate index, and `inputs_sha256`.
Its archive name and member name also identify the cell and replicate.
The replicate index has no separate hash. Unit members do not repeat the folder hashes.
A restart checks the folder identity and the unit's cell ID, replicate index, format, and input digest.
Rebuild paired contrasts and summary rows from the unit values.
Every replicate contributes to decision-curve summaries, including replicates without long-form exports.
Write long-form decision curves and calibration bins only for replicate 0 and smoke.
Use gzip with a zero timestamp and no stored filename, so worker count cannot change file bytes.
Keep only one cell's metric rows in memory during summary generation.
Read output files in blocks when calculating their hashes.
All output files have identical bytes for every worker count.
Print measured per-unit elapsed seconds to stdout, outside these deterministic files.
Capture stdout in a separate timing log beside the output folder when timing is needed.
Do not include worker count or elapsed times in the deterministic manifest or output hashes.

Synthetic row identifiers can appear in full audit files.
Do not export human participant membership in the optional fit audit.
Store only counts and hashes of human membership sets.
Figure data must include every planned level, including unavailable cells.
The manuscript must identify the grid and measured runs separately.

## 10. Implementation and verification

Cells store an explicit tuple of feature names. A joint cell lists all six names.

Run these commands from the repository root with the fixed Python environment.
Set `PY="$HOME/Library/Caches/SCRIPT/consequence-8917c7fd/venv/bin"`.
Use `"$PY/python" -I python/studies/consequence.py` for the commands below.
A smoke run selects the primary signal cells at k = 1 with nominal noise.
It runs two replicates per cell. It omits the permutation diagnostic and primary tables.
In measured mode, smoke selects the primary signal cells with the delivered inputs.
The manifest gives the canonical plan hash and points to the cell table.
`--cell-id` and `--replicate` select a restart unit.
A completed unit records the code hash and the protocol hash as well as the plan and input hashes.
A change to any of these hashes requires another output folder.
The study script reads registered move lists when their files exist.
Otherwise it loads the locked trajectory generator with `importlib.util.spec_from_file_location`.
It builds missing move lists in memory and passes their noiseless centers to the package.
The package does not load study scripts.
It applies the bench feature extractor to those move lists.
The shared fit witness also records row indices. This lets the record-wise audit check test-row access.


The package and study script implement this experiment.
All data shapes use frozen dataclasses.
The separate plan preserves `AnalysisPlan` catalog validation.

| Module | Responsibility |
| --- | --- |
| `plan.py` | Use the bench feature registry and limits. Define `ConsequencePlan`, `CellSpec`, `ProgramTarget`, and `SeedKey`. Hash canonical JSON. Enumerate primary cells and sensitivities. |
| `inputs.py` | Validate deliverables. Select a program before reading estimates. Build grid inputs in the same typed representation. |
| `generate.py` | Generate participant matrices with shape `(n, 2, features)`. Keep latent values outside predictor inputs. Apply paired observation models and raw W correction. |
| `validation.py` | Make participant-median and session-row views. Adapt them to `screening.crossfit`. Check fit records and report permitted participant overlap. |
| `metrics.py` | Calculate weighted metrics, paired contrasts, and Monte Carlo summaries. Reuse participant-level harness metrics. |
| `report.py` | Write tables, fit records, restart units, and hashes. Keep this report distinct from human screening reports. |
| `python/studies/consequence.py` | Select grid or measured inputs and an explicit output folder. Support cell and replicate restarts. |

```sh
"$PY/python" -I python/studies/consequence.py --mode grid --out bench/results/consequence_grid/
"$PY/python" -I python/studies/consequence.py --mode measured --deliverables bench/results/deliverables.json --expect-plan-sha256 <hash> --out bench/results/consequence/
"$PY/python" -I python/studies/consequence.py --mode grid --smoke --out <scratch-folder>
```

Reuse an identical completed unit. Refuse a different plan, input, code, or protocol hash.
Smoke mode cannot create primary tables. The full analysis remains necessary.

### Tests and their single target defects

Each test must name its defect in its first comment line.
Use generated fixtures only.
Do not open human files for these tests.

| Test | One defect it detects |
| --- | --- |
| `test_input_shape` | The reader accepts a missing required deliverable field. |
| `test_variance_units` | Observation noise uses a variance as a standard deviation. |
| `test_bias_direction` | W receives the P/W factor in the wrong direction. |
| `test_absolute_scale` | Speed CV receives a logarithmic transform. |
| `test_missing_program` | An unavailable selected program is silently replaced. |
| `test_program_tie` | A tied program choice changes with input ordering. |
| `test_program_outside_range` | An extrapolated target is reported within range. |
| `test_raw_correction_basis` | A camera-referenced difference is passed as a raw W value. |
| `test_fixed_terms` | The shift model fits or changes beta. |
| `test_correction_formula` | The gamma covariates are omitted from correction. |
| `test_failed_check_retained` | A failed correction disappears from diagnostic outputs. |
| `test_mounting_dependence` | Mounting effects are redrawn independently for every session. |
| `test_rule_b_control` | A noise-free L-sized shift gives the wrong scalar theoretical AUC. |
| `test_zero_difference_pair` | Reference and uncorrected values differ when all device inputs agree. |
| `test_oracle_shift_noise` | Oracle shift correction falsely removes random W noise. |
| `test_participant_label` | Session labels change within one generated participant. |
| `test_seed_stability` | Adding a cell changes an existing cell's random draws. |
| `test_participant_split` | A participant crosses a protected outer split. |
| `test_device_split` | The held-out device appears in training. |
| `test_record_overlap_reported` | The record-wise report hides true participant overlap. |
| `test_inner_witness` | A calibration fit accesses an outer test record. |
| `test_fold_auc` | The AUC compares scores from different fitted outer models. |
| `test_record_weights` | A participant with more records receives more total metric weight. |
| `test_prior_shift` | Risk adjustment uses pooled prevalence instead of fold training prevalence. |
| `test_calibration_offset` | Calibration intercept uses a free-slope definition. |
| `test_constant_slope` | Constant predictions receive a false finite calibration slope. |
| `test_net_benefit` | False positives receive the wrong threshold odds penalty. |
| `test_cluster_permutation` | A permutation gives two sessions of one participant different labels. |
| `test_one_class_holdout` | Full confounding produces a fabricated device-held-out metric. |
| `test_paired_summary` | The summary compares different replicate identifiers. |
| `test_failure_denominator` | A metric summary hides failed replicate pairs. |
| `test_output_roundtrip` | Export changes an estimate or its unavailable status. |

Use exact deterministic checks for algebraic properties.
Use analytic moments and fixed tolerances for stochastic checks.
For moment checks, use 100000 draws and tolerance five Monte Carlo standard errors.
Keep those checks separate from the primary and sensitivity analysis.
When implementation is authorized, run the relevant tests and the repository's required `./check`.

## 11. Lock, execution, and permitted claims

Before bench results are available, approve and timestamp this protocol and its canonical plan hash.
Lock all J1–J7 and J9–J12 choices, program-selection rules, feature definitions, models, splits, metrics, grids, and failure rules.
Lock generator code, dependency versions, seed keys, tests, table schemas, and primary figure specifications.
The bench lock includes `docs/CONSEQUENCE_PROTOCOL.md` and `python/studies/consequence.py`, like the other bench study scripts.
Do not edit either file after the lock.
Record a deviation as a row in `bench/DEVIATIONS.md`, as BENCH_PROTOCOL section 13 requires.
After the lock, put a fix in the package `scriptkin.consequence`.
A post hoc bench analysis may use changed package code.
Regenerate deliverables with `scriptkin bench analyse ROOT --post-hoc` before the measured consequence run.
Every output must identify the post hoc run. Compact rows and units link to the cell table's `post_hoc_bench` flag.
Keep the fixed protocol and study script unchanged.
Archive the complete grid results before reading measured deliverables.
A hash identifies content; it does not prove the date of the lock.
Use a dated external registration or an equivalent immutable project record.

The measured run replaces grid inputs with one delivered point estimate set.
It does not choose a favorable grid cell or a new cohort setting.
Run the locked study once with the measured inputs.
Deterministic restarts of failed computation are permitted and logged.
A scientific change requires a dated row in `bench/DEVIATIONS.md` and a separate exploratory output label.
Do not overwrite the planned result.

The paper may state the size of performance changes within the specified synthetic distributions.
It may state how much the correction reduced those changes under these assumptions.
It may report worsening, failed checks, and unavailable estimates.
It must identify tested units, software, surfaces, movement programs, and range limits.
It must separate camera-referenced bias from raw correction.

The paper may not infer screening accuracy, human reliability, clinical utility, or mental health associations.
It may not claim that robotic correction corrects human recordings.
It may not infer equivalence from a small synthetic performance change.
It may not extend band-power claims beyond the registered line oscillations.
It may not treat a failed equivalence test as proof of a device difference.
It may not standardize device differences by robot variation and transfer that scale to people.

BENCH_PROTOCOL section 16 calls chain-only consequences a lower bound for people.
Use that statement only to identify omitted behavioral effects.
Do not claim a numerical lower bound on AUC or calibration distortion.
Behavioral and chain effects can oppose each other, and model metrics are nonlinear.
The simulation estimates chain-only consequences, conditional on its assumptions.

## 12. Fixed judgment calls

The owner accepted J1–J7 and J9–J11. J8 is dropped. J12 remains conditional.
J10 uses the restricted diagnostic schedule in section 6.3.
Changing one after the bench requires a deviation entry.

| ID | Proposed choice | One alternative |
| --- | --- | --- |
| J1 | Six single-feature primary experiments; scenario-specific primary metrics; joint model as sensitivity. | Use a joint model as primary, with an externally fixed feature covariance. |
| J2 | Geometry-first nearest program; equal log-distance weights; fixed synthetic task targets. | Use every eligible program and report an unweighted program-average consequence. |
| J3 | Signed bias grid through 2 L; three noise settings; oracle and misspecified correction controls. | Extend the bias grid to 4 L and fit corrections on simulated bench runs. |
| J4 | Additive Gaussian feature-scale error; six mounting blocks; retain marked negative CV values. | Use a bounded CV observation model and a signal-level simulator for the other features. |
| J5 | Report all finite corrections diagnostically; support label from every program; bias-endpoint sensitivities. | Apply corrections only when every program passes, and leave all other W values unchanged. |
| J6 | N=600; prevalence 0.30; two visits; delta 0 or 0.8; variance shares 0.80/0.20; R=200 for primary cells and R=100 for sensitivities. | Use N=1200, prevalence 0.08, and R=500 as the primary design. |
| J7 | Master seed 20261107 with stable keyed streams. | Use a public random seed generated at registration. |
| J9 | Stable device assignment; 85% case-associated assignment; balanced transfer; full-confounding control. | Randomize device independently at each visit. |
| J10 | Fixed-penalty logistic model; KNN sensitivity; five outer folds; no norms or reliability gate; 50 permutations. | Use the full logistic penalty grid, five outer repeats, and 200 permutations. |
| J11 | Thresholds 0.05–0.30; stated material-change thresholds; 95-percent completeness rule. | Use thresholds 0.01–0.50 and report continuous changes without material-change labels. |
| J12 | Conditional static-spiral contact-duration model; fixed unknown-speed assumption; removal on ethics request. | Omit the human-data example from the first paper. |
