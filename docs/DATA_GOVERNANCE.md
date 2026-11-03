# SCRIPT data governance

This document describes how research data move through SCRIPT and who is responsible for them at each stage. It lists the threats, the controls that SCRIPT has, and the duties that stay with the site. It gives the withdrawal procedure and a checklist for four data protection laws.

This document is not legal advice. The duties of a study depend on its jurisdictions, its sponsor, and its sites. Where a rule depends on the jurisdiction, this document says "Check locally".

## Data that SCRIPT records

SCRIPT records pseudonymous data. A participant code links a journal to a person only through the site's identity key. The identity key is the site's list that maps each participant code to a person.

| Data | Where it is | Example |
| --- | --- | --- |
| Pen samples | Journal lines `sample`, `correction`, `hover`, and `finger` | Position, time, force, tilt, and roll of each Pencil sample |
| Session context | Journal line `sessionOpened` | Participant code, age band, jurisdiction, locale, administration mode, device asset code, and visit |
| Consent and ethics | Journal line `sessionOpened` | Attestations and document versions only |
| Questionnaire responses | Journal lines `questionnaireStarted`, `response`, and `questionnaireEnded` | The instrument, the item identifiers, and the response values |
| Safety records | Journal lines `safetyFlagged`, `safetyShown`, and `safetyAction` | The flagged item, each display of the crisis list, and the operator's action code |
| Derived data | Analyst files | Feature tables, quality findings, questionnaire scores, and pen-only copies |
| Deletion log | `Library/Application Support/Deletions.jsonl` on the iPad | Time, a keyed hash of the participant code, and session identifiers |
| Export index | `Library/Application Support/ExportIndex.jsonl` on the iPad | The name of each export and a keyed hash of its participant code |

The journal never holds a name, a date of birth, a signature, or contact details. The site keeps identities, signed forms, and guardian contact details in its own systems.

Treat every journal, export, and derived file as personal health data. A participant code alone does not make data anonymous. Handwriting and drawing movement can also carry personal characteristics.

## Data flow

Research data pass through seven stages. Each stage makes a new copy or a new kind of data.

```text
1 iPad journal -> 2 iPad export (Files) -> 3 transfer -> 4 study store (intake) -> 5 analysis
                                                                                    |-> 6 pen-only copies
                                                                                    |-> 7 labels
```

1. **Device.** The app writes one journal for each session to `Library/Application Support/Sessions/` in its container. The journal is plaintext on the iPad. Files does not show this folder. When a journal has damage before its last line, the app moves it unchanged to `Library/Application Support/Damaged/`. Files does not show that folder either.
2. **Export.** The operator closes the session and exports it from Home. A self-administered participant can also tap **Save to Files** on the thank-you screen. The app writes the export to `Documents/Exports/<export name>/`. Files shows it under On My iPad > SCRIPT Capture > Exports. The app verifies the export, and only then deletes the source journal. The operator exports a damaged journal with **Export damaged journal** on Home, under the same export policy.
3. **Transfer.** The operator moves the export folder off the iPad. The operator uses Files, or file sharing in Finder on a connected Mac. The site chooses the channel. SCRIPT has no network code.
4. **Intake.** The site runs `scriptkin intake` on the transferred folders. Intake verifies each export, keeps one copy of each session in the study store, and writes a receipt.
5. **Analysis.** The analyst opens an encrypted export with the study private key. Each decrypted file or derived table is a new copy.
6. **Pen-only copies.** `pen_only` writes a plaintext copy without questionnaire, response, and safety lines. The copy ends at the last task. Its `derivation.json` file names the source session and the SHA-256 (Secure Hash Algorithm, 256-bit) digest of the source journal.
7. **Labels.** `labels.labels` gives a questionnaire score or a refusal for each scale of each block. Each label carries the participant code and the session identifier.

An export name is `SCRIPT_<study>_<site>_<first 8 hex digits of the session identifier>`. The package of a damaged journal is `SCRIPT_DAMAGED_<session identifier, lowercase>`. Neither name holds the participant code. The participant code is inside the journal, and inside the ciphertext when the export is encrypted. The manifest holds the session identifier, the key identifier, and the file sizes and digests.

No file outside the iPad links a participant code to an export name. The site keeps that link in its [session register](#keep-the-site-session-register).

## Roles

Four roles share the duties. One person can hold more than one role if the protocol permits it.

| Role | Duties |
| --- | --- |
| Sponsor | Owns the protocol and the study configuration. Names the key custodian for the study private key. Sets the retention schedule. Agrees the data protection roles with each site. |
| Site | Keeps identities, consent records, guardian contacts, and the session register. Owns and manages the iPads. Trains operators. Runs the safety protocol with a named responder. Receives exports, keeps receipts, and carries out withdrawals across every copy. |
| Operator | Runs sessions. Gives the consent attestation in supervised sessions. Follows the safety protocol for each alert and records the action code. Exports each session and records it in the session register. Transfers and withdraws data on the iPad. |
| Analyst | Reads exports in an approved environment. Uses pen-only copies until the analysis plan permits the labels to be joined. Never contacts a participant and never shows a score to a participant. |

## Threat model

The table names five threats. The [hazard log](HAZARDS.md) covers safety and measurement hazards.

| Threat | What it exposes | Control in SCRIPT | Duty of the site | Residual risk |
| --- | --- | --- | --- | --- |
| Lost or stolen iPad | Journals not yet exported, exports, and damaged journals | File protection, encrypted exports, and no identities on the iPad | Device passcode, device management with remote erase, export at the end of each session, and an incident report | After the first unlock since a restart, the system can read unexported journals and damaged journals. A person who can unlock the iPad can read them. |
| Copied export | One session for each copied folder | Encryption to the study public key, digests in the manifest, and export names without the participant code | An approved transfer channel, and no email or personal cloud storage | A demo export is plaintext. |
| Backup leakage | The research folders, the deletion log, and the export index | Backup exclusion of each research folder and log | Turn off device backups. Do not copy exports into a backed-up folder. | A copy that a person saves to another place in Files leaves the backup exclusion. |
| Re-identification | A person behind a participant code | No names, dates of birth, or contact details on the iPad. Home masks participant codes without the PIN. Storage errors never name a participant. The deletion log and the export index hold only a keyed hash. | Random participant codes, an identity key and a session register kept apart from research data, and access control on the study store | Age band, jurisdiction, session times, device asset code, and handwriting can narrow the set of possible people. |
| Label leakage | Questionnaire answers or safety events seen by a feature analyst | Pen-only copies. Questionnaire blocks never enter strokes. | Give feature analysts pen-only copies. Freeze features before labels are joined. | The full export still holds the responses. Anyone with the private key can read them. |

## Controls that SCRIPT has

Each control has one of three statuses:

- **Implemented.** The code exists, and an automated test in `./check` proves it. The table names the code and the test.
- **Implemented without a test.** The code exists, but no automated test proves it. The table says why.
- **Not implemented.** No code in this repository does it. A site duty must cover it.

A script checks that every code symbol, file, and test in this table exists. Check the status of each control again for each release before a study uses it.

| Control | What it does | Status | Code | Test |
| --- | --- | --- | --- | --- |
| File protection | The app folders, the deletion log, and the export index use iOS file protection `completeUntilFirstUserAuthentication`. Files stay encrypted after a restart until the first unlock. | Implemented without a test | `Storage.createDirectories` and `Storage.protect` in `app/Sources/ScriptCore/Storage.swift` | No test reads the protection class back. The tests run on a Mac, where the code sets no protection, and in the simulator. |
| Backup exclusion | The app sets `isExcludedFromBackup` on `Sessions/`, `Staging/`, `Exports/`, `Damaged/`, the deletion log, and the export index. | Implemented | `Storage.createDirectories` and `Storage.excludeFromBackup` in `app/Sources/ScriptCore/Storage.swift` | `researchDataStaysOutOfDeviceBackups` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| Encrypted exports | HPKE (Hybrid Public Key Encryption, RFC 9180) seals each journal to the study public key. Only the study private key opens it. | Implemented | `ExportCipher` in `app/Sources/ScriptCore/Storage.swift` | `anEncryptedExportOpensWithTheContractSuiteInfoAndSessionID` and `aPlaintextExportIsMarkedDemoAndAnyExportKeyEncrypts` in `app/Tests/ScriptCoreTests/StorageTests.swift`. `test_swift_encrypted_exports_open_to_the_plaintext_export` in `python/tests/test_journal.py`. |
| Key and integrity checks on read | scriptkin refuses a wrong private key, a changed ciphertext, and an export for another session. | Implemented | `read_journal` and `verify_export` in `python/src/scriptkin/formats/export.py` | `test_tampered_or_misaddressed_ciphertext_is_refused` and `test_wrong_private_key_is_refused_by_key_id_before_decryption` in `python/tests/test_export.py` |
| Study configuration rules | A study configuration without an export key or an operator PIN (personal identification number) is refused. A stated key identifier must match the key. | Implemented | `validate` in `app/Sources/ScriptCore/Study.swift`. `validate` in `python/src/scriptkin/studyconfig.py`. | `aStudyConfigurationWithoutAnExportKeyOrAPinIsRefused` and `aStatedKeyIDMustBeTheSHA256OfTheExportKey` in `app/Tests/ScriptCoreTests/StudyTests.swift`. `test_study_config_without_key_or_pin_is_refused` and `test_export_key_id_must_be_the_hash_of_the_public_key` in `python/tests/test_contract.py`. |
| Study key pair | `scriptkin config keygen` writes the private key file with mode 600 and prints the `export` member. | Implemented | `export_key_pair` in `python/src/scriptkin/studytools.py` | `test_config_keygen_gives_a_key_pair_that_opens_its_exports` and `test_config_keygen_makes_a_private_key_that_only_its_owner_reads` in `python/tests/test_studytools.py` |
| Operator PIN | The configuration holds the PIN only as a PBKDF2 (Password-Based Key Derivation Function 2) key. It uses HMAC-SHA256, a salt of 16 bytes or more, and 600,000 iterations or more. The operator control asks for it. | Implemented | `OperatorPin` in `app/Sources/ScriptCore/Study.swift`. `OperatorControl` and `PINSheet` in `app/ScriptApp/Components.swift`. `pin_record` in `python/src/scriptkin/studytools.py`. | `theOperatorPinIsASlowKeyThatMatchesOnlyItsPin` in `app/Tests/ScriptCoreTests/StudyTests.swift`. `test_pin_record_verifies_with_the_swift_test_vector` in `python/tests/test_studytools.py`. |
| Masked codes on Home | Home shows only the last 4 characters of a participant code until the operator enters the PIN in this launch. | Implemented | `SessionStore.title` in `app/ScriptApp/SessionStore.swift` | `homeShowsOnlyTheLastFourCharactersOfACodeUntilThePINUnlocks` in `app/ScriptAppTests/StoreTests.swift` |
| Export names without the code | An export name holds the study, the site, and part of the session identifier, never the participant code. | Implemented | `SessionOpened.exportName` in `app/Sources/ScriptCore/Journal.swift`. `Storage.damagedExportName` in `app/Sources/ScriptCore/Storage.swift`. | `anExportNameHoldsNoParticipantCodeAndWithdrawalStillFindsAnEncryptedExport` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| Export index | Each export adds its name and a keyed hash of its participant code to the export index, so withdrawal finds encrypted exports. | Implemented | `Storage.index` in `app/Sources/ScriptCore/Storage.swift` | `anExportNameHoldsNoParticipantCodeAndWithdrawalStillFindsAnEncryptedExport` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| Withdrawal on the iPad | Withdrawal deletes every journal, export, damaged journal, and damaged package of one code on the iPad. It first logs the time, an HMAC of the code, and the session identifiers. | Implemented | `Storage.withdraw`, `Storage.verifyWithdrawal`, and `KeychainDeviceSecret` in `app/Sources/ScriptCore/Storage.swift` | `withdrawalDeletesOneParticipantAndLogsOnlyAKeyedHashOfTheCode`, `withdrawalDeletesRecoveringCopiesAndReportsJournalsWithoutAReadableCode`, and `onlyTheDeviceSecretConfirmsAWithdrawal` in `app/Tests/ScriptCoreTests/StorageTests.swift`. `testWithdrawalNeedsTheCodeTwiceAndTheLogConfirmsIt` in `app/ScriptAppUITests/SessionFlowTests.swift`. |
| Withdrawal outside the iPad | A record that links a participant code to the export names and copies outside the iPad. | Not implemented | None | None |
| No identities in the journal | The journal schema has no field for a name, a date of birth, a signature, or contact details, and it refuses unknown keys. | Implemented without a test | `contract/journal.schema.json` | No test gives the schema a line with an identity field. |
| Storage errors without the code | A storage error names a session by its identifier, never by a file path or a participant code. | Implemented | `Storage.StorageError` in `app/Sources/ScriptCore/Storage.swift` | `storageErrorsNameTheSessionAndNeverTheParticipant` in `app/Tests/ScriptCoreTests/StorageTests.swift` |
| Damaged journals | A damaged journal stays out of Files. **Export damaged journal** publishes it as a package under the export policy. | Implemented | `Storage.moveToDamaged` and `Storage.publishDamaged` in `app/Sources/ScriptCore/Storage.swift`. `SessionStore.exportDamaged` in `app/ScriptApp/SessionStore.swift`. | `aDamagedJournalNeverAppearsInFiles` in `app/Tests/ScriptCoreTests/StorageTests.swift`. `aDamagedJournalOnHomeLeavesTheIPadAsADamagedPackage` in `app/ScriptAppTests/StoreTests.swift`. |
| Resume only for the same participant | An interrupted self-administered session resumes only after the participant types the code of the session. | Implemented | `SessionStore.confirmCode` in `app/ScriptApp/SessionStore.swift`. `ConfirmCodeScreen` in `app/ScriptApp/ParticipantScreens.swift`. | `aResumedSelfAdministeredSessionWaitsForTheCodeOfItsParticipant` in `app/ScriptAppTests/StoreTests.swift`. `testAnInterruptedSelfAdministeredSessionResumesOnlyForItsCode` in `app/ScriptAppUITests/SessionFlowTests.swift`. |
| Verified intake | Intake verifies each export, keeps one copy of each session, quarantines a failed or conflicting export, and writes receipts. A second run changes nothing. | Implemented | `intake` in `python/src/scriptkin/intake.py` | `test_intake_copies_a_verified_export_with_its_manifest_and_receipt`, `test_tampered_export_goes_to_quarantine_with_its_reason`, `test_a_conflict_puts_both_journals_in_quarantine`, and `test_a_second_intake_changes_no_byte_in_the_store` in `python/tests/test_intake.py` |
| Encrypted store | The store keeps an encrypted export encrypted. It keeps a decrypted journal only with `--keep-plaintext`. | Implemented | `intake` in `python/src/scriptkin/intake.py` | `test_an_encrypted_export_stays_encrypted_unless_the_plaintext_is_kept` in `python/tests/test_intake.py` |
| Damaged packages at intake | Intake files a damaged package under `damaged/` with its decrypted journal, and never as a session. | Implemented | `intake` in `python/src/scriptkin/intake.py` | `test_a_damaged_package_is_filed_for_review_and_never_as_a_session` and `test_the_app_damaged_package_is_filed_for_review` in `python/tests/test_intake.py` |
| QC report without personal data | The QC report holds no label, score, response, feature value, or participant code. | Implemented | `qc_report` in `python/src/scriptkin/qcreport.py` | `test_qc_report_holds_no_label_score_or_response` in `python/tests/test_qcreport.py` |
| Harness refusals without a code | A leakage refusal in a report gives a count of participants and names none. | Implemented | `witness_refusal` in `python/src/scriptkin/evaluate/screening.py` | `test_a_leakage_refusal_counts_the_participants_and_names_none` in `python/tests/test_screening.py` |
| Pen-only copies | A copy for label-blind analysis has no questionnaire, response, or safety line, and it ends at the last task. It refuses a session with a block before the tasks. | Implemented | `pen_only` in `python/src/scriptkin/formats/export.py` | `test_pen_only_copy_passes_integrity_and_has_no_response`, `test_pen_only_copy_ends_at_the_last_trial`, and `test_pen_only_refuses_a_questionnaire_block_before_the_tasks` in `python/tests/test_export.py` |

HPKE base mode does not identify the sender. Anyone with the study public key can make an export that the study private key opens. A digest proves that a file did not change. It does not prove who made the file.

## Duties that stay with the site

SCRIPT cannot do these tasks. The site does them and records them.

- **Identity.** Assign random participant codes. Do not use initials, dates, or record numbers in a code. Keep the identity key apart from all research data.
- **Session register.** Record each export in the [site session register](#keep-the-site-session-register). Keep the register apart from the research data, like the identity key.
- **Consent records.** Keep signed forms, assent records, and guardian contact details in the site's own systems.
- **Devices.** Use a passcode and device management on each iPad. Use each iPad for the study only. Turn off device backups.
- **Keys.** Make the study key pair on a computer that the key custodian controls. Keep the private key off every iPad. Keep a protected copy of the private key, because a lost private key makes every encrypted export unreadable.
- **Transfer.** Name one approved transfer channel in the protocol. Do not use email, messaging apps, or personal cloud storage.
- **Intake.** Take each export into the study store with `scriptkin intake` before you delete the device copy. Keep the intake report and the receipts.
- **Plaintext files.** Handle damaged journals, decrypted journals, and demo exports as sensitive data. They are not encrypted.
- **Access.** Limit the study store, the session register, and the analysis environment to named people.
- **Incidents.** Report a lost iPad or a misdirected export under the site's breach procedure. Notification duties and time limits depend on the law. Check locally.
- **Retention.** Apply the sponsor's retention schedule to every copy.

## Keep the site session register

The session register is the site's only link from a participant code to the copies of that participant's data outside the iPad. SCRIPT does not keep it. Keep it in the site's own systems, with the same access control as the identity key.

Each row of the register is one export. Record these items:

| Item | Where to find it |
| --- | --- |
| Participant code | The site's records. Setup used this code. |
| Export name | The Export screen shows it after **Export**, **Export damaged journal**, or **Save to Files**. |
| Session identifier | The intake report. The receipt whose `source` ends with the export name gives the `sessionID`. The export name ends with its first 8 hex digits. |
| Device asset code | The asset label of the iPad. Setup recorded the same code. |
| Date and operator | The date of the export, and the operator who exported it. |
| Transfer locations | Each place that held a copy of the export folder. |
| Intake result | The `result` of the receipt: `accepted`, `duplicate`, `conflict`, `quarantined`, or `damaged`. |

1. After each export, record the participant code, the export name, the device asset code, the date, and the operator.
2. After intake, add the session identifier and the intake result to the same row.
3. Record each transfer location that held the folder.

## Withdrawal and deletion procedure

Use this procedure when a participant withdraws and asks for deletion. The consent documents state which data the site can still remove, for example from a completed analysis. Check locally.

The app removes the copies on the iPad. The site session register finds the copies outside the iPad.

1. Record the request in the site's withdrawal register, with the participant code and the date.
2. In the session register, find every row with the participant code. Note each device asset code, export name, and session identifier.
3. On each iPad that recorded the participant, finish or end any open session.
4. On Home, tap **Withdraw a participant**.
5. Enter the participant code in both fields.
6. Tap **Delete every recording of this code**, and then tap **Delete**.
7. Record the session identifiers that the app lists under **Deleted**.
8. If the app lists journals that could not be read, give them to the sponsor for a decision. The app did not delete them.
9. On the same iPad, tap **Verify a withdrawal**, enter the code, and tap **Check the deletion log**.
10. Confirm that the app reports a withdrawal for this code.
11. For each export name in the register, delete the export folder from each transfer location.
12. In the study store, delete `sessions/<session identifier>/` for each session identifier in the register.
13. Open `receipts/<session identifier>.json` in the study store. For each entry, delete `quarantine/<first 16 hex digits of exportSHA256>/` and `damaged/<first 16 hex digits of exportSHA256>/` when they exist.
14. Delete decrypted copies and pen-only copies. A pen-only copy names its source in `derivation.json` as `sourceSessionID`.
15. Delete the participant's rows from feature tables and label tables.
16. Record the expiry date of each backup that still holds a copy.
17. Mark the participant code as withdrawn in the identity key. Keep only the records that the protocol requires.
18. Record each location, the date, and the person who deleted the data.

The receipts and `intake.jsonl` in the study store hold session identifiers and source folder names, but no participant code. The protocol decides whether to keep them.

On the iPad, the app finds the data of a participant code in three ways:

- A journal or a damaged journal whose first line names the code.
- An export or a damaged package whose name the export index gives for the keyed hash of the code.
- An export whose name matches a journal of the code.

The app also deletes every `.recovering` file, which is a temporary copy of a journal. The app cannot find a damaged package whose first line did not decode. The session register finds that package by its session identifier.

## Retention

SCRIPT sets no retention period. The protocol sets it, and it must cover every copy in the data flow.

- **iPad.** Keep an export on the iPad only until the intake report shows the result `accepted`, `duplicate`, or `damaged` for it.
- **Study store.** Keep exports for the period that the protocol states.
- **Session register.** Keep the register while any copy of a session exists outside the iPad.
- **Study private key.** Keep the key while any encrypted export must stay readable. When every copy of the key is destroyed, no one can open its exports. Whether this counts as deletion depends on the law. Check locally.
- **Deletion log and export index.** Both stay on the iPad and hold no participant code. The site decides how long the iPad keeps them.

An append-only journal is a recording format. It is not a reason to keep data longer than the protocol permits.

## Applicability checklist

Answer each question for each site before the study starts. Record the answer and the person who gave it. Each answer depends on the jurisdiction and the study. Check locally.

### GDPR (European Union General Data Protection Regulation)

- [ ] Does the study handle personal data in the European Union? Check locally.
- [ ] Pseudonymised data that can be attributed to a person with additional information remain personal data ([GDPR Recital 26](https://gdpr-info.eu/recitals/no-26/)). SCRIPT data are pseudonymised. Check locally.
- [ ] Data concerning health are a special category. Their processing is prohibited unless a condition in Article 9(2) applies ([GDPR Article 9](https://gdpr-info.eu/art-9-gdpr/)). Which condition applies? Check locally.
- [ ] Which lawful basis applies? Confirm it with the data protection officer. Check locally.
- [ ] Article 8 sets an age for a child's consent to an information society service, where consent is the lawful basis. The age is 16, and a member state can set a lower age, but not below 13 ([GDPR Article 8](https://gdpr-info.eu/art-8-gdpr/)). Does Article 8 apply to this study? Check locally.
- [ ] Which organisation is the controller, and which is the processor? Check locally.
- [ ] Do data leave the European Union? Check locally.

### UK GDPR

- [ ] Does the study handle personal data in the United Kingdom? Check locally.
- [ ] UK GDPR sets the Article 8 age at 13. It excludes preventive or counselling services ([UK GDPR Article 8](https://www.legislation.gov.uk/eur/2016/679/article/8)). Does Article 8 apply? Check locally.
- [ ] The Information Commissioner's Office (ICO) explains that pseudonymised data can remain personal data ([ICO guidance on pseudonymisation](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/pseudonymisation/)). Check locally.
- [ ] Which lawful basis and which condition for health data apply? Check locally.

### HIPAA (United States Health Insurance Portability and Accountability Act)

- [ ] Is the site a covered entity or a business associate? HIPAA applies to these entities, as the Department of Health and Human Services (HHS) explains ([HHS covered entities page](https://www.hhs.gov/hipaa/for-professionals/covered-entities/index.html)). Check locally.
- [ ] A participant code alone does not meet HIPAA de-identification ([HHS de-identification guidance](https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html)). If HIPAA applies, which research permission applies? Ask the privacy officer. Check locally.
- [ ] Do state laws add duties? Check locally.

### India DPDP Act 2023 (Digital Personal Data Protection Act) and DPDP Rules 2025

The Act and its Rules start in phases. The Ministry of Electronics and Information Technology (MeitY) publishes the official notification. Confirm each date against it before you plan ([MeitY commencement notification](https://www.meity.gov.in/static/uploads/2025/11/c56ceae6c383460ca69577428d36828b.pdf)).

- [ ] The Rules were notified as G.S.R. 846(E) on 13 November 2025. The rules on the Data Protection Board started on that date ([DPDP Rules timeline](https://www.mirvolegal.com/insights/dpdp-rules-2025-compliance-timeline/)). Check locally.
- [ ] Registration of consent managers (Rule 4) starts about 13 November 2026 ([DPDP Rules timeline](https://www.mirvolegal.com/insights/dpdp-rules-2025-compliance-timeline/)). Check locally.
- [ ] The main duties start about 13 May 2027. They include notice, consent, security, breach reporting, children's data, and the exercise of rights ([DPDP Rules timeline](https://www.mirvolegal.com/insights/dpdp-rules-2025-compliance-timeline/)). The source reports a proposal to shorten this period, and it found no amending notification. Check locally.
- [ ] Under section 2(f), a child is a person under 18. Section 9 requires verifiable parental consent ([summary of the children's data rules](https://ssrana.in/articles/safeguarding-childrens-data-under-dpdp-law/)). Check locally.
- [ ] The Fourth Schedule has an exemption for health and mental health establishments that give health services ([summary of the exemptions](https://www.storyboard18.com/amp/digital/dpdp-rules-carve-out-key-exemptions-for-healthcare-providers-schools-and-childcare-services-processing-childrens-data-84208.htm)). Does it apply to this study? Check locally.
- [ ] Does the research exemption in section 17(2)(b), with the standards in Rule 16, apply ([summary of Rule 16](https://www.sansalegal.com/post/how-to-rely-on-the-research-and-statistical-exemption-under-rule-16-of-the-dpdp-rules-2025))? Check locally.

The section, rule, and schedule references above come from secondary summaries. Confirm each one against the official text.

### Other jurisdictions

The demo configuration also lists Australia. This document has no verified source for Australian privacy law. Check locally for every jurisdiction in the study configuration.
