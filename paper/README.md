# First SCRIPT paper

This folder contains a draft skeleton for an IEEE JBHI regular paper.
It uses `\documentclass[journal]{IEEEtran}`.
It contains no study results. Red boxed notes mark unfinished work.
Authors must verify the Methods draft against the executed study.

From the repository root, build outside the repository:

```sh
mkdir -p /private/tmp/paper-build
tectonic -X compile paper/main.tex --outdir /private/tmp/paper-build
```

Tectonic must have access to its TeX package bundle, including IEEEtran.
The output is `/private/tmp/paper-build/main.pdf`.
If the sandbox blocks the default cache, use a writable cache outside the repository:

```sh
TECTONIC_CACHE_DIR=/private/tmp/paper-build/tectonic-cache \
  tectonic -X compile paper/main.tex --outdir /private/tmp/paper-build
```

The temporary cache must contain or fetch the required TeX packages.
Do not put build output or recordings in this folder.
Skip every name that starts with `._` when searching or processing files.

## Structure and budgets

| Files | Content | Shared budget from the plan |
| --- | --- | --- |
| `main.tex`, `sections/abstract.tex`, `sections/introduction.tex` | Title, abstract, introduction | 1.0 page |
| `sections/related_work.tex` | Prior comparisons, surface effects, sources for limits | 0.5 page |
| `sections/methods.tex` | Bench and synthetic experiment | 2.5 pages |
| `sections/results.tex` | Planned results, tables, figures | 2.3 pages |
| `sections/discussion.tex`, `sections/statements.tex` | Interpretation, limitations, declarations | 0.9 page |
| `references.bib` | References | 0.8 page |

These are allocation targets, not measured draft lengths.
The combined budget is eight pages. The Methods draft still needs editing to fit.
The official author page could not be checked on 7 November 2026 because it blocked automated access.
Recheck current submission rules before submission:
[JBHI author instructions](https://www.embs.org/jbhi/prepare-and-submit-your-manuscript/).

## Sources and unresolved decisions

The content specifications follow `docs/PAPER_PLAN.md`, including its claim, ethics rules, and owner decisions.
Methods use `docs/BENCH_PROTOCOL.md` and `docs/CONSEQUENCE_PROTOCOL.md`.
This branch contains the consequence protocol, its implementation, and the capture parity test.
The output map follows consequence protocol section 9. Outputs still require the specified study runs.
Do not replace missing measured outputs with grid results or smoke-run outputs.

References R2 to R6, W1, M2, M3, and M4 use only metadata given in the bench protocol.
R5 supports the related-work specification. R3 supplies descriptive jerk terminology.
R2, R4, and R6 support the limit rationale. R6 also supports the surface-effect limitation.
W1 describes the tablet. M2, M3, and M4 describe motion candidates and timed commands.
No full ParkinsonHW citation was found in the supplied documents or the repository documentation search.
It is therefore absent from the bibliography.

The ParkinsonHW example stays disabled. No human dataset was opened or analysed for this skeleton.
Its inclusion requires written governance clearance, verified public provenance, full citations, deduplication, and participant grouping.
Remove it if an editor or reviewer requests ethics approval.
The owner must settle authorship, AI disclosure, release licences, funding, and conflicts.
The hardware, versions, calibration results, and final locked program sets also need confirmation.
No approval, release, or successful check is asserted by the manuscript.

## Placeholder-to-output map

This map belongs here only. Do not put internal output names in manuscript text.
Bench paths follow bench protocol section 15.
`B` below means `bench/results/`.
`C` means `bench/results/consequence/` for measured inputs.
`G` means `bench/results/consequence_grid/` for the assumed grid.
Use C and G where specified below, with separate source labels.

| Placeholder or display | Output files that supply it | Use and limits |
| --- | --- | --- |
| R-completeness | B `run_status.csv`, `agreement.csv`; `bench/DEVIATIONS.md` | Count failures, reruns, camera validity, and non-estimable programs. Explain deviations. |
| T-sampling; R-sampling | B `sampling.csv`, `accuracy.csv`, `bench_features.csv`, `run_status.csv` | Sampling intervals, gaps, noise floors, hold counts, spatial distances, rotations, valid counts. |
| T-agreement; R-agreement | B `agreement.csv`, `bench_features.csv`, `deliverables.json` | All six feature decisions, intervals, scales, limits, program coverage, adjusted and unadjusted decisions. |
| F-agreement | B `agreement.csv`, `bland_altman.csv` | Program bias intervals and repeated-measures agreement. Separate mounting means from single runs. |
| T-correction; R-correction | B `correction.csv`, `deliverables.json` | Model coefficients, fitted terms, jackknife uncertainty, held-out residuals, lack-of-fit status. |
| T-gate; R-gate | B `gate.csv`, `run_status.csv`; `bench/registry.json` | Fault detection, main acceptance, exact intervals, locked Pencil dwell expectation. |
| R-program-selection | C and G `consequence_inputs.csv` | Target geometry, scale, speed, frequency, selected program, scale-and-speed distance beside absolute frequency difference, and range status. |
| T-consequence; R-consequence | C and G `table_primary.csv`, `consequence_summary.csv`, `consequence_inputs.csv`, `consequence_corrections.csv`, `consequence_failures.csv` | Primary summaries: participant-wise all-W versus all-P AUC; W-to-P calibration intercept; null-effect participant-wise confounding AUC. Include raw change, absolute reduction, usable counts, and support. |
| F-consequence, bias-response panel | G `figure_bias_response.csv`; C `consequence_inputs.csv`, `consequence_summary.csv` | Grid bias response with separately identified measured-input estimates. Do not imply a measured bias sweep. |
| F-consequence, transfer-calibration panel | C and G `figure_transfer_calibration.csv`, `consequence_calibration.csv` | Directional calibration summaries. Long-form bins cover replicate zero only. |
| F-consequence, decision-curve panel | C and G `figure_decision_curves.csv` | All-replicate curve summaries, paired differences, reference strategies, pointwise uncertainty. |
| R-validation | C and G `table_validation.csv`, `consequence_summary.csv`, `consequence_failures.csv`, `consequence_splits.csv.gz`, `consequence_fit_audit.jsonl.gz` | Participant overlap in record-wise splits, protected splits, directional failures, baselines, and sensitivity cells. Full audit detail covers replicate zero only. |
| Optional example, omitted by default | C `parkinsonhw_summary.csv`, `parkinsonhw_duplicates.csv` | Conditional future result only. No main claim depends on it. |

Supporting bench outputs remain available for author verification:

| Output | Purpose |
| --- | --- |
| B `pilot.csv`, `interim.csv`, `bench_precision.json` | Pilot choices, mounting extension, design precision. These are not main-run agreement results. |
| B `features.csv` and its provenance file, `bench_features.csv` | Descriptive library features and motion-interval features. Jerk and pressure have no equivalence claim. |
| B `deliverables.json` | Read-only measured bias, noise, correction, basis, and range inputs for the consequence experiment. |

Supporting consequence outputs complete the result audit:

| C and G output | Purpose |
| --- | --- |
| `consequence_cells.csv`, `consequence_manifest.json` | Planned cells, source, replication, hashes, judgments, versions, deviations. |
| `consequence_replicates.csv.gz`, `consequence_contrasts.csv.gz` | Replicate metrics and paired raw, corrected, signed-removal, and absolute-reduction contrasts. |
| `consequence_decision_curves.csv`, `consequence_calibration.csv` | Long-form checks for replicate zero and smoke runs. Use figure summaries for all-replicate figures. |

Abstract and discussion pending notes use the verified results above.
Methods pending notes use study records and the locked protocols; they are not result placeholders.
Statement pending notes require owner decisions and verified release information.
The AI-use disclosure belongs in the acknowledgments.
The lock reference belongs in release metadata without a printed revision hash.
Add a dated external consequence registration or equivalent immutable record before claiming registration.
Analysis changes after the lock must be reported as post hoc.

## Source checks

`python/tests/test_paper.py` checks input targets, citation keys, internal identifiers, and planning captions in uncommented manuscript source.
It excludes resource-selection and label arguments, which do not print text.
It is a static guard for this skeleton, not a complete TeX interpreter.
Inspect the compiled PDF after future macro or formatting changes.
