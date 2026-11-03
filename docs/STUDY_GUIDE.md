# SCRIPT study guide

This guide tells site staff how to prepare and run a SCRIPT study. It is for the study lead, the operators, the safety responder, the reviewer, and the analyst who receives exports. Read the [intended use](INTENDED_USE.md) and the [data governance](DATA_GOVERNANCE.md) documents first.

The guide follows [the design](DESIGN.md#2-usage). Before a study starts, check that the installed release does each step as this guide describes. The [dry run](#train-the-operators-and-do-a-dry-run) is the check.

## Before the study

Do these tasks once for each study. Do the device tasks again for each new iPad.

### Get the approvals and the documents

1. Get the ethics approval. Record its identifier for `ethics.approvalID`.
2. Record the protocol version and the safety protocol version for the `ethics` block.
3. Write a consent document for adults, and a guardian permission form and an assent form for ages 13 to 17.
4. Give each document an identifier and a version. The `consentModel` block lists them for each age band.
5. Write the risk procedures into the consent and assent documents.
6. For a self-administered block with a safety item, write a monitoring disclosure. Give it a version.

The rules for minors depend on the jurisdiction. Check locally. These sources show some of the differences:

- United States. The rules are in Title 45 of the Code of Federal Regulations (CFR), part 46. Assent is more than a failure to object ([45 CFR 46.402](https://www.law.cornell.edu/cfr/text/45/46.402)). The institutional review board (IRB) decides the assent provisions and how assent is documented ([45 CFR 46.408](https://www.law.cornell.edu/cfr/text/45/46.408)).
- United Kingdom. The Health Research Authority (HRA) updated its guidance on research with children on 10 July 2026 ([HRA guidance on research involving children](https://www.hra.nhs.uk/about-us/news-updates/hra-guidance-on-research-involving-children/)). Scotland has different rules.
- India. The ICMR (Indian Council of Medical Research) guidelines ask for written assent from about age 12 or 13, plus parental consent. Sources differ on the exact age ([summary of the ICMR guidelines](https://www.mondaq.com/india/life-sciences-biotechnology-nanotechnology/654548/national-ethical-guidelines-for-biomedical-research-involving-children-icmr)).

The US National Institute of Mental Health (NIMH) asks studies to explain risk procedures in consent and assent ([NIMH considerations for researchers](https://www.nimh.nih.gov/funding/clinical-research/conducting-research-with-participants-at-elevated-risk-for-suicide-considerations-for-researchers)). A consensus on digital monitoring says that consent for remote monitoring must state three things ([Nock et al. 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8372411)):

- The participant must not rely on the study monitoring.
- How often the team checks responses.
- Whether a response can lead to follow-up or to a break of confidentiality.

### Write the safety protocol

The safety protocol is a site document. SCRIPT records each safety flag and each action. Trained people respond. [The design](DESIGN.md#15-questionnaires-and-safety) describes how the app handles a flag.

1. Name a trained responder for each supervised session. Name a clinical backup.
2. For each action code, state what the responder does. The default codes are `clinicianAssessment`, `referred`, `emergencyServices`, `guardianContacted`, and `participantDeclinedHelp`.
3. State the local referral pathway and the emergency step.
4. State that the responder speaks with the participant in private.
5. State the time limits for each step.
6. State when the team contacts a guardian, and how.
7. For self-administered blocks with a safety item, name the reviewer role and the review times.

Use published guidance to write the protocol:

- The University of Pennsylvania IRB asks each plan to state four things ([Penn IRB guidance](https://irb.upenn.edu/wp-content/uploads/2023/02/Suicidal-Ideation-and-Behavior-Risk-Mitigation-Guidance.pdf)). They are the delay before review, the reviewer, the time limit to report to the principal investigator, and the resources for participants.
- NIMH asks a protocol to describe the response process and its staff, including clinical backup ([NIMH considerations for researchers](https://www.nimh.nih.gov/funding/clinical-research/conducting-research-with-participants-at-elevated-risk-for-suicide-considerations-for-researchers)).
- The NIMH ASQ (Ask Suicide-Screening Questions) tool counts a refusal to answer as a positive screen ([ASQ screening tool](https://www.nimh.nih.gov/research/research-conducted-at-nimh/asq-toolkit-materials/asq-tool/asq-screening-tool.shtml)). SCRIPT also counts a skipped safety item as a flag.
- The ASQ outpatient pathway screens youth apart from parents where possible. It sends an acute positive screen to emergency evaluation ([ASQ outpatient pathway](https://www.nimh.nih.gov/research/research-conducted-at-nimh/asq-toolkit-materials/youth-outpatient/suicide-risk-screening-pathway-outpatient-flowchart-description)).
- Without a real-time alert, the consensus says to review data at least every weekday, and within 24 hours for imminent risk ([Nock et al. 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8372411)).

The default self-administered blocks are the PHQ-8 (Patient Health Questionnaire-8) and the GAD-7 (Generalized Anxiety Disorder-7). They have no safety item, because no one can respond in real time. The PHQ-8 leaves out the safety item of the PHQ-9 (Patient Health Questionnaire-9) for the same reason ([PHQ-8 notes](https://camcops.readthedocs.io/en/latest/_sources/tasks/phq8.rst.txt)). A self-administered block with a safety item needs a `remoteReview` block. The app refuses the configuration when one of these rules fails:

- `imminentReviewWithinHours` is more than 24.
- `routineReviewWithinWeekdays` is more than 1.
- `exportEveryHours` is more than `imminentReviewWithinHours`.

Exports leave the iPad only through Files, by a person. Plan who exports each session, and how the export reaches the reviewer in time.

### Verify the crisis lists

Each crisis list in the configuration has a `jurisdiction`, a `locale`, a `version`, a `verifiedOn` date, and `entries`. The participant sees the list for the session's jurisdiction. Do not copy the demo lists into a study configuration without your own check.

1. For each jurisdiction in the configuration, open the official page of each service.
2. Check the name, the contact number, the hours, and the age limits of each service.
3. Check the emergency number on an official government page.
4. Remove a service that the check cannot confirm.
5. Set `verifiedOn` to the date of the check.
6. Set a new `version` for each changed list.
7. Record the person who checked each page and the date.
8. Check each list again before the study starts and at the interval that the protocol sets.

Services change. In September 2026, administrators were appointed for Papyrus, and its UK service HOPELINE247 stopped operating ([administrators' notice](https://opusllp.com/restructuring-insolvency/case-papyrus-in-administration/)). These official pages were starting points for the demo jurisdictions:

| Jurisdiction code | Official pages |
| --- | --- |
| `US` | [988 Suicide and Crisis Lifeline](https://988lifeline.org/) |
| `GB` | [Samaritans](https://www.samaritans.org/how-we-can-help/contact-samaritan/), [National Health Service (NHS) urgent help for mental health](https://www.nhs.uk/nhs-services/mental-health-services/where-to-get-urgent-help-for-mental-health/), and [Shout](https://giveusashout.org/) |
| `IN` | [Tele-MANAS](https://telemanas.mohfw.gov.in/home). This official site failed its certificate check during the project's research. Try it again, and confirm the numbers on another government page if it still fails. |
| `AU` | [Lifeline](https://www.lifeline.org.au/) and [Kids Helpline](https://kidshelpline.com.au/about) |

In the European Union, the number 116 123 is reserved for callers in psychological crisis. The number 116 111 is reserved for child helplines ([EU decision 2007/698 on 116 numbers](https://www.legislation.gov.uk/eudn/2007/698/data.htm?view=plain)). Check the service in each country, because the Commission's status page dates from 2013 ([116 numbers status page](https://digital-strategy.ec.europa.eu/en/library/state-play-implementation-116-numbers-2)).

### Make the study keys

The study key pair encrypts each export. The key custodian makes it on a computer that the custodian controls.

1. Save the script below as `make_export_key.py` in the custodian's key folder.
2. Open a terminal in the key folder.
3. Run `uv run --project <repository>/python --frozen python make_export_key.py study-export-key.bin`.
4. Copy the printed `recipientPublicKey` and `keyID` into the `export` block of the configuration.
5. Keep `study-export-key.bin` in the custodian's encrypted storage.
6. Keep a protected copy of the private key in a second place.
7. Never copy the private key to an iPad.

```python
import base64
import os
import sys

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from scriptkin import hpke

path = sys.argv[1]
private = X25519PrivateKey.generate()
raw_private = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
raw_public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "wb") as file:
	file.write(raw_private)
print("recipientPublicKey:", base64.b64encode(raw_public).decode())
print("keyID:", hpke.key_id(raw_public))
```

The script refuses to overwrite an existing key file. Only the owner of the file can read it.

The `uv run` commands in this guide make a Python environment in `python/.venv` of the repository. To keep it outside the repository, set `UV_PROJECT_ENVIRONMENT` to another folder first.

### Set the operator PIN

The configuration stores the operator PIN (personal identification number) only as a slow key derivation.

1. Save the script below as `make_pin_record.py`.
2. Run `uv run --project <repository>/python --frozen python make_pin_record.py`.
3. Enter the new PIN two times.
4. Copy the printed record into `operatorPin`.
5. Give the PIN only to trained operators.
6. When an operator leaves the study, make a new PIN and a new configuration version.

```python
import base64
import getpass
import hashlib
import json
import secrets

pin = getpass.getpass("New operator PIN: ")
if pin != getpass.getpass("Repeat the PIN: "):
	raise SystemExit("The two PINs differ.")
salt = secrets.token_bytes(16)
iterations = 600_000
key = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations, dklen=32)
record = {"salt": base64.b64encode(salt).decode(), "iterations": iterations, "pbkdf2Sha256": key.hex()}
print(json.dumps(record))
```

A short PIN is easy to find by trial, even with a slow derivation. Use the longest PIN that operators can enter without errors.

### Write the study configuration

The configuration controls the study without a code change. `contract/config.schema.json` defines its shape. [The design](DESIGN.md#14-study-configuration) gives its rules.

1. Copy `contract/configs/demo.json` to a new file outside the repository.
2. Set `demo` to `false`.
3. Set `study`, `sites`, `jurisdictions`, and `administration`.
4. Set the task order, the practice flags, the attempts, and the hands in `tasks`.
5. Keep the `battery` block unchanged. It must match the battery that the app ships.
6. Set the questionnaire blocks for each administration mode and age band.
7. Set the instruction texts and the `instructionVersion` for each locale.
8. Set the action codes, the crisis lists, and `remoteReview` in `safety`.
9. Set `consentModel` and `ethics` from your approved documents.
10. Set `export` and `operatorPin` from the two scripts above.
11. Run the check script below, and correct each problem that it prints.
12. Record the SHA-256 (Secure Hash Algorithm, 256-bit) digest of the file with `shasum -a 256 study.json`.

Each session records this digest as `study.configSHA256`. An analyst uses it to prove which configuration made a session.

Save this script as `check_config.py` in the folder that holds `study.json`. In that folder, run `uv run --project <repository>/python --frozen python check_config.py study.json <repository>/contract/config.schema.json`.

```python
import json
import sys

from jsonschema import Draft202012Validator

from scriptkin import studyconfig

config_path, schema_path = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as file:
	config = json.load(file)
with open(schema_path, encoding="utf-8") as file:
	schema = json.load(file)
problems = [error.message for error in Draft202012Validator(schema).iter_errors(config)]
if not problems:
	problems = studyconfig.validate(config)
print("\n".join(problems) or "The configuration passes every check.")
raise SystemExit(1 if problems else 0)
```

The default supervised blocks are the PHQ-9 and the GAD-7, for both age bands. The DASS-21 (Depression Anxiety Stress Scales-21) is for enrolled participants aged 14 or older only. Its results go to the researcher only ([DASS frequently asked questions](https://www2.psy.unsw.edu.au/dass/DASSFAQ.htm)). Its scales must not be changed ([DASS download page](https://www2.psy.unsw.edu.au/dass/down.htm)). The configuration check refuses the DASS-21 for the age band 13 to 17. To add another instrument, ask the SCRIPT maintainers. Its text must match the published form word for word, and its licence must permit the use.

### Set up each iPad

1. Use an iPad that the site owns and manages. Give it a device asset code.
2. Set a device passcode.
3. Install the iPadOS version that the device qualification will use. Do not update it during the study without a new qualification.
4. Turn off device backups. Do not back up the iPad to a computer.
5. Install the SCRIPT Capture release that the protocol names.
6. Import the study configuration through Files.
7. Check that the app shows the study, the version, and the digest that you recorded.
8. Turn on Guided Access, so that a participant cannot leave the app. Use Apple's instructions for the installed iPadOS version.
9. Set a Guided Access passcode that differs from the device passcode and the operator PIN.
10. Set a Focus, for example Do Not Disturb, so that no notification appears during a session.
11. Charge the iPad and the Apple Pencil. Record the screen protector and the state of the Pencil nib.
12. Check the free storage before each session day.

A 10-minute session makes a journal of about 20 to 30 MB (megabytes). During an export, the iPad holds two copies of the journal for a short time.

### Qualify each device

A qualification session tests one iPad and one Pencil ([design section 16](DESIGN.md#16-device-qualification)). It has three tasks:

- A reference-length check with a steel rule or an ID-1 card. An ID-1 card is 85.60 mm wide.
- A 10-second still hold.
- A straight line along a guide.

The qualification report also shows which channels the device recorded.

1. Run one qualification session for each combination of iPad, Pencil, iPadOS version, and app version.
2. Export the qualification session, and transfer it as for a study session.
3. Make the qualification report with the script below.
4. File the report with the device asset code.
5. Run a new qualification after a change of iPadOS, Pencil, screen protector, or app version.

```python
from pathlib import Path

import scriptkin as sk
from scriptkin import qualification

key = Path("/secure/study-export-key.bin").read_bytes()
report = qualification.qualify(sk.read_journal("incoming/QUAL-01_1a2b3c4d", private_key=key))
print(report.intervals, report.hold_jitter_mm, report.line_deviation_mm, report.scale, report.observed_channels)
```

The report describes what the device recorded. It does not decide whether two devices are comparable. Only the analysis plan decides that. When the iPad model is not in the device table, Setup asks for a measurement. Measure the bar with a steel rule.

### Train the operators and do a dry run

1. Train each operator on this guide, the safety protocol, and the data governance duties.
2. Reserve a participant code for tests in the identity key.
3. Run a full supervised session with the test code on each iPad.
4. Skip the safety item in the test questionnaire, so that the operator alert appears.
5. Record an action code, show the crisis list, and close the session.
6. Export, transfer, and check the export at intake.
7. Withdraw the test code on the iPad, and confirm the deletion.
8. Delete the test export from the transfer location and the study store.
9. Compare each step with this guide. Report each difference to the SCRIPT maintainers before the study starts.

## Run a supervised session

An operator is present for the whole session. Do the [checks before the session](#before-each-session) first.

1. On Home, tap **New session**.
2. In Setup, enter the participant code from the site's records.
3. Select the Pencil model: Pro, 2nd generation, or USB-C.
4. Select the battery.
5. Give the consent attestation for the participant's age band.
6. If Setup shows a bar of known length, measure it with a steel rule and enter the length.
7. Check the participant code again.
8. Start Guided Access.
9. When the app shows the handoff screen, give the iPad to the participant.
10. Stay near the participant. Do not look at the questionnaire answers.
11. Do not comment on the drawings.
12. Give only the accommodations that the protocol permits. Record each accommodation.
13. When the app asks for the iPad back, take it.
14. If the app shows a safety alert, [handle the safety alert](#handle-a-safety-alert) before you continue.
15. Show the participant the crisis list that the app displays at the end of the session.
16. On the thank-you screen, press and hold with three fingers for 2 seconds to open Review.
17. In Review, check the duration, the sample rate, and the channels of each trial.
18. Repeat a trial, or add a mark, if the protocol requires it.
19. Close the session.
20. [Export the session](#export-and-transfer-a-session).

The participant sees no score, no replay, and no quality value. The handoff screen after a questionnaire block is the same with and without a flag. The app refuses to close a session while a flag has no action code. It also refuses to close until the crisis list has appeared after the last questionnaire block.

A mark is one of `repeatRequested`, `disturbance`, `deviceProblem`, or `exclude`. An accommodation is one of `readAloud`, `largerText`, `extraTime`, `positionAssist`, or `other`.

## Run a self-administered session

No operator is present during a self-administered session. The enrolment record of the study gives the consent attestation, with the role `enrolment`.

1. Prepare the iPad with the study configuration, Guided Access, and a Focus.
2. Give the participant the instructions that the protocol names.
3. If the participant takes part remotely, confirm that the participant is in a private place.
4. Let the participant complete the tasks and the questionnaire blocks.
5. Make sure the session is exported within `exportEveryHours`, if the configuration has a `remoteReview` block.
6. Make sure the reviewer checks each export within the review times.

The app never shows an operator alert in a self-administered session. The participant sees the crisis list at the end of each session with a questionnaire block. If a block has a safety item, the app also shows the crisis list at once when the item is flagged. The app never tells the participant that a response was flagged.

For remote participation by minors, published studies used extra identity checks. One online cohort of ages 12 to 25 used a linked parent survey, date-of-birth checks, and attention checks ([online youth cohort](https://pmc.ncbi.nlm.nih.gov/articles/PMC9717385)). Decide the checks in the protocol.

## Handle a safety alert

A safety alert is a neutral notice on the operator screen. It appears when a participant endorses or skips a safety item in a supervised session.

1. If the participant is in immediate danger, do the emergency step of the safety protocol first.
2. Do not discuss the alert in front of other people.
3. Call the responder that the safety protocol names.
4. Let the responder speak with the participant in private.
5. Record one action code for each flag. The app asks for the oldest open flag first.
6. Record the name of the responder, the time, and the outcome in the site's safety record.
7. Continue the session at the crisis list.

The journal holds only the action code and the role of the person who recorded it. The site's safety record holds the details.

In a self-administered session with remote review, the reviewer finds flags in each export:

```python
from pathlib import Path

import scriptkin as sk
from scriptkin import labels

key = Path("/secure/study-export-key.bin").read_bytes()
recording = sk.read_journal("incoming/P0042_3f2a9c1e", private_key=key)
print(labels.safety_flag_count(recording))
```

When the count is more than zero, the reviewer follows the safety protocol within the review time. The site holds the participant's contact details. The flag count is for safety review and audit only. It is never a research label.

## Handle an interruption

- **Crash.** On the next launch, Home shows the session as interrupted. The open trial is marked interrupted. Repeat the trial, continue, or close the session.
- **App in the background.** The trial ends with the reason `backgrounded`. Guided Access prevents most of these events.
- **Pencil problem.** Add the mark `deviceProblem` to the trial. Repeat the trial when the Pencil works.
- **Participant stops.** Respect the decision. The end reason `declined` means that the participant chose to stop. The end reason `unable` means that the participant could not do the task.
- **Damaged journal.** The app moves a journal with damage before its last line to the Damaged folder, unchanged. Transfer it to the study store with the same controls as an export. Tell the sponsor. Do not delete it unless the protocol or a withdrawal requires it.
- **Write failure.** After a failed write, the journal accepts no more lines. Free storage, then relaunch the app. Home shows the session as interrupted.

## Export and transfer a session

1. Close the session.
2. Export the session. The app verifies the export folder and then deletes the source journal.
3. In Files, open On My iPad > SCRIPT > Exports.
4. Move the export folder through the approved transfer channel.
5. At intake, check the export with the script below.
6. Record a receipt: the session identifier, the folder name, the ciphertext digest, the date, and your name.
7. Do not add a second copy of a session that the study store already holds.
8. Delete the export from the iPad and from the transfer location after the receipt.

```python
from pathlib import Path

import scriptkin as sk

key = Path("/secure/study-export-key.bin").read_bytes()
recording = sk.read_journal("incoming/P0042_3f2a9c1e", private_key=key)
print(recording.session_id)
```

`read_journal` raises `JournalError` when the export fails a check. Do not delete the device copy of an export that fails. The export folder name holds the participant code. Treat the name as personal data.

## Withdraw a participant

Follow the [withdrawal and deletion procedure](DATA_GOVERNANCE.md#withdrawal-and-deletion-procedure). The steps on each iPad are:

1. Open the withdrawal screen and enter the participant code.
2. Confirm two times.
3. Record the session identifiers that the app reports as deleted.
4. Enter the code again to confirm the deletion against the deletion log.

## Session checklists

### Before each session

- [ ] The iPad and the Pencil are charged.
- [ ] The free storage is enough for the session and its export.
- [ ] The app shows the study configuration digest that the site recorded.
- [ ] The crisis list for the session's jurisdiction was checked within the interval that the protocol sets.
- [ ] Guided Access and the Focus are on.
- [ ] The device qualification for this iPad, Pencil, iPadOS, and app version is on file.
- [ ] For a supervised session, the responder is available.
- [ ] The consent, assent, or guardian permission is on file for the participant code.
- [ ] The room is private and quiet.

### During each session

- [ ] The participant code and the Pencil model in Setup are correct.
- [ ] Each accommodation is recorded.
- [ ] Each safety alert has an action code and an entry in the site's safety record.
- [ ] The participant saw the crisis list at the end.

### After each session

- [ ] Each trial in Review has a plausible duration, sample rate, and channel set.
- [ ] The session is closed and exported.
- [ ] The export is transferred, checked at intake, and has a receipt.
- [ ] The device copy is deleted after the receipt.
- [ ] Each damaged journal is reported to the sponsor.
