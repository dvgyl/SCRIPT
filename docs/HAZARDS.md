# SCRIPT hazard log

This log lists the hazards of SCRIPT, their controls, and the risk that remains. It is written in the style of ISO 14971, the risk management standard for medical devices from the International Organization for Standardization (ISO). It is a proportionate log for a research platform. It is not a claim of conformity with ISO 14971.

The [intended use](INTENDED_USE.md) sets the scope of this log. The [data governance](DATA_GOVERNANCE.md) document describes the privacy controls in more detail.

## How to read and update this log

Each hazard has an identifier, a cause, a harm, and a severity. It also has two likelihood estimates, the controls, the duties of the site, the residual risk, and an owner.

- **Severity** is the worst credible harm.
- **Likelihood before controls** assumes no control in SCRIPT and no site procedure.
- **Residual likelihood** assumes the SCRIPT controls in code and the site duties in this log.
- **Control in SCRIPT** names the code and the test that implement each control. "Design only" means that [the design](DESIGN.md) specifies the control, and no code in this repository implements it. "No test" means that code exists without an automated test.
- **Owner** is the role that keeps the control in place.

The estimates are initial qualitative judgements. The SCRIPT maintainers and the sponsor review them. The sponsor accepts each residual risk in writing before a study starts. Update the "Control in SCRIPT" rows and the estimates at each release.

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
| [H6](#h6-the-millimetre-scale-is-wrong) | The millimetre scale is wrong | S2 | L3 | L2 | Maintainers and site |
| [H7](#h7-device-differences-are-mistaken-for-symptoms) | Device differences are mistaken for symptoms | S3 | L4 | L3 | Analyst and sponsor |
| [H8](#h8-labels-leak-into-features) | Labels leak into features | S3 | L3 | L2 | Analyst |
| [H9](#h9-a-participant-is-re-identified) | A participant is re-identified | S3 | L3 | L2 | Site and sponsor |
| [H10](#h10-an-export-reaches-the-wrong-recipient) | An export reaches the wrong recipient | S3 | L3 | L2 | Site |
| [H11](#h11-an-instrument-text-is-not-verified) | An instrument text is not verified | S3 | L2 | L1 | Maintainers |
| [H12](#h12-script-is-used-outside-the-intended-population) | SCRIPT is used outside the intended population | S3 | L2 | L2 | Site and sponsor |
| [H13](#h13-research-data-enter-a-device-backup) | Research data enter a device backup | S3 | L3 | L3 | Maintainers and site |
| [H14](#h14-a-session-has-the-wrong-participant-code) | A session has the wrong participant code | S2 | L3 | L2 | Site |

H7 and H13 have the highest residual risk. H7 stays at L3 until human reliability evidence exists. H13 stays at L3 until the app implements backup exclusion.

## Hazards

### H1. A safety flag is missed or lost

| Field | Entry |
| --- | --- |
| Hazard | A participant endorses or skips a safety item, and no one responds. |
| Cause | A crash before the flag is on disk. An operator misses the alert. A session closes without an action. A self-administered export reaches the reviewer late. The responder is not available. |
| Harm | A person at risk does not get help in time. |
| Severity | S4 |
| Likelihood before controls | L3 |
| Control in SCRIPT | The writer runs fsync, which forces the data onto the storage, after each `safetyFlagged` and `safetyAction` line: `theWriterSyncsAtEverySafetyAndEndLine` in `app/Tests/ScriptCoreTests/WriterTests.swift`. A supervised session cannot close with an open flag: `aSupervisedSessionCannotCloseWithAnUnactionedFlag` in `app/Tests/ScriptCoreTests/DecideTests.swift`. A skipped safety item counts as a flag: `test_a_skipped_or_endorsed_safety_item_is_flagged_in_the_fixtures` in `python/tests/test_contract.py`. A self-administered safety item needs a `remoteReview` block: `aSelfAdministeredSafetyItemNeedsRemoteReview` in `app/Tests/ScriptCoreTests/StudyTests.swift` and `test_self_administered_safety_item_needs_remote_review` in `python/tests/test_contract.py`. These tests check a missing block and the export interval. The 24-hour and 1-weekday limits have no test. The operator alert screen is design only. |
| Duty of the site | A safety protocol with a named responder for each supervised session. Export and review times that meet the `remoteReview` block. See the [study guide](STUDY_GUIDE.md#write-the-safety-protocol). |
| Residual likelihood | L2 |
| Residual risk | Acceptable only with the site safety protocol in place. SCRIPT cannot alert anyone in real time in a self-administered session. |
| Owner | Site |

### H2. A participant infers a score

| Field | Entry |
| --- | --- |
| Hazard | A participant learns or guesses a questionnaire result, or that a response was flagged. |
| Cause | The app shows a score, a replay, or a quality value. The flow after a block differs when a flag is raised. Only flagged participants see the crisis list. The operator reacts in front of the participant. |
| Harm | Distress, false reassurance, or a loss of trust. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Control in SCRIPT | A supervised flag changes nothing that the participant sees: `aSupervisedFlagChangesNothingThatTheParticipantSees` in `app/Tests/ScriptCoreTests/DecideTests.swift`. Every session with a questionnaire block shows the crisis list before it closes: `aSessionCannotCloseBeforeTheCrisisListFollowsTheLastBlock` in the same file. `InstrumentSet` in `app/Sources/ScriptCore/Study.swift` reads no scoring rule, so the app cannot calculate a score. The fold summary holds counts only. |
| Duty of the site | Train operators to react in the same way to every session. The responder speaks with the participant in private. |
| Residual likelihood | L2 |
| Residual risk | In a self-administered session with a safety item, the crisis list appears at once after a flag. The participant can link the two. This is the cost of a safety control, and the sponsor accepts it in writing. |
| Owner | Maintainers and site |

### H3. A crisis list is wrong or out of date

| Field | Entry |
| --- | --- |
| Hazard | The crisis list shows a number that does not work, a closed service, or a service for another country. |
| Cause | A service changes or closes. A list is copied from the demo configuration without a check. The session has the wrong jurisdiction. |
| Harm | A person in crisis cannot reach help in time. |
| Severity | S4 |
| Likelihood before controls | L3 |
| Control in SCRIPT | A configuration with questionnaire blocks needs a crisis list for each jurisdiction: `validate` in `app/Sources/ScriptCore/Study.swift` and `python/src/scriptkin/studyconfig.py`. No test. A questionnaire block cannot start without a list: `Rejection.noCrisisList` in `app/Sources/ScriptCore/Session.swift`. No test. Each list has a `version` and a `verifiedOn` date. Each display records the list version in `safetyShown`. SCRIPT does not check the age of `verifiedOn`. |
| Duty of the site | Check each entry on the official page of the service, and record the date. Check again at the interval that the protocol sets. See the [study guide](STUDY_GUIDE.md#verify-the-crisis-lists). |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site check. A service can change between two checks. |
| Owner | Site |

### H4. Data are lost in a crash or on a full disk

| Field | Entry |
| --- | --- |
| Hazard | Part or all of a session is lost. |
| Cause | The app crashes, the iPad loses power, the system ends the app, or the storage is full. |
| Harm | The participant repeats the session, or a research record is lost. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Control in SCRIPT | The journal is append-only. The writer flushes at `contactEnded`: `theWriterKeepsCallOrderAndFlushesAtContactEnded` in `app/Tests/ScriptCoreTests/StorageTests.swift`. It runs fsync at `trialEnded`, `questionnaireEnded`, `safetyFlagged`, `safetyAction`, and `sessionClosed`: `theWriterSyncsAtEverySafetyAndEndLine` in `app/Tests/ScriptCoreTests/WriterTests.swift`. Recovery keeps every complete line: `reconcileRemovesATornTailAndRecordsItsBytes` in `app/Tests/ScriptCoreTests/StorageTests.swift`. Export and recovery converge from every crash point: `publishConvergesFromEveryCrashPoint` and `reconcileConvergesFromEveryCrashPointAndASecondLaunchChangesNoByte` in the same file. A failed write reaches the app at once: `aFailedWriteReachesTheAppAtOnceAndRefusesLaterAppends` in `app/Tests/ScriptCoreTests/WriterTests.swift`. The app does not check free storage before a session. |
| Duty of the site | Charge the iPad. Check the free storage before each session day. |
| Residual likelihood | L1 |
| Residual risk | Acceptable. Lines written after the last fsync can be lost in a power failure. |
| Owner | Maintainers and site |

### H5. A journal is corrupted

| Field | Entry |
| --- | --- |
| Hazard | An analyst reads a journal whose content differs from the recording. |
| Cause | A torn tail after a crash. Damage inside the file. A transfer error. A changed file. |
| Harm | A wrong research value, or a lost session. |
| Severity | S2 |
| Likelihood before controls | L2 |
| Control in SCRIPT | The reader applies the integrity rules I1 to I7: `test_i1_rejects_invalid_lines` to `test_i7_rejects_clock_reset_without_new_boot` in `python/tests/test_journal.py`. The manifest digest detects a changed file: `test_i5_rejects_manifest_mismatch` in the same file. A changed ciphertext is refused: `test_tampered_or_misaddressed_ciphertext_is_refused` in `python/tests/test_export.py`. Recovery moves a journal with interior damage to `Damaged/` unchanged: `reconcileRemovesATornTailAndRecordsItsBytes` in `app/Tests/ScriptCoreTests/StorageTests.swift`. Swift and Python fold each fixture to the same summary: `theFoldEqualsTheFixtureSummary` in `app/Tests/ScriptCoreTests/FoldTests.swift`. |
| Duty of the site | Check each export at intake with `read_journal`. Keep the device copy until the check passes. |
| Residual likelihood | L1 |
| Residual risk | Acceptable. |
| Owner | Maintainers |

### H6. The millimetre scale is wrong

| Field | Entry |
| --- | --- |
| Hazard | Features in millimetres use a wrong scale. |
| Cause | A wrong pixel density in the device table. An operator measures the reference bar wrongly. |
| Harm | Wrong spatial and kinematic values, and false differences between devices. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Control in SCRIPT | Each journal records the scale and its source. The model-table formula is tested: `theModelTableScaleMatchesTheContractFormula` and `theIDCardScaleUsesTheCardWidth` in `app/Tests/ScriptCoreTests/StudyTests.swift`. An unknown scale makes millimetre features refuse: `test_unknown_scale_refuses_distance_but_keeps_time` in `python/tests/test_quality_features.py`. The qualification report gives jitter and line deviation in millimetres: `test_hold_jitter_and_line_deviation_are_in_millimetres` in `python/tests/test_qualification.py`. `DeviceTable` in `app/Sources/ScriptCore/Scale.swift` marks some pixel densities as not confirmed. `MillimetreScale.lookup` returns a model-table scale for these entries too. A comment on `ppiConfirmed` says that Setup then asks for a measurement. The design asks for a measurement only when the model is not in the device table. No app code asks for one. |
| Duty of the site | Qualify each device with a reference length. Measure with a steel rule. |
| Residual likelihood | L2 |
| Residual risk | Acceptable with device qualification. |
| Owner | Maintainers and site |

### H7. Device differences are mistaken for symptoms

| Field | Entry |
| --- | --- |
| Hazard | A difference between devices looks like a difference between people. |
| Cause | Different iPads, Pencils, screen protectors, nibs, or iPadOS versions. A Pencil model without pressure. A device used more for one group than another. |
| Harm | A misleading research result. |
| Severity | S3 |
| Likelihood before controls | L4 |
| Control in SCRIPT | The channel plan follows the declared Pencil: `theChannelPlanFollowsTheDeclaredPencil` in `app/Tests/ScriptCoreTests/InkTests.swift`. Quality control reports a planned channel that the data do not show: `test_qc_reports_planned_channel_absent` in `python/tests/test_quality_features.py`. Simulated input refuses Pencil-only features: `test_simulator_refuses_pen_channels` in the same file. The journal records the device model, the asset code, the screen protector, the nib, and the last qualification. The reliability analysis between devices is design only. |
| Duty of the site | Qualify each device. Balance devices across groups. Record the device of each session. |
| Residual likelihood | L3 |
| Residual risk | Not acceptable for a screening claim. Acceptable for engineering work and for reliability studies. |
| Owner | Analyst and sponsor |

### H8. Labels leak into features

| Field | Entry |
| --- | --- |
| Hazard | Information about the questionnaire result reaches the features or the model. |
| Cause | Responses are in the same journal as the pen data. The time after the last task shows whether an operator handled a flag. An analyst sees the labels before the features are frozen. |
| Harm | An inflated accuracy and a misleading research result. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Control in SCRIPT | Questionnaire blocks never enter strokes: `test_questionnaire_blocks_stay_apart_from_strokes` in `python/tests/test_journal.py`. A pen-only copy has no questionnaire, response, or safety line, and ends at the last task: `test_pen_only_copy_passes_integrity_and_has_no_response` and `test_pen_only_copy_ends_at_the_last_trial` in `python/tests/test_export.py`. Practice trials and qualification sessions never enter the feature table: `test_practice_trials_never_enter_the_feature_table` and `test_qualification_sessions_never_enter_the_feature_table` in `python/tests/test_session_features.py`. `labels.safety_flag_count` is for audit and is never a label. The leakage witness and the null check of the evaluation harness are design only. |
| Duty of the site | Give feature analysts pen-only copies. Freeze the features and the analysis plan before the labels are joined. |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the analysis plan. |
| Owner | Analyst |

### H9. A participant is re-identified

| Field | Entry |
| --- | --- |
| Hazard | Someone links a journal or a label to a person. |
| Cause | Participant codes that are not random. Export folder names. Small groups by age band, site, and time. Handwriting. An identity key stored with the research data. |
| Harm | Disclosure of a participant's mental health data. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Control in SCRIPT | The journal has no field for a name, a date of birth, a signature, or contact details: `contract/journal.schema.json`. Storage errors never name a participant: `storageErrorsNameTheSessionAndNeverTheParticipant` in `app/Tests/ScriptCoreTests/StorageTests.swift`. The deletion log holds only a keyed hash of the code: `withdrawalDeletesOneParticipantAndLogsOnlyAKeyedHashOfTheCode` and `onlyTheDeviceSecretConfirmsAWithdrawal` in the same file. |
| Duty of the site | Random participant codes. The identity key kept apart from research data. Access control on the study store. A check for small groups before data leave the study. |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site duties. Handwriting stays in the data. |
| Owner | Site and sponsor |

### H10. An export reaches the wrong recipient

| Field | Entry |
| --- | --- |
| Hazard | A person outside the study receives an export. |
| Cause | A wrong transfer channel. A configuration with a wrong public key. A demo configuration in a real study. |
| Harm | Disclosure of a participant's health data. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Control in SCRIPT | Each export in a study configuration is encrypted to the study public key: `anEncryptedExportOpensWithTheContractSuiteInfoAndSessionID` and `aPlaintextExportIsMarkedDemoAndAnyExportKeyEncrypts` in `app/Tests/ScriptCoreTests/StorageTests.swift`. A study configuration without an export key is refused: `aStudyConfigurationWithoutAnExportKeyOrAPinIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift`. A stated key identifier must match the key: `aStatedKeyIDMustBeTheSHA256OfTheExportKey` in the same file. A wrong private key is refused: `test_wrong_private_key_is_refused_by_key_id_before_decryption` in `python/tests/test_export.py`. |
| Duty of the site | One approved transfer channel. A check of the configuration digest on each iPad. No demo configuration with real participants. |
| Residual likelihood | L2 |
| Residual risk | Acceptable. The folder name shows the participant code, and the manifest shows the file sizes. |
| Owner | Site |

### H11. An instrument text is not verified

| Field | Entry |
| --- | --- |
| Hazard | The app shows a questionnaire text that differs from the published form, or that the licence does not permit. |
| Cause | A typing error, a changed wording, a translation without validation, or a licence that does not cover the use. |
| Harm | Invalid labels, a missing safety item, or a licence breach. |
| Severity | S3 |
| Likelihood before controls | L2 |
| Control in SCRIPT | `ScriptCore` and scriptkin refuse an instrument whose `textStatus` is not `verified`: `anUnverifiedInstrumentIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift` and `test_unverified_or_out_of_range_instruments_are_refused` in `python/tests/test_contract.py`. `contract/instruments.json` is generated from `python/src/scriptkin/instruments.py`, and `./check` fails when they differ: `test_checked_in_contract_matches_the_generator` in `python/tests/test_contract.py`. The safety items are fixed: `test_phq9_item_9_is_the_only_safety_item_in_the_default_instruments` in the same file. Each instrument records its licence status and source. |
| Duty of the site | Use only the instruments and locales in `contract/instruments.json`. Ask the maintainers to add others. |
| Residual likelihood | L1 |
| Residual risk | Acceptable. The status `verified` records a human check that a test cannot repeat. |
| Owner | Maintainers |

### H12. SCRIPT is used outside the intended population

| Field | Entry |
| --- | --- |
| Hazard | SCRIPT is used with people outside ages 13 to 24, without an approved study, or to make a decision about a person. |
| Cause | A wrong age band. A clinical setting. A study without ethics approval. |
| Harm | Results from instruments that are not validated for the person, or a decision based on output with no clinical validity. |
| Severity | S3 |
| Likelihood before controls | L2 |
| Control in SCRIPT | The journal accepts only the age bands 13 to 17 and 18 to 24: `contract/journal.schema.json`. An instrument outside its validated ages is refused: `anInstrumentOutsideItsValidatedAgesIsRefused` in `app/Tests/ScriptCoreTests/StudyTests.swift` and `aBlockBelowTheInstrumentsValidatedAgeIsRejected` in `app/Tests/ScriptCoreTests/DecideTests.swift`. SCRIPT shows no score to anyone (H2). Each session records the ethics approval identifier. |
| Duty of the site | Check the participant's age against the site records before Setup. Use SCRIPT only under the [intended use](INTENDED_USE.md). |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site check. The app cannot check a participant's real age. The PHQ-8 has no adolescent validation, but its entry in `contract/instruments.json` permits ages 13 to 17. |
| Owner | Site and sponsor |

### H13. Research data enter a device backup

| Field | Entry |
| --- | --- |
| Hazard | A device backup holds journals, exports, damaged journals, or the deletion log. |
| Cause | The app does not exclude its research folders from backup, and the iPad has backups turned on. |
| Harm | Disclosure of a participant's health data, and copies that withdrawal cannot reach. |
| Severity | S3 |
| Likelihood before controls | L3 |
| Control in SCRIPT | Backup exclusion of `Sessions/`, `Staging/`, `Exports/`, `Damaged/`, and the deletion log is design only ([design section 17](DESIGN.md#17-privacy-and-withdrawal)). The device secret of the deletion log is a Keychain item that stays on the device: `KeychainDeviceSecret` in `app/Sources/ScriptCore/Storage.swift`. |
| Duty of the site | Turn off device backups on each study iPad. Check each release before use. |
| Residual likelihood | L3 |
| Residual risk | Not acceptable for a study release. Implement backup exclusion in the app, with a test, before the first study. |
| Owner | Maintainers and site |

### H14. A session has the wrong participant code

| Field | Entry |
| --- | --- |
| Hazard | The operator enters a valid participant code that belongs to another participant. |
| Cause | A typing error, or a mix-up of two participants. |
| Harm | Labels joined to the wrong pen data, a withdrawal that misses a session, or a safety follow-up for the wrong person. |
| Severity | S2 |
| Likelihood before controls | L3 |
| Control in SCRIPT | A participant code must match `^[A-Z0-9-]{3,16}$`. SCRIPT has no check digit and cannot detect a valid code for the wrong person. |
| Duty of the site | Read the code from the site's records, and check it again before the handoff. Use codes that differ in more than one character. |
| Residual likelihood | L2 |
| Residual risk | Acceptable with the site check. |
| Owner | Site |
