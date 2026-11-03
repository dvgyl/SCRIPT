# SCRIPT hazard log

This log lists the hazards of SCRIPT, their controls, and the risk that remains. It follows the style of ISO 14971, the risk management standard for medical devices from the International Organization for Standardization (ISO). It is a proportionate log for a research platform. It is not a claim of conformity with ISO 14971.

The [intended use](INTENDED_USE.md) sets the scope of this log. The [data governance](DATA_GOVERNANCE.md) document describes the privacy controls in more detail.

## How to read and update this log

Each hazard has an identifier, a cause, a harm, and a severity. It also has two likelihood estimates, the duties of the site, the residual risk, an owner, and a table of controls.

- **Severity** is the worst credible harm.
- **Likelihood before controls** assumes no control in SCRIPT and no site procedure.
- **Residual likelihood** assumes the implemented controls and the site duties in this log.
- **Owner** is the role that keeps the controls in place.

Each control in a controls table has one of three statuses:

- **Implemented.** The code exists, and an automated test in `./check` proves it. The table names the code and the test.
- **Implemented without a test.** The code exists, but no automated test proves it. The table says why.
- **Not implemented.** No code in this repository does it. A site duty or a later release must cover it.

A script checks that every code symbol, file, and test in these tables exists. The estimates are initial qualitative judgements. The SCRIPT maintainers and the sponsor review them. The sponsor accepts each residual risk in writing before a study starts. Review the controls tables and the estimates at each release, as the [release process](RELEASE.md) states.

### Severity bands

| Band | Harm |
| --- | --- |
| S1 Minor | Inconvenience, or one repeated task. No lasting effect. |
| S2 Moderate | Distress, a lost session, or a wrong research value that a quality check can find. |
| S3 Serious | Disclosure of a participant's health data, or a misleading research result that can reach a publication. |
| S4 Critical | A person at risk does not get help in time. |

### Likelihood bands

| Band | Meaning |
| --- | --- |
| L1 Rare | Not expected during the life of the platform. |
| L2 Unlikely | Can occur in some studies. |
| L3 Possible | Can occur in any study. |
| L4 Likely | Expected in most studies. |

## Summary

| ID | Hazard | Severity | Likelihood before controls | Residual likelihood | Owner |
| --- | --- | --- | --- | --- | --- |
| [H1](#h1-a-safety-flag-is-missed-or-lost) | A safety flag is missed or lost | S4 | L3 | L2 | Site |
| [H2](#h2-a-participant-infers-a-score) | A participant infers a score | S2 | L3 | L2 | Maintainers and site |
| [H3](#h3-a-crisis-list-is-wrong-or-out-of-date) | A crisis list is wrong or out of date | S4 | L3 | L2 | Site |
| [H4](#h4-data-are-lost-in-a-crash-or-on-a-full-disk) | Data are lost in a crash or on a full disk | S2 | L3 | L1 | Maintainers and site |
| [H5](#h5-a-journal-is-corrupted) | A journal is corrupted | S2 | L2 | L1 | Maintainers |
| [H6](#h6-the-millimetre-scale-is-wrong) | The millimetre scale is wrong | S2 | L3 | L1 | Maintainers and site |
| [H7](#h7-device-differences-are-mistaken-for-symptoms) | Device differences are mistaken for symptoms | S3 | L4 | L3 | Analyst and sponsor |
| [H8](#h8-labels-leak-into-features) | Labels leak into features | S3 | L3 | L2 | Analyst |
| [H9](#h9-a-participant-is-re-identified) | A participant is re-identified | S3 | L3 | L2 | Site and sponsor |
| [H10](#h10-an-export-reaches-the-wrong-recipient) | An export reaches the wrong recipient | S3 | L3 | L2 | Site |
| [H11](#h11-an-instrument-text-is-not-verified) | An instrument text is not verified | S3 | L2 | L1 | Maintainers |
| [H12](#h12-script-is-used-outside-the-intended-population) | SCRIPT is used outside the intended population | S3 | L2 | L2 | Site and sponsor |
| [H13](#h13-research-data-enter-a-device-backup) | Research data enter a device backup | S3 | L3 | L1 | Maintainers and site |
| [H14](#h14-a-session-has-the-wrong-participant-code) | A session has the wrong participant code | S2 | L3 | L2 | Site |
| [H15](#h15-a-withdrawal-misses-a-copy) | A withdrawal misses a copy | S3 | L3 | L2 | Site |

H7 has the highest residual likelihood. It stays at L3 until human evidence shows that features agree between devices.

These grades changed at this review:

- H6 fell from L2 to L1. Setup now asks for a reference measurement when the device table does not confirm the iPad model.
- H13 fell from L3 to L1. The app now excludes every research folder and log from device backups, and a test proves it.
- H15 is new. Export names no longer hold the participant code, so the withdrawal of copies outside the iPad depends on the site session register.

## Hazards

### H1. A safety flag is missed or lost

| Field | Entry |
| --- | --- |
| Hazard | A participant endorses, skips, or never reaches a safety item, and no one responds. |
| Cause | A crash before the flag is on storage. A block that ends before its safety item. An operator misses the alert. A session closes without an action. A self-administered export reaches the reviewer late. The responder is not available. |
| Harm | A person at risk does not get help in time. |
| Severity | S4 |
| Likelihood before controls | L3 |
| Duty of the site | A safety protocol with a named responder for each supervised session. Export and review times that meet the `remoteReview` block. See the [study guide](STUDY_GUIDE.md#write-the-safety-protocol). |
| Residual likelihood | L2 |
| Residual risk | Acceptable only with the site safety protocol in place. SCRIPT cannot alert anyone in real time in a self-administered session, and it cannot send an export on a schedule. |
| Owner | Site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| The writer runs fsync after each `safetyFlagged` and `safetyAction` line. fsync forces the data onto the storage. | Implemented | `JournalWriter.Durability` in `app/Sources/ScriptCore/Storage.swift` | `theWriterSyncsAtEverySafetyAndEndLine` in `app/Tests/ScriptCoreTests/WriterTests.swift` |
| No screen that follows a flag shows before the flag is on storage. | Implemented | `SessionStore.syncedFlags` in `app/ScriptApp/SessionStore.swift` | `aSelfAdministeredFlagIsOnStorageBeforeTheCrisisListShows` in `app/ScriptAppTests/StoreTests.swift` |
| An endorsed or skipped safety item is a flag. | Implemented | `SessionState.decide` in `app/Sources/ScriptCore/Session.swift` | `test_a_skipped_or_endorsed_safety_item_is_flagged_in_the_fixtures` in `python/tests/test_contract.py` |
| A safety item that its block never reached is a flag. This includes a declined block, a block that the operator ended, and an interrupted block. | Implemented | `SessionState.unreachedFlags` and `SessionState.recovery` in `app/Sources/ScriptCore/Session.swift` | `aRecoveryInsideABlockFlagsItsUnreachedSafetyItemInTheBoundsOfTheBlock` in `app/Tests/ScriptCoreTests/DecideTests.swift`. `reconcileFlagsTheUnreachedSafetyItemOfAnInterruptedBlock` in `app/Tests/ScriptCoreTests/StorageTests.swift`. `anOperatorEndOfASelfAdministeredBlockBeforeItsSafetyItemFlagsItAndShowsTheList` in `app/ScriptAppTests/StoreTests.swift`. `test_an_unreached_safety_item_is_flagged_inside_its_block` in `python/tests/test_contract.py`. |
| Review shows a neutral alert for each flag of a supervised session and asks for one action code. | Implemented | `ReviewScreen` in `app/ScriptApp/OperatorScreens.swift` | `aSupervisedFlagNeedsOneActionCodeBeforeTheCrisisListAndTheClose` in `app/ScriptAppTests/StoreTests.swift`. `testSupervisedAdultSessionWithAFlagEndsWithTheAlertActionAndCrisisList` in `app/ScriptAppUITests/SessionFlowTests.swift`. |
| A supervised session cannot close while a flag has no action. | Implemented | `Rejection.unactionedFlag` in `app/Sources/ScriptCore/Session.swift` | `aSupervisedSessionCannotCloseWithAnUnactionedFlag` in `app/Tests/ScriptCoreTests/DecideTests.swift` |
| A self-administered safety item needs a `remoteReview` block. Imminent review is 24 hours or less, and routine review is 1 weekday or less. The export interval is no longer than the imminent review. | Implemented | `validate` in `app/Sources/ScriptCore/Study.swift`. `validate` in `python/src/scriptkin/studyconfig.py`. | `aSelfAdministeredSafetyItemNeedsRemoteReview` and `aRemoteReviewSlowerThanTheProtocolIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift`. `test_self_administered_safety_item_needs_remote_review` in `python/tests/test_contract.py`. |
| The resume window of a self-administered session is no longer than the imminent review interval, so a flag cannot wait on the iPad longer. | Implemented | `validate` in `app/Sources/ScriptCore/Study.swift`. `validate` in `python/src/scriptkin/studyconfig.py`. | `theResumeWindowDefaultsTo24HoursAndMustNotOutlastTheReview` in `app/Tests/ScriptCoreTests/StudyTests.swift`. `test_a_resume_window_longer_than_the_review_interval_is_refused` in `python/tests/test_contract.py`. |
| A self-administered session with a flag closes and exports after the operator ends it. | Implemented | `SessionStore.showSupportList` in `app/ScriptApp/SessionStore.swift` | `aSelfAdministeredFlagStillClosesAndExportsWhenTheOperatorEndsTheSession` in `app/ScriptAppTests/StoreTests.swift` |
| An interrupted self-administered session closes for export after its resume window. | Implemented | `SessionStore.expireIfDue` in `app/ScriptApp/SessionStore.swift` | `aSelfAdministeredSessionPastItsResumeWindowClosesForExport` in `app/ScriptAppTests/StoreTests.swift`. `anInterruptedSelfAdministeredSessionClosesOnlyAfterItsResumeWindow` in `app/Tests/ScriptCoreTests/DecideTests.swift`. |
| A damaged journal leaves the iPad as a package, and intake files it for review with its decrypted journal. | Implemented | `Storage.publishDamaged` in `app/Sources/ScriptCore/Storage.swift`. `intake` in `python/src/scriptkin/intake.py`. | `aDamagedJournalOnHomeLeavesTheIPadAsADamagedPackage` in `app/ScriptAppTests/StoreTests.swift`. `test_a_damaged_package_is_filed_for_review_and_never_as_a_session` in `python/tests/test_intake.py`. |
| The reviewer counts the endorsed, skipped, and unreached flags of an export. | Implemented | `safety_flag_count` in `python/src/scriptkin/labels.py` | `test_safety_flag_count_counts_endorsed_skipped_and_unreached_items` in `python/tests/test_labels.py` |
| Each export of a self-administered session reaches the reviewer within `exportEveryHours`. | Not implemented | None | None |

### H2. A participant infers a score

| Field | Entry |
| --- | --- |
| Hazard | A participant learns or guesses a questionnaire result, or that a response was flagged. |
| Cause | The app shows a score, a replay, or a quality value. The flow after a block differs when a flag is raised. Only flagged participants see the crisis list. The operator reacts in front of the participant. |
| Harm | Distress, false reassurance, or a loss of trust. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Duty of the site | Train operators to react in the same way to every session. The responder speaks with the participant in private. |
| Residual likelihood | L2 |
| Residual risk | In a self-administered session with a safety item, the crisis list appears at once after a flag. The participant can link the two. This is the cost of a safety control, and the sponsor accepts it in writing. |
| Owner | Maintainers and site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| A supervised flag changes nothing that the participant sees. | Implemented | `SessionState.decide` in `app/Sources/ScriptCore/Session.swift` | `aSupervisedFlagChangesNothingThatTheParticipantSees` in `app/Tests/ScriptCoreTests/DecideTests.swift`. `aFlagInASupervisedSessionChangesNothingThatTheParticipantSees` in `app/ScriptAppTests/StoreTests.swift`. |
| Every session with a questionnaire block shows the crisis list before it closes. | Implemented | `Rejection.crisisListNotShown` in `app/Sources/ScriptCore/Session.swift` | `aSessionCannotCloseBeforeTheCrisisListFollowsTheLastBlock` in `app/Tests/ScriptCoreTests/DecideTests.swift` |
| The app calculates no score. | Implemented without a test | `InstrumentSet` in `app/Sources/ScriptCore/Study.swift` | No test. `InstrumentSet` reads no scoring rule, so the app has no score to show. |

### H3. A crisis list is wrong or out of date

| Field | Entry |
| --- | --- |
| Hazard | The crisis list shows a number that does not work, a closed service, or a service for another country. |
| Cause | A service changes or closes. A list is copied from the demo configuration without a check. The session has the wrong jurisdiction. |
| Harm | A person in crisis cannot reach help in time. |
| Severity | S4 |
| Likelihood before controls | L3 |
| Duty of the site | Check each entry on the official page of the service, and record the date. Check again at the interval that the protocol sets. See the [study guide](STUDY_GUIDE.md#verify-the-crisis-lists). |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site check. A service can change between two checks. |
| Owner | Site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| A configuration needs a crisis list for each jurisdiction. | Implemented | `validate` in `app/Sources/ScriptCore/Study.swift`. `validate` in `python/src/scriptkin/studyconfig.py`. | `aConfiguredJurisdictionWithoutACrisisListIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift` |
| A block cannot start, and no list shows, without a list for the jurisdiction of the session. | Implemented | `Rejection.noCrisisList` in `app/Sources/ScriptCore/Session.swift` | `aSessionWithoutACrisisListForItsJurisdictionCannotStartABlockOrShowAList` in `app/Tests/ScriptCoreTests/DecideTests.swift` |
| A new study configuration has no crisis list, so the check refuses it until the study adds checked lists. | Implemented | `skeleton` in `python/src/scriptkin/studytools.py` | `test_config_new_writes_a_skeleton_that_the_check_refuses_until_it_is_complete` in `python/tests/test_studytools.py` |
| A check of the age of `verifiedOn`. | Not implemented | None | None |

### H4. Data are lost in a crash or on a full disk

| Field | Entry |
| --- | --- |
| Hazard | Part or all of a session is lost. |
| Cause | The app crashes, the iPad loses power, the system ends the app, or the storage is full. |
| Harm | The participant repeats the session, or a research record is lost. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Duty of the site | Charge the iPad. Check the free storage before each session day. |
| Residual likelihood | L1 |
| Residual risk | Acceptable. Lines written after the last fsync can be lost in a power failure. |
| Owner | Maintainers and site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| The journal is append-only. The writer keeps call order and flushes at `contactEnded`. | Implemented | `JournalWriter` in `app/Sources/ScriptCore/Storage.swift` | `theWriterKeepsCallOrderAndFlushesAtContactEnded` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| The writer runs fsync at `trialEnded`, `questionnaireEnded`, `safetyFlagged`, `safetyAction`, and `sessionClosed`. | Implemented | `JournalWriter.Durability` in `app/Sources/ScriptCore/Storage.swift` | `theWriterSyncsAtEverySafetyAndEndLine` in `app/Tests/ScriptCoreTests/WriterTests.swift` |
| Recovery keeps every complete line and records the bytes that it removes. | Implemented | `Storage.reconcile` in `app/Sources/ScriptCore/Storage.swift` | `reconcileRemovesATornTailAndRecordsItsBytes` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| Export and recovery converge from every crash point. | Implemented | `Storage.publish` and `Storage.reconcile` in `app/Sources/ScriptCore/Storage.swift` | `publishConvergesFromEveryCrashPoint` and `reconcileConvergesFromEveryCrashPointAndASecondLaunchChangesNoByte` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| A failed write stops the session at once and keeps every line saved before it. | Implemented | `SessionStore.stop` in `app/ScriptApp/SessionStore.swift` | `aFailedWriteReachesTheAppAtOnceAndRefusesLaterAppends` in `app/Tests/ScriptCoreTests/WriterTests.swift`. `aFailedWriteStopsTheSessionAtOnceAndRebuildsItFromTheFile` in `app/ScriptAppTests/StoreTests.swift`. |
| A session does not start with less than 500 MB of free storage. | Implemented | `SessionStore.hasRoom` in `app/ScriptApp/SessionStore.swift` | `aSessionDoesNotStartWithLessThan500MBFree` in `app/ScriptAppTests/StoreTests.swift` |

### H5. A journal is corrupted

| Field | Entry |
| --- | --- |
| Hazard | An analyst reads a journal whose content differs from the recording. |
| Cause | A torn tail after a crash. Damage inside the file. A transfer error. A changed file. |
| Harm | A wrong research value, or a lost session. |
| Severity | S2 |
| Likelihood before controls | L2 |
| Duty of the site | Take each export into the study store with `scriptkin intake`. Keep the device copy until intake accepts the export. |
| Residual likelihood | L1 |
| Residual risk | Acceptable. |
| Owner | Maintainers |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| The reader applies the integrity rules I1 to I7. | Implemented | `read_journal` in `python/src/scriptkin/formats/export.py` | `test_i1_rejects_invalid_lines` to `test_i7_rejects_clock_reset_without_new_boot` in `python/tests/test_journal.py` |
| The manifest digest detects a changed file. | Implemented | `verify_export` in `python/src/scriptkin/formats/export.py` | `test_i5_rejects_manifest_mismatch` in `python/tests/test_journal.py` |
| A changed ciphertext is refused. | Implemented | `read_journal` in `python/src/scriptkin/formats/export.py` | `test_tampered_or_misaddressed_ciphertext_is_refused` in `python/tests/test_export.py` |
| Intake quarantines an export that fails a check, with the reason. | Implemented | `intake` in `python/src/scriptkin/intake.py` | `test_tampered_export_goes_to_quarantine_with_its_reason` in `python/tests/test_intake.py` |
| Recovery moves a journal with damage before its last line to `Damaged/`, unchanged and out of Files. | Implemented | `Storage.moveToDamaged` in `app/Sources/ScriptCore/Storage.swift` | `aDamagedJournalNeverAppearsInFiles` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| A damaged package names its first damaged line, and intake never takes it as a session. | Implemented | `Storage.publishDamaged` in `app/Sources/ScriptCore/Storage.swift`. `intake` in `python/src/scriptkin/intake.py`. | `aDamagedJournalWritesTheContractPackage` in `app/Tests/ScriptCoreTests/ContractOutTests.swift`. `test_the_app_damaged_package_is_filed_for_review` in `python/tests/test_intake.py`. |
| Swift and Python fold each fixture to the same summary. | Implemented | `SessionState` in `app/Sources/ScriptCore/Session.swift`. `fold` in `python/src/scriptkin/formats/journal.py`. | `theFoldEqualsTheFixtureSummary` in `app/Tests/ScriptCoreTests/FoldTests.swift`. `test_fold_matches_independent_builder` in `python/tests/test_journal.py`. |

### H6. The millimetre scale is wrong

| Field | Entry |
| --- | --- |
| Hazard | Features in millimetres use a wrong scale. |
| Cause | A wrong pixel density in the device table. An operator measures the reference wrongly. |
| Harm | Wrong spatial and kinematic values, and false differences between devices. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Duty of the site | Qualify each device with a reference length. Measure with a steel rule or an ID-1 card. |
| Residual likelihood | L1 |
| Residual risk | Acceptable with device qualification. An operator can still measure wrongly on an iPad model that the device table does not confirm. |
| Owner | Maintainers and site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| Each journal records the scale and its source. The model-table scale follows the contract formula. | Implemented | `MillimetreScale.lookup` in `app/Sources/ScriptCore/Scale.swift` | `theModelTableScaleMatchesTheContractFormula` and `theIDCardScaleUsesTheCardWidth` in `app/Tests/ScriptCoreTests/StudyTests.swift` |
| Setup asks for a reference measurement when the device table does not confirm the iPad model. | Implemented | `DeviceFacts.tableScale` in `app/ScriptApp/Environment.swift`. `MeasurementScreen` in `app/ScriptApp/SetupScreen.swift`. | `aModelWhoseDensityIsNotConfirmedNeedsAReferenceMeasurement` in `app/ScriptAppTests/StoreTests.swift`. `testQualificationFromAMeasuredScaleShowsTheSampleTiming` in `app/ScriptAppUITests/SessionFlowTests.swift`. |
| An unknown scale makes the features in millimetres refuse. | Implemented | `extract` in `python/src/scriptkin/features.py` | `test_unknown_scale_refuses_distance_but_keeps_time` in `python/tests/test_quality_features.py` |
| The qualification report gives the jitter and the line deviation in millimetres. | Implemented | `qualify` in `python/src/scriptkin/qualification.py` | `test_hold_jitter_and_line_deviation_are_in_millimetres` in `python/tests/test_qualification.py` |

### H7. Device differences are mistaken for symptoms

| Field | Entry |
| --- | --- |
| Hazard | A difference between devices looks like a difference between people. |
| Cause | Different iPads, Pencils, screen protectors, nibs, or iPadOS versions. A Pencil model without pressure. A device used more for one group than another. |
| Harm | A misleading research result. |
| Severity | S3 |
| Likelihood before controls | L4 |
| Duty of the site | Qualify each device. Balance devices across groups. Record the device of each session. Run the [device test](DEVICE_TESTING.md) on each iPad and Pencil model. |
| Residual likelihood | L3 |
| Residual risk | Not acceptable for a screening claim. Acceptable for engineering work and for reliability studies. No human evidence shows that features agree between devices. |
| Owner | Analyst and sponsor |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| The channel plan follows the declared Pencil. | Implemented | `ChannelPlan.plan` in `app/Sources/ScriptCore/Ink.swift` | `theChannelPlanFollowsTheDeclaredPencil` in `app/Tests/ScriptCoreTests/InkTests.swift` |
| Quality control reports a planned channel that the data do not show. | Implemented | `assess` in `python/src/scriptkin/quality.py` | `test_qc_reports_planned_channel_absent` in `python/tests/test_quality_features.py` |
| Simulated input refuses the features that need a Pencil-only channel. | Implemented | `assess` in `python/src/scriptkin/quality.py` | `test_simulator_refuses_pen_channels` in `python/tests/test_quality_features.py` |
| The harness measures agreement between devices, and its gate removes a feature that fails. | Implemented | `reliability` and `reliability_gate` in `python/src/scriptkin/evaluate/reliability.py` | `test_device_agreement_pairs_each_participant_across_devices` and `test_gate_fails_a_feature_with_injected_session_noise` in `python/tests/test_reliability.py` |
| A device-only baseline gives a caution when the device can explain a result. | Implemented | `screening` in `python/src/scriptkin/evaluate/screening.py` | `test_device_baseline_explains_a_device_confounded_result` in `python/tests/test_screening.py` |
| A device holdout keeps whole devices out of every fit. | Implemented | `screening` in `python/src/scriptkin/evaluate/screening.py` | `test_device_holdout_keeps_whole_devices_out_of_every_fit` in `python/tests/test_screening.py` |
| The QC report gives the sample rate and the channels of each device and Pencil. | Implemented | `qc_report` in `python/src/scriptkin/qcreport.py` | `test_qc_report_counts_match_a_hand_count_of_the_contract_fixtures` in `python/tests/test_qcreport.py` |

### H8. Labels leak into features

| Field | Entry |
| --- | --- |
| Hazard | Information about the questionnaire result reaches the features or the model. |
| Cause | Responses are in the same journal as the pen data. The time after the last task shows whether an operator handled a flag. An analyst sees the labels before the features are frozen. |
| Harm | An inflated accuracy and a misleading research result. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Duty of the site | Give feature analysts pen-only copies. Register the plan digest before the labels are joined. |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the analysis plan. The harness cannot stop an analyst who sees the labels before the plan is registered. |
| Owner | Analyst |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| Questionnaire blocks never enter strokes. | Implemented | `read_journal` in `python/src/scriptkin/formats/export.py` | `test_questionnaire_blocks_stay_apart_from_strokes` in `python/tests/test_journal.py` |
| A pen-only copy has no questionnaire, response, or safety line, and it ends at the last task. It refuses a session with a block before the tasks. | Implemented | `pen_only` in `python/src/scriptkin/formats/export.py` | `test_pen_only_copy_passes_integrity_and_has_no_response`, `test_pen_only_copy_ends_at_the_last_trial`, and `test_pen_only_refuses_a_questionnaire_block_before_the_tasks` in `python/tests/test_export.py` |
| Practice trials and qualification sessions never enter the feature table. | Implemented | `feature_table` in `python/src/scriptkin/features.py` | `test_practice_trials_never_enter_the_feature_table` and `test_qualification_sessions_never_enter_the_feature_table` in `python/tests/test_session_features.py` |
| The leakage witness refuses a fit that sees a test participant. | Implemented | `witness_refusal` in `python/src/scriptkin/evaluate/screening.py` | `test_witness_refuses_an_ungrouped_splitter` and `test_screening_refuses_when_a_fit_sees_test_participants` in `python/tests/test_screening.py` |
| The null check refuses a harness whose null AUC is not at chance. | Implemented | `screening` in `python/src/scriptkin/evaluate/screening.py` | `test_null_check_refuses_a_harness_that_leaks_labels` in `python/tests/test_screening.py` |
| `scriptkin evaluate --expect-plan-digest` refuses a plan that differs from the registered plan. | Implemented | `plan_digest` in `python/src/scriptkin/evaluate/plan.py`. `_evaluate` in `python/src/scriptkin/cli.py`. | `test_evaluate_command_refuses_a_plan_whose_digest_differs_from_the_registered_digest` in `python/tests/test_cli.py`. `test_every_report_states_that_its_digests_do_not_date_the_plan` in `python/tests/test_screening.py`. |

### H9. A participant is re-identified

| Field | Entry |
| --- | --- |
| Hazard | Someone links a journal or a label to a person. |
| Cause | Participant codes that are not random. Small groups by age band, site, and time. Handwriting. An identity key or a session register stored with the research data. |
| Harm | Disclosure of a participant's mental health data. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Duty of the site | Random participant codes. The identity key and the session register kept apart from research data. Access control on the study store. A check for small groups before data leave the study. |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site duties. Handwriting stays in the data. |
| Owner | Site and sponsor |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| The journal has no field for a name, a date of birth, a signature, or contact details. | Implemented without a test | `contract/journal.schema.json` | No test gives the schema a line with an identity field. |
| An export name holds no participant code. | Implemented | `SessionOpened.exportName` in `app/Sources/ScriptCore/Journal.swift` | `anExportNameHoldsNoParticipantCodeAndWithdrawalStillFindsAnEncryptedExport` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| Home shows only the last 4 characters of a participant code until the operator enters the PIN. | Implemented | `SessionStore.title` in `app/ScriptApp/SessionStore.swift` | `homeShowsOnlyTheLastFourCharactersOfACodeUntilThePINUnlocks` in `app/ScriptAppTests/StoreTests.swift` |
| Storage errors never name a participant. | Implemented | `Storage.StorageError` in `app/Sources/ScriptCore/Storage.swift` | `storageErrorsNameTheSessionAndNeverTheParticipant` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| The deletion log and the export index hold only a keyed hash of the code. | Implemented | `Storage.withdraw` and `Storage.index` in `app/Sources/ScriptCore/Storage.swift` | `withdrawalDeletesOneParticipantAndLogsOnlyAKeyedHashOfTheCode` and `onlyTheDeviceSecretConfirmsAWithdrawal` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| The QC report and a leakage refusal name no participant. | Implemented | `qc_report` in `python/src/scriptkin/qcreport.py`. `witness_refusal` in `python/src/scriptkin/evaluate/screening.py`. | `test_qc_report_holds_no_label_score_or_response` in `python/tests/test_qcreport.py`. `test_a_leakage_refusal_counts_the_participants_and_names_none` in `python/tests/test_screening.py`. |
| A check for small groups before data leave the study. | Not implemented | None | None |

### H10. An export reaches the wrong recipient

| Field | Entry |
| --- | --- |
| Hazard | A person outside the study receives an export. |
| Cause | A wrong transfer channel. A configuration with a wrong public key. A demo configuration in a real study. |
| Harm | Disclosure of a participant's health data. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Duty of the site | One approved transfer channel. A check of the configuration digest on each iPad. No demo configuration with real participants. |
| Residual likelihood | L2 |
| Residual risk | Acceptable. The manifest shows the file sizes. A demo export is plaintext. |
| Owner | Site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| A configuration with an export key encrypts every export to the study public key. | Implemented | `ExportCipher` in `app/Sources/ScriptCore/Storage.swift` | `anEncryptedExportOpensWithTheContractSuiteInfoAndSessionID` and `aPlaintextExportIsMarkedDemoAndAnyExportKeyEncrypts` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| A study configuration without an export key is refused, and a stated key identifier must match the key. | Implemented | `validate` in `app/Sources/ScriptCore/Study.swift` | `aStudyConfigurationWithoutAnExportKeyOrAPinIsRefused` and `aStatedKeyIDMustBeTheSHA256OfTheExportKey` in `app/Tests/ScriptCoreTests/StudyTests.swift` |
| A wrong private key is refused before decryption. | Implemented | `read_journal` in `python/src/scriptkin/formats/export.py` | `test_wrong_private_key_is_refused_by_key_id_before_decryption` in `python/tests/test_export.py` |
| Only the owner of the private key file can read it. | Implemented | `export_key_pair` in `python/src/scriptkin/studytools.py` | `test_config_keygen_makes_a_private_key_that_only_its_owner_reads` in `python/tests/test_studytools.py` |
| Every operator screen of a demo configuration shows "Demo, not for research". | Implemented without a test | `DemoBanner` in `app/ScriptApp/Components.swift` | No test checks that the banner appears. |

### H11. An instrument text is not verified

| Field | Entry |
| --- | --- |
| Hazard | The app shows a questionnaire text that differs from the published form, or that the licence does not permit. |
| Cause | A typing error, a changed wording, a translation without validation, or a licence that does not cover the use. |
| Harm | Invalid labels, a missing safety item, or a licence breach. |
| Severity | S3 |
| Likelihood before controls | L2 |
| Duty of the site | Use only the instruments and locales in `contract/instruments.json`. Ask the maintainers to add others. |
| Residual likelihood | L1 |
| Residual risk | Acceptable. The status `verified` records a human check that a test cannot repeat. |
| Owner | Maintainers |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| An instrument whose `textStatus` is not `verified` is refused. | Implemented | `validate` in `app/Sources/ScriptCore/Study.swift`. `validate` in `python/src/scriptkin/studyconfig.py`. | `anUnverifiedInstrumentIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift`. `test_unverified_or_out_of_range_instruments_are_refused` in `python/tests/test_contract.py`. |
| `contract/instruments.json` matches its generator, and `./check` fails when they differ. | Implemented | `verify` in `python/src/scriptkin/contract.py` | `test_checked_in_contract_matches_the_generator` in `python/tests/test_contract.py` |
| PHQ-9 item 9 is the only safety item in the default instruments. | Implemented | `python/src/scriptkin/instruments.py` | `test_phq9_item_9_is_the_only_safety_item_in_the_default_instruments` in `python/tests/test_contract.py` |

### H12. SCRIPT is used outside the intended population

| Field | Entry |
| --- | --- |
| Hazard | SCRIPT is used with people outside ages 13 to 24, without an approved study, or to make a decision about a person. |
| Cause | A wrong age band. A clinical setting. A study without ethics approval. |
| Harm | Results from instruments that are not validated for the person, or a decision based on output with no clinical validity. |
| Severity | S3 |
| Likelihood before controls | L2 |
| Duty of the site | Check the participant's age against the site records before Setup. Use SCRIPT only under the [intended use](INTENDED_USE.md). |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site check. The app cannot check a participant's real age. The PHQ-8 has no adolescent validation, but its entry in `contract/instruments.json` permits ages 13 to 17. |
| Owner | Site and sponsor |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| The journal accepts only the age bands 13 to 17 and 18 to 24. | Implemented without a test | `AgeBand` in `app/Sources/ScriptCore/Journal.swift` | No test gives the decoder or the schema another age band. |
| An instrument outside its validated ages is refused. | Implemented | `validate` in `app/Sources/ScriptCore/Study.swift`. `SessionState.decide` in `app/Sources/ScriptCore/Session.swift`. | `anInstrumentOutsideItsValidatedAgesIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift`. `aBlockBelowTheInstrumentsValidatedAgeIsRejected` in `app/Tests/ScriptCoreTests/DecideTests.swift`. |

SCRIPT shows no score to anyone ([H2](#h2-a-participant-infers-a-score)). Each session records the ethics approval identifier.

### H13. Research data enter a device backup

| Field | Entry |
| --- | --- |
| Hazard | A device backup holds journals, exports, damaged journals, the deletion log, or the export index. |
| Cause | The app does not exclude its research folders from backup, and the iPad has backups turned on. |
| Harm | Disclosure of a participant's health data, and copies that withdrawal cannot reach. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Duty of the site | Turn off device backups on each study iPad. Do not save exports into a folder that a backup or a cloud service copies. |
| Residual likelihood | L1 |
| Residual risk | Acceptable. A person can still save a copy of an export to another place in Files, outside the backup exclusion. |
| Owner | Maintainers and site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| The app excludes `Sessions/`, `Staging/`, `Exports/`, `Damaged/`, the deletion log, and the export index from device backups. | Implemented | `Storage.createDirectories` and `Storage.excludeFromBackup` in `app/Sources/ScriptCore/Storage.swift` | `researchDataStaysOutOfDeviceBackups` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| The device secret of the deletion log is a Keychain item that a backup cannot move to another device. | Implemented without a test | `KeychainDeviceSecret` in `app/Sources/ScriptCore/Storage.swift` | No test reads the accessibility class of the Keychain item. |

### H14. A session has the wrong participant code

| Field | Entry |
| --- | --- |
| Hazard | A session records a valid participant code that belongs to another participant. |
| Cause | A typing error, a mix-up of two participants, or a second person who continues an interrupted self-administered session. |
| Harm | Labels joined to the wrong pen data, a withdrawal that misses a session, or a safety follow-up for the wrong person. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Duty of the site | Read the code from the site's records, and check it again before the handoff. Use codes that differ in more than one character. |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site check. SCRIPT cannot detect a valid code for the wrong person in Setup. |
| Owner | Site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| A participant code matches `^[A-Z0-9-]{3,16}$`. | Implemented without a test | `ParticipantCode` in `app/Sources/ScriptCore/Journal.swift` | No test gives Setup a code outside the pattern. |
| An interrupted self-administered session resumes only after the participant types the code of the session. | Implemented | `SessionStore.confirmCode` in `app/ScriptApp/SessionStore.swift`. `ConfirmCodeScreen` in `app/ScriptApp/ParticipantScreens.swift`. | `aResumedSelfAdministeredSessionWaitsForTheCodeOfItsParticipant` in `app/ScriptAppTests/StoreTests.swift`. `testAnInterruptedSelfAdministeredSessionResumesOnlyForItsCode` in `app/ScriptAppUITests/SessionFlowTests.swift`. |
| An interrupted self-administered session closes after its resume window and does not resume. | Implemented | `SessionStore.expireIfDue` in `app/ScriptApp/SessionStore.swift` | `aSelfAdministeredSessionPastItsResumeWindowClosesForExport` in `app/ScriptAppTests/StoreTests.swift` |
| A check digit in the participant code. | Not implemented | None | None |

### H15. A withdrawal misses a copy

| Field | Entry |
| --- | --- |
| Hazard | A participant withdraws, and a copy of their data stays somewhere. |
| Cause | Export names hold no participant code, so a folder name cannot show whose data it holds. Copies sit on the iPad, in transfer locations, in the study store, and in derived files. |
| Harm | Data kept against the participant's wishes, and a breach of the consent terms. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Duty of the site | Keep the [session register](DATA_GOVERNANCE.md#keep-the-site-session-register). Follow the [withdrawal procedure](DATA_GOVERNANCE.md#withdrawal-and-deletion-procedure). |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the session register. A missing register row leaves a copy outside the iPad that nothing finds. |
| Owner | Site |

| Control | Status | Code | Test |
| --- | --- | --- | --- |
| Withdrawal deletes every journal, export, damaged journal, and damaged package of the code on the iPad, and logs the deletion. | Implemented | `Storage.withdraw` in `app/Sources/ScriptCore/Storage.swift` | `withdrawalDeletesOneParticipantAndLogsOnlyAKeyedHashOfTheCode` and `withdrawalDeletesRecoveringCopiesAndReportsJournalsWithoutAReadableCode` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| The export index finds an encrypted export of the code on the iPad. | Implemented | `Storage.index` in `app/Sources/ScriptCore/Storage.swift` | `anExportNameHoldsNoParticipantCodeAndWithdrawalStillFindsAnEncryptedExport` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| The intake receipt of a session names its source folder and its export digest, so the store folders of a session can be found. | Implemented | `intake` in `python/src/scriptkin/intake.py` | `test_intake_copies_a_verified_export_with_its_manifest_and_receipt` in `python/tests/test_intake.py` |
| A record that links a participant code to the copies outside the iPad. | Not implemented | None | None |
