# SCRIPT intended use

This document states what SCRIPT is for, what it is not for, and which claims the current evidence supports. It applies to the SCRIPT Capture app, the SCRIPT Bench app, the scriptkin library, and the evaluation harness that [the design](DESIGN.md#7-evaluation) describes.

## Intended use

SCRIPT is a research platform. It records drawing movement and reference questionnaire responses, and it tests whether movement features relate to symptom scores.

| Item | Intended use |
| --- | --- |
| Purpose | Research only. Record drawing movement and questionnaire responses for an approved study. |
| Population | People aged 13 to 24, recorded in two age bands: 13 to 17, and 18 to 24. |
| Administration | Supervised sessions, with an operator present. Self-administered sessions, with no operator present. |
| Users | The operator runs the session. The participant draws and answers the questionnaires. The analyst reads the exports. |
| Device | An iPad with Apple Pencil. The app needs iPadOS 18 or later. For the bench study only, a Mac with macOS 15 or later runs SCRIPT Bench with a Wacom tablet. |
| Outputs | One journal and one export for each session. Features, quality findings, and questionnaire scores for the analyst only. |

A study uses SCRIPT only with an ethics approval, a study configuration, and a safety protocol. The [study guide](STUDY_GUIDE.md) gives the procedure.

## Bench sessions

SCRIPT Capture in bench mode and SCRIPT Bench record the robotic bench study of the [bench protocol](BENCH_PROTOCOL.md). A motion system draws in a bench session, so the session records no person. It has no participant code, no questionnaire, and no safety step. Do not use a bench session to record a person.

## What SCRIPT is not

SCRIPT is not a clinical tool. It does not diagnose, screen, triage, or advise.

- SCRIPT shows no score, result, or interpretation to anyone. This includes the participant, the operator, and a guardian.
- The operator sees only a neutral safety alert when a safety item is endorsed, skipped, or not reached.
- The app shows the exact questionnaire text and records item identifiers and response values. The app never calculates a score.
- scriptkin calculates questionnaire scores for the analyst only. A score is a research label, not a result for the participant.
- SCRIPT is not a crisis service and does not monitor responses in real time. The site's safety protocol and its trained responder handle every safety flag.
- SCRIPT is not for people younger than 13 or older than 24. The journal has no age band outside 13 to 24.

## Claims that SCRIPT supports now

SCRIPT supports engineering claims about its software only. Each claim below names the test that checks it. `./check` runs every test.

| Claim | Test |
| --- | --- |
| An absent Pencil channel stays absent in the journal, and each number reads back with the same bits. | `anAbsentChannelIsAnAbsentKey` and `doublesParseToTheirBitsAndTheCodecTextRoundTrips` in `app/Tests/ScriptCoreTests/CodecTests.swift` |
| `ScriptCore` and the Python reader give the same session summary for each shared fixture. | `theFoldEqualsTheFixtureSummary` in `app/Tests/ScriptCoreTests/FoldTests.swift` and `test_fold_matches_independent_builder` in `python/tests/test_journal.py` |
| A crash at any step of an export or a recovery leaves a valid state. | `publishConvergesFromEveryCrashPoint` and `reconcileConvergesFromEveryCrashPointAndASecondLaunchChangesNoByte` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| The reader refuses a journal that breaks an integrity rule (I1 to I7). | `test_i1_rejects_invalid_lines` to `test_i7_rejects_clock_reset_without_new_boot` in `python/tests/test_journal.py` |
| A feature gives a refusal, not a number, when its data cannot support it. | `test_unknown_scale_refuses_distance_but_keeps_time` and `test_qc_refuses_bad_acquisition` in `python/tests/test_quality_features.py` |
| A supervised session cannot close while a safety flag has no recorded action. | `aSupervisedSessionCannotCloseWithAnUnactionedFlag` in `app/Tests/ScriptCoreTests/DecideTests.swift` |
| A safety flag in a supervised session changes nothing that the participant sees. | `aSupervisedFlagChangesNothingThatTheParticipantSees` in `app/Tests/ScriptCoreTests/DecideTests.swift` |
| A configuration with an unverified instrument, or an instrument outside its validated ages, is refused. | `anUnverifiedInstrumentIsRefused` and `anInstrumentOutsideItsValidatedAgesIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift` |
| An encrypted export opens only with the study private key. | `test_wrong_private_key_is_refused_by_key_id_before_decryption` in `python/tests/test_export.py` |
| A pen-only copy has no questionnaire or safety line and ends at the last task. | `test_pen_only_copy_passes_integrity_and_has_no_response` and `test_pen_only_copy_ends_at_the_last_trial` in `python/tests/test_export.py` |
| A bench session cannot hold a handoff, questionnaire, or safety line. | `aBenchSessionRefusesEveryParticipantCommand` in `app/Tests/ScriptCoreTests/DecideTests.swift` and `test_a_questionnaire_line_in_a_bench_session_is_refused` in `python/tests/test_journal.py` |

These claims apply to `ScriptCore` and scriptkin. The app applies these rules through `ScriptCore`. Before a study uses a release, the release must pass the [release process](RELEASE.md), which includes `./check` and the [device test](DEVICE_TESTING.md). The simulator cannot make Pencil force or tilt.

## Claims that SCRIPT does not support

No evidence supports these claims now. Do not make them in a protocol, a consent document, a paper, or a report.

- Movement features relate to a mental health condition in people.
- A feature is reliable between sessions or between devices in people. Report each feature as "reliability not established" until human evidence exists.
- A model can screen for a condition with a stated accuracy. No labelled human dataset is cleared for this project.
- Two iPads give comparable measurements. A device that can record is not, for that reason, qualified for comparison. Only the analysis plan decides comparability ([design section 16](DESIGN.md#16-device-qualification)).

## Limits of the population

- Age. A configuration names an age band for each questionnaire block. `ScriptCore` and scriptkin refuse an instrument whose lowest validated age is above the lowest age of the band.
- Instrument evidence differs by age. The PHQ-9 (Patient Health Questionnaire-9) has an adult validation ([Kroenke 2001](https://pmc.ncbi.nlm.nih.gov/articles/PMC1495268/)) and a validation for ages 13 to 17 ([Richardson 2010](https://pmc.ncbi.nlm.nih.gov/articles/PMC3217785)).
- The project found no adolescent validation of the PHQ-8 (Patient Health Questionnaire-8). Its entry in `contract/instruments.json` takes its lowest age, 13, from the PHQ-9 validation. So the age rule permits the PHQ-8 for ages 13 to 17, and the demo configuration uses it there.
- For the GAD-7 (Generalized Anxiety Disorder-7), one exploratory study gives the evidence for ages 12 to 17. It had 40 adolescents at one site ([GAD-7 adolescent study](https://pmc.ncbi.nlm.nih.gov/articles/PMC5765270)). A community study in Hong Kong gives a cut-off for ages 15 to 24 ([GAD-7 community study](https://pmc.ncbi.nlm.nih.gov/articles/PMC9664827/)).
- The DASS-21 (Depression Anxiety Stress Scales-21) developers state use down to age 14 ([DASS frequently asked questions](https://www2.psy.unsw.edu.au/dass/DASSFAQ.htm)).
- Movement can change with the hand used, motor or visual impairment, fatigue, and practice. The journal records the hand, the practice flag, and accommodation codes for each trial. The site keeps other participant attributes in its own records.

## What a future screening claim needs

A screening claim is a new intended use. It needs a separate intended-use statement that names each item below, and evidence for each item.

1. **Intended population.** State the ages, the inclusion and exclusion rules, and how participants are sampled.
2. **Setting and user.** State where the test runs and who acts on its output.
3. **Target condition.** Name one condition. State whether the target is a symptom score or a clinical diagnosis.
4. **Reference standard.** Name an independent reference assessment. Keep its assessors blind to the drawing results. Set the longest permitted time between the drawing and the assessment.
5. **Measurement reliability.** Show test-retest and cross-device agreement for each feature before any model uses it.
6. **Prevalence-based validation.** Sample the intended population, not a case group and a control group. Plan the sample size from the expected number of positive cases. Report the positive and negative predictive values at the real prevalence.
7. **Outcome-blind analysis.** Freeze the features and the analysis plan before the analyst joins the labels.
8. **Action.** Define the output, the threshold, the result for an inconclusive test, and the follow-up service for a positive result.
9. **Regulatory review.** Decide whether the software is a medical device in each jurisdiction of use. Check locally.

Prevalence changes what a positive result means. For example, take a test with 80% sensitivity and 80% specificity, at a prevalence of 8%. Only about 26% of positive results are true positives. A case-control study cannot show this number, because it fixes the prevalence by design.

A regulator can expect software lifecycle, risk management, and usability records. The Australian Therapeutic Goods Administration (TGA) lists IEC 62304, ISO 14971, and IEC 62366-1 for these topics. IEC is the International Electrotechnical Commission, and ISO is the International Organization for Standardization ([TGA standards page](https://www.tga.gov.au/products/medical-devices/software-and-artificial-intelligence-ai/overview/standards-software-based-medical-devices)). In the United States, the Food and Drug Administration (FDA) sets a quality management system regulation (QMSR) for medical devices ([FDA QMSR page](https://www.fda.gov/medical-devices/postmarket-requirements-devices/quality-management-system-regulation-qmsr)). The [hazard log](HAZARDS.md) and the [dependency list](DEPENDENCIES.md) prepare for this work. SCRIPT makes no claim of conformity with any standard or regulation.
