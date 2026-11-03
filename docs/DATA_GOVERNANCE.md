# SCRIPT data governance

This document describes how research data move through SCRIPT and who is responsible for them at each stage. It lists the threats, the controls that SCRIPT provides, and the duties that stay with the site. It gives the withdrawal procedure and a checklist for four data protection laws.

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
| Deletion log | `Deletions.jsonl` on the iPad | Time, a keyed hash of the participant code, and session identifiers |

The journal never holds a name, a date of birth, a signature, or contact details. The site keeps identities, signed forms, and guardian contact details in its own systems.

Treat every journal, export, and derived file as personal health data. A participant code alone does not make data anonymous. Handwriting and drawing movement can also carry personal characteristics.

## Data flow

Research data pass through seven stages. Each stage makes a new copy or a new kind of data.

```text
1 iPad journal -> 2 iPad export (Files) -> 3 transfer -> 4 study store (intake) -> 5 analysis
                                                                                    |-> 6 pen-only copies
                                                                                    |-> 7 labels
```

1. **Device.** The app writes one journal for each session to `Library/Application Support/Sessions/` in its container. The journal is plaintext on the iPad. The app moves a journal with damage before its last line to `Documents/Damaged/`, unchanged.
2. **Export through Files.** The operator closes the session and exports it. The app writes the export to `Documents/Exports/<participant code>_<first 8 hex digits of the session identifier>/`. Files shows it under On My iPad > SCRIPT > Exports. The app verifies the export, and only then deletes the source journal.
3. **Transfer.** The operator moves the export folder off the iPad with Files. The site chooses the channel. SCRIPT has no network code.
4. **Intake.** The site copies the export to its study store and checks it with `read_journal`. It removes duplicates by session identifier and records a receipt.
5. **Analysis.** The analyst opens an encrypted export with the study private key. Each decrypted file or derived table is a new copy.
6. **Pen-only copies.** `pen_only` writes a plaintext copy without questionnaire, response, and safety lines. The copy ends at the last task. Its `derivation.json` file names the source session and the SHA-256 (Secure Hash Algorithm, 256-bit) digest of the source journal.
7. **Labels.** `labels.labels` gives a questionnaire score or a refusal for each scale of each block. Each label carries the participant code and the session identifier.

The export folder name holds the participant code, also for an encrypted export. The manifest holds the session identifier, the key identifier, and the file sizes and digests.

## Roles

Four roles share the duties. One person can hold more than one role if the protocol permits it.

| Role | Duties |
| --- | --- |
| Sponsor | Owns the protocol and the study configuration. Names the key custodian for the study private key. Sets the retention schedule. Agrees the data protection roles with each site. |
| Site | Keeps identities, consent records, and guardian contacts. Owns and manages the iPads. Trains operators. Runs the safety protocol with a named responder. Receives exports, keeps receipts, and carries out withdrawals across every copy. |
| Operator | Runs sessions. Gives the consent attestation in supervised sessions. Follows the safety protocol for each alert and records the action code. Exports, transfers, and withdraws data on the iPad. |
| Analyst | Reads exports in an approved environment. Uses pen-only copies until the analysis plan permits the labels to be joined. Never contacts a participant and never shows a score to a participant. |

## Threat model

The table names five threats. The [hazard log](HAZARDS.md) covers safety and measurement hazards.

| Threat | What it exposes | Control in SCRIPT | Duty of the site | Residual risk |
| --- | --- | --- | --- | --- |
| Lost or stolen iPad | Journals not yet exported, exports, and damaged journals | File protection, encrypted exports, and no identities on the iPad | Device passcode, device management with remote erase, export at the end of each session, and an incident report | After the first unlock since a restart, the system can read unexported journals and damaged journals. A person who can unlock the iPad can read them. |
| Copied export | One session for each copied folder | Encryption to the study public key, and digests in the manifest | An approved transfer channel, and no email or personal cloud storage | The folder name shows the participant code. A demo export is plaintext. |
| Backup leakage | Every file in the app container | Backup exclusion of the research folders (design only) | Turn off device backups, and check each release | A release without backup exclusion copies research data into a device backup. |
| Re-identification | A person behind a participant code | No names, dates of birth, or contact details on the iPad. Storage errors never name a participant. The deletion log holds only a keyed hash. | Random participant codes, an identity key kept apart, and access control on the study store | Age band, jurisdiction, session times, device asset code, and handwriting can narrow the set of possible people. |
| Label leakage | Questionnaire answers or safety events seen by a feature analyst | Pen-only copies. Questionnaire blocks never enter strokes. | Give feature analysts pen-only copies. Freeze features before labels are joined. | The full export still holds the responses. Anyone with the private key can read them. |

## Controls that SCRIPT provides

"In code" means that `ScriptCore` or scriptkin implements the control. The app screens are design only, so a release must still call this code. "Design only" means that [the design](DESIGN.md#17-privacy-and-withdrawal) specifies the control, and no code in this repository implements it. Check the status of each control in each release before a study uses it.

| Control | What it does | Code and tests | Status |
| --- | --- | --- | --- |
| File protection | The app folders and the deletion log use iOS file protection `completeUntilFirstUserAuthentication`. Files stay encrypted after a restart until the first unlock. | `Storage.createDirectories` for the folders and `Storage.withdraw` for the deletion log, in `app/Sources/ScriptCore/Storage.swift`. No automated test, because the protection applies on iOS only. | In code |
| Backup exclusion | The app sets `isExcludedFromBackup` on `Sessions/`, `Staging/`, `Exports/`, `Damaged/`, and the deletion log. | None | Design only |
| Encrypted exports | HPKE (Hybrid Public Key Encryption, defined in Request for Comments (RFC) 9180) seals each journal to the study public key. Only the study private key opens it. | `ExportCipher` in `app/Sources/ScriptCore/Storage.swift`. `anEncryptedExportOpensWithTheContractSuiteInfoAndSessionID` and `aPlaintextExportIsMarkedDemoAndAnyExportKeyEncrypts` in `app/Tests/ScriptCoreTests/StorageTests.swift`. | In code |
| Key and integrity checks on read | scriptkin refuses a wrong private key, a changed ciphertext, and an export for another session. | `python/src/scriptkin/formats/export.py`. `test_tampered_or_misaddressed_ciphertext_is_refused` and `test_wrong_private_key_is_refused_by_key_id_before_decryption` in `python/tests/test_export.py`. | In code |
| Study configuration rules | A study configuration without an export key or an operator PIN (personal identification number) is refused. A stated key identifier must match the key. | `validate` in `app/Sources/ScriptCore/Study.swift` and `python/src/scriptkin/studyconfig.py`. `aStudyConfigurationWithoutAnExportKeyOrAPinIsRefused` and `aStatedKeyIDMustBeTheSHA256OfTheExportKey` in `app/Tests/ScriptCoreTests/StudyTests.swift`. | In code |
| Operator PIN | The configuration holds the PIN only as a PBKDF2 (Password-Based Key Derivation Function 2) key. The derivation uses HMAC (hash-based message authentication code) with SHA-256. It needs a salt of at least 16 bytes and at least 600,000 iterations. | `OperatorPin` in `app/Sources/ScriptCore/Study.swift`. `theOperatorPinIsASlowKeyThatMatchesOnlyItsPin` in `app/Tests/ScriptCoreTests/StudyTests.swift`. | In code |
| Deletion log | Withdrawal deletes every device copy for one participant code. It first logs the time, an HMAC of the code, and the session identifiers. The HMAC key never leaves the iPad. | `Storage.withdraw` and `KeychainDeviceSecret` in `app/Sources/ScriptCore/Storage.swift`. `withdrawalDeletesOneParticipantAndLogsOnlyAKeyedHashOfTheCode` and `onlyTheDeviceSecretConfirmsAWithdrawal` in `app/Tests/ScriptCoreTests/StorageTests.swift`. | In code |
| No identities on the iPad | The journal has no field for a name, a date of birth, a signature, or contact details. A participant code matches `^[A-Z0-9-]{3,16}$`. | `contract/journal.schema.json` refuses unknown keys. No test checks the absent fields. `storageErrorsNameTheSessionAndNeverTheParticipant` in `app/Tests/ScriptCoreTests/StorageTests.swift` checks that storage error text leaves out the participant code. | In code |
| Pen-only copies | A copy for label-blind analysis has no questionnaire, response, or safety line. It ends at the last task. | `pen_only` in `python/src/scriptkin/formats/export.py`. `test_pen_only_copy_passes_integrity_and_has_no_response` and `test_pen_only_copy_ends_at_the_last_trial` in `python/tests/test_export.py`. | In code |

HPKE base mode does not identify the sender. Anyone with the study public key can make an export that the study private key opens. A digest proves that a file did not change. It does not prove who made the file.

## Duties that stay with the site

SCRIPT cannot do these tasks. The site does them and records them.

- **Identity.** Assign random participant codes. Do not use initials, dates, or record numbers in a code. Keep the identity key apart from all research data.
- **Consent records.** Keep signed forms, assent records, and guardian contact details in the site's own systems.
- **Devices.** Use a passcode and device management on each iPad. Use each iPad for the study only. Turn off device backups.
- **Keys.** Make the study key pair on a computer that the key custodian controls. Keep the private key off every iPad. Keep a protected copy of the private key, because a lost private key makes every encrypted export unreadable.
- **Transfer.** Name one approved transfer channel in the protocol. Do not use email, messaging apps, or personal cloud storage.
- **Intake.** Check each export with `read_journal` before you delete the device copy. Record a receipt with the session identifier and the digest from the manifest.
- **Plaintext files.** Handle damaged journals and demo exports as sensitive data. They are not encrypted.
- **Access.** Limit the study store and the analysis environment to named people.
- **Incidents.** Report a lost iPad or a misdirected export under the site's breach procedure. Notification duties and time limits depend on the law. Check locally.
- **Retention.** Apply the sponsor's retention schedule to every copy.

## Withdrawal and deletion procedure

Use this procedure when a participant withdraws and asks for deletion. The consent documents state which data the site can still remove, for example from a completed analysis. Check locally.

1. Record the request in the site's withdrawal register, with the participant code and the date.
2. Find every iPad that recorded the participant. Use the device asset codes in the site's session records.
3. On each iPad, open the withdrawal screen and enter the participant code. Confirm two times.
4. Record the session identifiers that the app reports as deleted.
5. If the app reports journals without a readable participant code, give them to the sponsor for a decision.
6. On each iPad, enter the participant code again to confirm the deletion against the deletion log.
7. Delete the export folders whose names start with `<participant code>_` from each transfer location.
8. Delete the same export folders from the study store.
9. Delete decrypted copies and pen-only copies. A pen-only copy names its source in `derivation.json` as `sourceSessionID`.
10. Delete the participant's rows from feature tables and label tables.
11. Record the expiry date of each backup that still holds a copy.
12. Mark the participant code as withdrawn in the identity key. Keep only the records that the protocol requires.
13. Record each location, the date, and the person who deleted the data.

The app deletes a journal only when its first line names the participant code. It deletes an export only when the folder name starts with `<participant code>_`. It also deletes every `.recovering` file, which is a temporary copy of a journal. Copies outside the iPad are the site's duty.

## Retention

SCRIPT sets no retention period. The protocol sets it, and it must cover every copy in the data flow.

- **iPad.** Keep an export on the iPad only until the site has a verified receipt for it.
- **Study store.** Keep exports for the period that the protocol states.
- **Study private key.** Keep the key while any encrypted export must stay readable. When every copy of the key is destroyed, no one can open its exports. Whether this counts as deletion depends on the law. Check locally.
- **Deletion log.** The log stays on the iPad and holds no participant code. The site decides how long the iPad keeps it.

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
