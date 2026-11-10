# SCRIPT study guide

This guide tells site staff how to prepare and run a SCRIPT study. It is for the study lead, the operators, the safety responder, the reviewer, and the analyst who receives exports. Read the [intended use](INTENDED_USE.md) and the [data governance](DATA_GOVERNANCE.md) documents first.

The guide follows [the design](DESIGN.md#2-usage). Use only a release that went through the [release process](RELEASE.md). Before a study starts, check that the installed release does each step as this guide describes. The [dry run](#train-the-operators-and-do-a-dry-run) is the check.

## Run the scriptkin commands

The study team runs `scriptkin` on a computer that has the SCRIPT repository, [uv](https://docs.astral.sh/uv/), and Python 3.12 or later. In this guide, `scriptkin` stands for this command:

```sh
uv run --project <repository>/python --frozen scriptkin
```

uv makes a Python environment in `python/.venv` of the repository the first time. To keep the environment outside the repository, set `UV_PROJECT_ENVIRONMENT` to another folder first.

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
8. Name the person who decrypts each export for the reviewer. The key custodian keeps the study private key.

Use published guidance to write the protocol:

- The University of Pennsylvania IRB asks each plan to state four things ([Penn IRB guidance](https://irb.upenn.edu/wp-content/uploads/2023/02/Suicidal-Ideation-and-Behavior-Risk-Mitigation-Guidance.pdf)). They are the delay before review, the reviewer, the time limit to report to the principal investigator, and the resources for participants.
- NIMH asks a protocol to describe the response process and its staff, including clinical backup ([NIMH considerations for researchers](https://www.nimh.nih.gov/funding/clinical-research/conducting-research-with-participants-at-elevated-risk-for-suicide-considerations-for-researchers)).
- The NIMH ASQ (Ask Suicide-Screening Questions) tool counts a refusal to answer as a positive screen ([ASQ screening tool](https://www.nimh.nih.gov/research/research-conducted-at-nimh/asq-toolkit-materials/asq-tool/asq-screening-tool.shtml)). SCRIPT also counts a skipped safety item as a flag.
- The ASQ outpatient pathway screens youth apart from parents where possible. It sends an acute positive screen to emergency evaluation ([ASQ outpatient pathway](https://www.nimh.nih.gov/research/research-conducted-at-nimh/asq-toolkit-materials/youth-outpatient/suicide-risk-screening-pathway-outpatient-flowchart-description)).
- Without a real-time alert, the consensus says to review data at least every weekday, and within 24 hours for imminent risk ([Nock et al. 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8372411)).

SCRIPT counts a safety item as a flag in three cases:

- The participant gives a response of 1 or more to the item.
- The participant selects **Prefer not to answer** for the item.
- The block ends before the participant reaches the item. This happens when the operator ends the session, or when a crash interrupts the block.

The default self-administered blocks are the PHQ-8 (Patient Health Questionnaire-8) and the GAD-7 (Generalized Anxiety Disorder-7). They have no safety item, because no one can respond in real time. The PHQ-8 leaves out the safety item of the PHQ-9 (Patient Health Questionnaire-9) for the same reason ([PHQ-8 notes](https://camcops.readthedocs.io/en/latest/_sources/tasks/phq8.rst.txt)). A self-administered block with a safety item needs a `remoteReview` block. `scriptkin config check` and the app refuse the configuration when one of these rules fails:

- `imminentReviewWithinHours` is more than 24.
- `routineReviewWithinWeekdays` is more than 1.
- `exportEveryHours` is more than `imminentReviewWithinHours`.
- `selfAdministered.resumeWithinHours` is more than `imminentReviewWithinHours`.

Exports leave the iPad only by a person's action. SCRIPT does not export on a schedule, and it does not check `exportEveryHours`. Plan who exports each session, and how the export reaches the reviewer in time.

### Verify the crisis lists

Each crisis list in the configuration has a `jurisdiction`, a `locale`, a `version`, a `verifiedOn` date, and `entries`. The participant sees the list for the session's jurisdiction. `scriptkin config new` leaves the crisis lists empty, so the check refuses the configuration until you add them. Do not copy the demo lists into a study configuration without your own check.

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

1. Run `scriptkin config keygen --out <key folder>`. Use a new, empty key folder.
2. Copy the printed `recipientPublicKey` and `keyID` into the `export` block of the configuration.
3. Keep `study-export-key.bin` in the custodian's encrypted storage.
4. Keep a protected copy of the private key in a second place.
5. Never copy the private key to an iPad.

The command writes two files. `study-export-key.bin` holds the private key, and only its owner can read it. `study-export-key.public.json` holds the `export` block. The command refuses to overwrite a key file.

### Set the operator PIN

The configuration stores the operator PIN (personal identification number) only as a slow key derivation. The operator enters the PIN on a number pad, so a PIN has 4 or more digits and no other characters.

1. Run `scriptkin config pin --out operator-pin.json`.
2. Enter the new PIN two times.
3. Copy the content of `operator-pin.json` into `operatorPin`.
4. Delete `operator-pin.json`.
5. Give the PIN only to trained operators.
6. When an operator leaves the study, make a new PIN and a new configuration version.

A short PIN is easy to find by trial, even with a slow derivation. Use the longest PIN that operators can enter without errors.

With a PIN in the configuration, Home is locked after each launch and after each session that ends at the thank-you screen. Tap **Enter the operator PIN**, and enter the PIN one time. The PIN at the **Operator** control of the thank-you screen also opens Home.

After 5 wrong PINs, the app waits before the next try. The wait is 30 seconds, then 1, 2, 4, and 8 minutes, and then 15 minutes for each further wrong PIN. A relaunch does not end the wait. A correct PIN sets the count back to zero.

### Write the study configuration

The configuration controls the study without a code change. `contract/config.schema.json` defines its shape. [The design](DESIGN.md#14-study-configuration) gives its rules.

1. Run `scriptkin config new --from demo --out study.json`. The command lists the items that you must complete.
2. Set `study`, `sites`, `jurisdictions`, and `administration`.
3. Set the task order, the practice flags, the attempts, and the hands in `tasks`.
4. Keep the `battery` block unchanged. It must match the battery that the app ships.
5. Set the questionnaire blocks for each administration mode and age band.
6. Set the instruction texts and the `instructionVersion` for each locale.
7. Set the action codes, the crisis lists, and `remoteReview` in `safety`.
8. Set `selfAdministered.resumeWithinHours`. Without it, the value is 24.
9. Set `consentModel` and `ethics` from your approved documents.
10. Set `export` and `operatorPin` from the two commands above.
11. Run `scriptkin config check study.json`, and correct each problem that it lists.
12. Record the SHA-256 (Secure Hash Algorithm, 256-bit) digest that the check prints when the file passes.

Each session records this digest as `study.configSHA256`. An analyst uses it to prove which configuration made a session. Home shows the first 12 hexadecimal digits of the digest of the active configuration.

The default supervised blocks are the PHQ-9 and the GAD-7, for both age bands. The DASS-21 (Depression Anxiety Stress Scales-21) is for enrolled participants aged 14 or older only. Its results go to the researcher only ([DASS frequently asked questions](https://www2.psy.unsw.edu.au/dass/DASSFAQ.htm)). Its scales must not be changed ([DASS download page](https://www2.psy.unsw.edu.au/dass/down.htm)). The configuration check refuses the DASS-21 for the age band 13 to 17. To add another instrument, ask the SCRIPT maintainers. Its text must match the published form word for word, and its licence must permit the use.

### Set up each iPad

1. Use an iPad that the site owns and manages. Give it a device asset code.
2. Set a device passcode.
3. Install the iPadOS version that the device qualification will use. Do not update it during the study without a new qualification.
4. Turn off device backups. Do not back up the iPad to a computer.
5. Install the SCRIPT Capture release that the protocol names, as the [release process](RELEASE.md) states.
6. Import the study configuration. On Home, tap **Import a configuration**, and select the file in Files.
7. Check that Home shows the study, the version, and the digest that you recorded.
8. Turn on Guided Access, so that a participant cannot leave the app. Use Apple's instructions for the installed iPadOS version.
9. Set a Guided Access passcode that differs from the device passcode and the operator PIN.
10. Set a Focus, for example Do Not Disturb, so that no notification appears during a session.
11. Charge the iPad and the Apple Pencil. Record the screen protector and the state of the Pencil tip.
12. Check the free storage before each session day.

A 10-minute session makes a journal of about 20 to 30 MB (megabytes). The app does not start a session with less than 500 MB of free storage. During an export, the iPad holds two copies of the journal for a short time.

### Qualify each device

A qualification session tests one iPad and one Pencil ([design section 16](DESIGN.md#16-device-qualification)). It has three steps:

- A reference-length check. The bar on the screen is 100 mm long. Measure it with a steel rule or an ID-1 card, which is 85.60 mm wide.
- A 10-second still hold on a dot.
- A line drawn along a guide at an even speed.

Run one qualification session for each combination of iPad, Pencil, iPadOS version, and app version. Run a new qualification after a change of iPadOS, Pencil, screen protector, or app version.

1. On Home, tap **Device qualification**.
2. Enter your operator code and the device asset code.
3. Select the screen protector, the Pencil tip state, and the Pencil.
4. If the **Scale** section shows **Measure the screen**, measure the screen as the app asks.
5. Tap **Start the qualification**, and do the three steps. Tap **Begin** before and **Done** after each step.
6. Record the measured length of the bar in the qualification record.
7. Read the median sample interval, the sample rate, and the share of intervals longer than 30 ms.
8. Tap **Export**, and then [take the export into the study store](#take-exports-into-the-study-store).
9. Make the qualification report with the code below, and file it with the device asset code.

```python
from pathlib import Path

import scriptkin as sk
from scriptkin import qualification

key = Path("<key folder>/study-export-key.bin").read_bytes()
recording = sk.read_journal("<study store>/sessions/<session identifier>", private_key=key)
report = qualification.qualify(recording)
print(report.intervals, report.hold_jitter_mm, report.line_deviation_mm, report.scale, report.observed_channels)
```

Each later study session records the last qualification of its iPad and Pencil. The report describes what the device recorded. It does not decide whether two devices are comparable. Only the analysis plan decides that.

### Train the operators and do a dry run

1. Train each operator on this guide, the safety protocol, and the data governance duties.
2. Reserve a participant code for tests in the identity key.
3. Run a full supervised session with the test code on each iPad.
4. In the PHQ-9, select **Prefer not to answer** for item 9, so that the safety alert appears.
5. Record an action code, show the support list, and close the session.
6. Export the session, and record it in the session register.
7. Take the export into the study store, and check its receipt.
8. Withdraw the test code on the iPad, and confirm the deletion.
9. Delete the test export from the transfer location and the study store.
10. Compare each step with this guide. Report each difference to the SCRIPT maintainers before the study starts.

## Run a supervised session

An operator is present for the whole session. Do the [checks before the session](#before-each-session) first.

1. On Home, tap **New session**.
2. If Setup shows **Administration**, select **Supervised**.
3. Enter the participant code from the site's records.
4. Select the age band, the jurisdiction, the language, the site, and the writing hand.
5. Enter the visit code and the visit number.
6. Under **Consent**, tick each document that the participant or guardian signed.
7. Enter your operator code.
8. Enter the device asset code. Select the screen protector, the Pencil tip state, and the Pencil.
9. If the **Scale** section shows **Measure the screen**, measure the screen as the app asks.
10. Check the participant code again, and tap **Start the session**.
11. Start Guided Access.
12. When the app shows "Please give the iPad to the participant.", give the iPad to the participant.
13. Stay near the participant. Do not look at the questionnaire answers, and do not comment on the drawings.
14. When the app shows "Please give the iPad back to the researcher.", take the iPad.
15. Press and hold **Operator** with one finger for 2 seconds. Enter the PIN.
16. Review opens. If it shows a safety alert, [handle the safety alert](#handle-a-safety-alert) first.
17. In Review, check the duration, the sample count, the sample rate, and the channels of each trial.
18. Tap **Show the support list to the participant**, and give the iPad to the participant.
19. The participant reads the crisis list and taps **Continue**. The app closes the session.
20. When the app shows "Thank you.", take the iPad.
21. Press and hold **Operator** with one finger for 2 seconds, and enter the PIN. Home opens.
22. [Export the session](#export-a-session).

The participant starts each task with **Begin** and ends it with **Done**. A task also ends at its time limit, and no countdown shows. The participant answers each questionnaire item, or selects **Prefer not to answer**.

The participant sees no score, no replay, and no quality value. The hand-back screen after the questionnaires is the same with and without a flag. Review enables **Show the support list to the participant** only after each flag has an action code. In a session without a questionnaire block, Review shows **Close the session** instead.

The app does not record accommodations or marks, and it has no button to repeat a trial. Record each accommodation and each problem with a trial in the site's session records.

### Take over during a session

The **Operator** control is on every participant screen. Press and hold it for 2 seconds, and enter the PIN when the configuration has one. The session then pauses, and the app shows three actions:

- **Return to the participant** goes back to the same screen.
- **End the session** ends the open trial or questionnaire block, and opens Review.
- **Leave the session open and go to Home** keeps the session open. Home lists it as open, with **Return**.

When you end the session, the open trial ends with the reason `operatorAbort`, and the open block ends as `declined`. A safety item that the block did not reach becomes a flag.

## Run a self-administered session

No operator is present during a self-administered session. The enrolment record of the study gives the consent attestation, with the role `enrolment`.

1. Prepare the iPad with the study configuration, Guided Access, and a Focus.
2. On Home, tap **New session**. In Setup, select **Self-administered** under **Administration**.
3. Complete Setup as for a supervised session. Enter the enrolment record code in place of the operator code.
4. Tap **Start the session**.
5. Give the participant the iPad and the instructions that the protocol names.
6. If the participant takes part remotely, confirm that the participant is in a private place.
7. If the configuration has a `remoteReview` block, export the session within `exportEveryHours`.
8. Make sure that the reviewer checks each export within the review times.

The participant taps **Start**, does the tasks, and answers the questionnaire blocks. If a block of the session has a safety item, the participant first reads the monitoring disclosure. The participant sees the crisis list at the end of each session with a questionnaire block, and then the thank-you screen. On the thank-you screen, the participant can tap **Save to Files** to export the session.

The app never shows an operator alert in a self-administered session, and it never tells the participant that a response was flagged. If a block has a safety item, the app shows the crisis list at once after each flag. This includes a flag for an item that the block did not reach.

For remote participation by minors, published studies used extra identity checks. One online cohort of ages 12 to 25 used a linked parent survey, date-of-birth checks, and attention checks ([online youth cohort](https://pmc.ncbi.nlm.nih.gov/articles/PMC9717385)). Decide the checks in the protocol.

### Resume a self-administered session

When the app stops during a self-administered session, the next launch opens the session again for its participant. The app asks "Type your participant code to continue." The session resumes only when the participant types the code of the session. A wrong code shows "This code is not the code of this session." and the number of tries that are left. After 5 wrong codes, the session closes and does not resume. The app shows the support list first when the session had a questionnaire. Home then lists the session as closed, with **Export**. Export it at once, because it can hold a flag. The app keeps the count of wrong codes when it relaunches. A correct code sets the count back to zero.

A session can resume only inside its resume window, `selfAdministered.resumeWithinHours` after the session opened. After the window, the next launch closes the session without a crisis list, because no participant is present. Home then lists it as closed, with **Export**. Export it at once, because it can hold a flag.

After a resume, the app shows the crisis list first when the interrupted block had a flag.

### End a self-administered session early

An operator can end a self-administered session with the **Operator** control. If the end flags a safety item that the block did not reach, the crisis list shows first. Give the iPad to the participant, who taps **Continue**. Review then shows each flag with "Reviewed by the study reviewer after export". It asks for no action code.

1. Tap **Show the support list to the participant**, and give the iPad to the participant.
2. When the participant taps **Continue**, the app closes the session.
3. Export the session, so that the reviewer gets the flags.

## Handle a safety alert

A safety alert is a neutral notice in Review. It reads "A safety item needs follow-up under your protocol", with the instrument and the item number. It never shows the response. Review shows one alert for each flag. In a supervised session, each alert needs an action code.

1. If the participant is in immediate danger, do the emergency step of the safety protocol first.
2. Do not discuss the alert in front of other people.
3. Call the responder that the safety protocol names.
4. Let the responder speak with the participant in private.
5. For the first open alert, select an action in **Action**, and tap **Record action**.
6. Repeat step 5 for each alert. The app records each action for the oldest open flag.
7. Record the name of the responder, the time, and the outcome in the site's safety record.
8. Tap **Show the support list to the participant**.

The journal holds only the action code and the role of the person who recorded it. The site's safety record holds the details.

In a self-administered session with remote review, the reviewer finds the flags in each session of the study store:

```python
from pathlib import Path

import scriptkin as sk
from scriptkin import labels

key = Path("<key folder>/study-export-key.bin").read_bytes()
recording = sk.read_journal("<study store>/sessions/<session identifier>", private_key=key)
print(labels.safety_flag_count(recording))
```

The count includes endorsed, skipped, and unreached safety items. When the count is more than zero, the reviewer follows the safety protocol within the review time. The site holds the participant's contact details. The flag count is for safety review and audit only. It is never a research label.

`read_journal` refuses a damaged journal. For a damaged package, search the decrypted `journal.jsonl` in its `damaged/` folder of the study store for `safetyFlagged` lines.

## Handle an interruption

- **Crash in a supervised session.** On the next launch, Home lists the session as interrupted. Tap **Resume**. The app ends the open trial as interrupted and gives the task again, up to the number of attempts in the configuration. It gives an interrupted questionnaire block once more.
- **Crash in a self-administered session.** See [Resume a self-administered session](#resume-a-self-administered-session).
- **App in the background.** The open trial ends with the reason `backgrounded`, and every line reaches storage. The app gives the task again, up to the number of attempts. Guided Access prevents most of these events.
- **Pencil problem.** Note the trial in the site's session records. The app has no mark for it.
- **Participant stops.** Respect the decision. [Take over](#take-over-during-a-session), and tap **End the session**.
- **Damaged journal.** Home lists a journal with damage before its last line as damaged. Tap **Export damaged journal**. Transfer the package and take it into the study store as an export. Tell the sponsor. Do not delete it unless the protocol or a withdrawal requires it.
- **Write failure.** The app shows "The session stopped" and keeps every line saved before the failure. Tap **Go to Home**. Free storage, and relaunch the app. Home lists the session as interrupted.
- **Write failure in a self-administered session.** The participant sees "The session stopped. Please tell the study team." The screen also shows the support list when a questionnaire had started. Press and hold **Operator** to open Home. Export the session after the next launch, because it can hold a flag.
- **Configuration not on this iPad.** To resume or export a session, the iPad needs the configuration that the session used. Import that configuration, and try again.

## Export a session

1. On Home, find the session with the status **Closed**.
2. Tap **Export**. The app verifies the export, and only then deletes the source journal.
3. Read the export name and the encryption on the Export screen.
4. Record the participant code, the export name, the device asset code, the date, and your name in the [session register](DATA_GOVERNANCE.md#keep-the-site-session-register).
5. Tap **Done**.
6. Move the export folder through the approved transfer channel. The folder is in Files under On My iPad > SCRIPT Capture > Exports.
7. [Take the export into the study store](#take-exports-into-the-study-store).
8. After the result `accepted`, `duplicate`, or `damaged`, delete the export from the iPad and the transfer location.

Home masks each participant code. It shows at most the last 4 characters and hides at least 4. The PIN that opens Home also shows the full codes. A self-administered participant exports with **Save to Files** on the thank-you screen. Record that export in the register in the same way.

## Take exports into the study store

The study store is the site's folder of verified sessions. The `scriptkin intake` command checks each export folder, copies each session into the store once, and writes a receipt for each export. Use it at stage 4 of the [data flow](DATA_GOVERNANCE.md#data-flow).

1. Put the transferred export folders in one incoming folder, for example `incoming/`. Leave out every bench export, whose name starts with `SCRIPT_BENCH_`.
2. Run `scriptkin intake incoming --store <study store> --key <key folder>/study-export-key.bin --out intake-report.json`.
3. Read the result of each export in `intake-report.json`.
4. Add the `sessionID` of each receipt to the row of its export name in the session register.
5. When the result is `damaged`, give the package to the reviewer, and tell the sponsor.
6. When the result is `quarantined`, read `reason.json` in the quarantine folder. Keep the device copy, and tell the sponsor.
7. When the result is `conflict`, keep the device copy, and tell the sponsor.
8. When an export is in the `refused` list, find the study key of this study. Run the command again with that key.

| Result | Meaning | What the store holds |
| --- | --- | --- |
| `accepted` | The export passed every check. | `sessions/<session identifier>/` holds the export as received. |
| `duplicate` | The store already holds the same journal for this session. | Nothing new. |
| `conflict` | The store already holds a different journal for this session. | Every version is in `quarantine/`. `sessions/` holds none. |
| `quarantined` | The export failed a check. | `quarantine/<digest>/` holds the export and `reason.json`. |
| `damaged` | The export is the package of a damaged journal. Intake checked its manifest and decrypted it. | `damaged/<digest>/` holds the package as received and the decrypted `journal.jsonl`. |
| `refused` | The key is absent, or it does not fit the export. | Nothing. |

`<digest>` is the first 16 hexadecimal digits of the `exportSHA256` of the receipt. The `known` list names the folders that intake already took from the same place with the same bytes. Intake skips them, so you can run the command again on the same folders.

The command exits with 0 when each export is `accepted` or a `duplicate`. It exits with 1 when an export needs your attention, and with 2 when it cannot run.

The store keeps each encrypted export encrypted. Add `--keep-plaintext` only when the protocol permits a decrypted copy in the store. The decrypted `journal.jsonl` is then beside the encrypted file.

The receipt of a session is `receipts/<session identifier>.json`. It lists each intake of that session. Each entry gives the source folder, the SHA-256 digests of the export and the journal, and the encryption. It also gives the key identifier, the result, and the time. `intake.jsonl` lists every intake in order. Add your name and the date to the site's own intake record. Treat the whole store as personal data.

## Check the quality of the store

The `scriptkin qc-report` command counts what the store holds. It reads every session in `sessions/`.

1. Run `scriptkin qc-report <study store> --key <key folder>/study-export-key.bin --out qc-report.json --markdown qc-report.md`.
2. Read the two tables in `qc-report.md`.
3. Compare the sample rates of each iPad and Pencil with its qualification report.
4. Look at each refused trial, unresolved estimate, interrupted trial, and recovered session with the site staff.
5. File both report files with the study records.

The report gives these counts:

- Sessions by site, device asset code, configuration digest, app version, administration mode, and purpose. A bench session has no site, configuration, or administration mode, so those three counts leave it out.
- The measured trials of study sessions that quality control accepted or refused, with the refusal codes.
- For each iPad and declared Pencil: the sample rate (median, 5th percentile, and 95th percentile) and the latest qualification session in the store.
- For each channel: the sessions that planned it, recorded it, or planned it but did not record it.
- The share of unresolved estimates and unmatched corrections.
- The interrupted trials and questionnaire blocks, and the recovered sessions.
- The safety flags, operator actions, operator alerts, and crisis list displays. These counts are for audit only.

The report never holds a questionnaire score, a label, a feature value, or a participant code. Do not use the safety counts as research data.

## Withdraw a participant

Follow the [withdrawal and deletion procedure](DATA_GOVERNANCE.md#withdrawal-and-deletion-procedure). It uses the session register for the copies outside the iPad. The steps on each iPad are:

1. Finish or end any open session.
2. On Home, tap **Withdraw a participant**.
3. Enter the participant code in both fields.
4. Tap **Delete every recording of this code**, and then tap **Delete**.
5. Record the session identifiers that the app lists under **Deleted**.
6. Tap **Verify a withdrawal**, enter the code, and tap **Check the deletion log**.
7. Check that the app reports a withdrawal and no recording of this code. If a recording is still on the iPad, do steps 2 to 6 again.

## Session checklists

### Before each session

- [ ] The iPad and the Pencil are charged.
- [ ] The free storage is more than 500 MB, and enough for the session and its export.
- [ ] Home shows the study configuration digest that the site recorded.
- [ ] The crisis list for the session's jurisdiction was checked within the interval that the protocol sets.
- [ ] Guided Access and the Focus are on.
- [ ] The device qualification for this iPad, Pencil, iPadOS, and app version is on file.
- [ ] For a supervised session, the responder is available.
- [ ] The consent, assent, or guardian permission is on file for the participant code.
- [ ] The room is private and quiet.

### During each session

- [ ] The participant code and the Pencil in Setup are correct.
- [ ] Each accommodation and each trial problem is in the site's session records.
- [ ] Each safety alert has an action code and an entry in the site's safety record.
- [ ] The participant saw the crisis list at the end.

### After each session

- [ ] Each trial in Review has a plausible duration, sample rate, and channel set.
- [ ] The session is closed and exported.
- [ ] The export is in the session register.
- [ ] The export is transferred and taken into the study store, and its receipt is checked.
- [ ] The device copy is deleted after intake.
- [ ] Each damaged journal is exported, taken into the store, and reported to the sponsor.
