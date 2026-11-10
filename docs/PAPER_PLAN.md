# First paper: venue, quality bar, and plan

This plan starts on 3 November 2026. It replaces the earlier EMBC plan and bench protocol 4.8 in the older `Screening_Device` folder. It reuses their tested parts only where they still earn their place.

Revision of 9 November 2026: the venue changes from IEEE JBHI to Measurement Science and Technology, because the owner submits only to venues with double-anonymous review (section 1). The manuscript draft, its results script, and its build script are in `paper/` (section 6). Section 7 is the owner checklist for the hardware phase. Section 8 lists the open points that need a decision before the lock.

## 1. The venue

### 1.1 The rule and the choice

The owner's rule has four parts:

1. Double-anonymous review only (set on 8 November 2026). Verify the review model on the journal's own pages.
2. The most prestigious venue with a realistic chance.
3. No venue with a reviewing duty for authors.
4. A conference only when its deadline is within 3 to 4 months.

**Target: Measurement Science and Technology (MST), IOP Publishing.** First fallback: Physiological Measurement. Second fallback: Medical Engineering & Physics. All three are IOP journals with the same review option and the same anonymity rules, so one manuscript format serves all three.

### 1.2 Evidence for the review model

Every line below was read on the journal's own page on 9 November 2026. Each quote has fewer than 15 words.

| Venue | Review model | Quoted policy line | Page |
| --- | --- | --- | --- |
| IEEE JBHI | Single-anonymous. **Not eligible.** | "but the reviewers know the identities of the authors." | [prepare and submit](https://www.embs.org/jbhi/prepare-and-submit-your-manuscript/) |
| IEEE TBME | Single-anonymous. **Not eligible.** | "but the reviewers know the identities of the authors." | IEEE Information for Authors of the journal (read by the research agent, not opened a second time) |
| Behavior Research Methods | Not shown as double-anonymous. **Not eligible until shown.** The guidelines require a title page with the authors' names and state no masked review for this journal. | "The title page must include the authors' names and affiliations" | [submission guidelines](https://link.springer.com/journal/13428/submission-guidelines) |
| **Measurement Science and Technology** | Double-anonymous by author choice. **Eligible.** | "Identity transparency: single-anonymous, double-anonymous (author choice)" | [about the journal](https://publishingsupport.iopscience.iop.org/journals/measurement-science-and-technology/about-measurement-science-technology/) |
| Physiological Measurement | Double-anonymous by author choice. **Eligible.** | "Identity transparency: single-anonymous, double-anonymous (author choice)" | [about the journal](https://publishingsupport.iopscience.iop.org/journals/physiological-measurement/about-physiological-measurement/) |
| Medical Engineering & Physics | Double-anonymous by author choice. **Eligible.** | "Identity transparency: single-anonymous, double-anonymous (author choice)" | [about the journal](https://publishingsupport.iopscience.iop.org/journals/medical-engineering-physics/about-medical-engineering-physics/) |
| Scientific Reports | Single-anonymous. Not eligible. | "operates a single-anonymised peer review process for all content types" | [peer-review policies](https://www.nature.com/srep/journal-policies/peer-review) |

Other candidates that the research agent (GPT-6.1 Sol) read on their own pages:

- Single-anonymous, not eligible: IEEE Transactions on Human-Machine Systems, IEEE Sensors Journal, Journal of NeuroEngineering and Rehabilitation, Digital Biomarkers, the JMIR journals, Movement Disorders Clinical Practice, Royal Society Open Science, PLOS Digital Health.
- Double-anonymous by author choice, eligible, weaker fit: Journal of Neural Engineering, Biomedical Physics & Engineering Express (the practical reserve), Machine Learning: Health (open access only, with a fee).
- Not verified, because the page blocked automated reading: the Elsevier journals (Measurement, Journal of Neuroscience Methods, Human Movement Science, Computer Methods and Programs in Biomedicine, Biomedical Signal Processing and Control, Journal of Biomedical Informatics, Artificial Intelligence in Medicine), IEEE TIM, IEEE TNSRE, npj Digital Medicine, ACM IMWUT. Do not submit to one of these before a person reads its policy page. *Measurement* (Elsevier) is the one worth a manual check: its scope fits and its impact factor is higher than that of MST.
- Conference: ICDAR 2027 has double-blind review ("The review of conference papers will be double blind.") and a full-paper deadline of 5 March 2027, with no reviewing duty found in its call. It is the only conference inside the rule. CHIL 2027 and EMBC 2027 require a reviewing co-author and stay excluded. A journal remains the first choice, because the paper needs more than 17 pages of the LNCS format only if the supplement is counted, and a conference needs travel.

### 1.3 Why MST, and what it requires

| Item | Requirement or fact | Source |
| --- | --- | --- |
| Scope | "new measurement techniques and associated instrumentation". A paper with experiments "must represent an advance in measurement science or measurement technique". | about the journal |
| Prestige | Impact factor 3.7 (2025) on an IOP news page. Not read on the journal metrics page. Physiological Measurement: 2.7 (2024). | research agent |
| Review | Double-anonymous by author choice. Choose it at submission. | about the journal |
| Novelty statement | Fewer than 100 words on the novelty, significance, and relevance in a measurement context. Required at submission. | about the journal |
| Length | No stated limit for a paper. | about the journal |
| Charges | Subscription publication is free. Open access is optional (£2655). | about the journal |
| Review duty | None found in the journal pages or in the IOP ethical policy. No explicit statement of its absence. | research agent |
| Format | Any LaTeX format is accepted. The IOP class `iopjournal` with the option `anonymous` is optional and is not in the tectonic bundle. The draft uses the `article` class. | IOP LaTeX page |
| Anonymity | No author names, affiliations, or photographs in the manuscript or the supplement. No names in file names or document properties. No identifying acknowledgements or funding. Cite own work in the third person. Do not sign a response at revision. | [IOP checklist](https://publishingsupport.iopscience.iop.org/questions/checklist-for-anonymising-your-manuscript/) |
| AI use | IOP asks for a disclosure that names the tool, describes its use, and states how the authors checked the output. IOP forbids AI in referee reports and in replies to them. | [IOP generative AI page](https://publishingsupport.iopscience.iop.org/questions/generative-ai-tools/) (read through a search summary; read the page itself before submission) |
| Ethics | The paper has no participants. An ethics statement is not required. | |

The fit argument: the bar of MST is an advance in measurement technique. The paper therefore presents the **method** first (a camera-referenced bench with its own length standard and clock, replication over mountings, and equivalence limits fixed in advance) and the two devices as its application. The title and the abstract follow this order. Physiological Measurement would put the health measurement first; its structured abstract (Objective, Approach, Main results, Significance, at most 250 words) and its limit of 8000 words would need an edit.

### 1.4 What the quality bar means for this paper

- A comparison of two products is not sufficient for MST. The referee of section 6.3 asked for an uncertainty budget of the camera reference, a control of the executed movement for the two features without a camera reference, and a method-first structure. The draft has the structure and the text for the first two; their numbers need the hardware.
- No peer-reviewed study that drives an Apple Pencil and a Wacom pen with the same mechanical movement was found (searches of 3 and 9 November 2026). The paper says "we found no published study", not "no study exists".
- A scoping review of 120 digitised-spiral studies counts 41 with a Wacom tablet and 6 with an iPad (Wang 2025). It does not state that direct device comparisons are absent. Do not cite it for that.
- A published case shows the risk that the paper addresses: a dysgraphia study recorded the two diagnostic groups on two tablet models (Asselborn 2018, the comment of Deschamps 2019, and the reply of Asselborn 2019).

## 2. The paper

### 2.1 Contributions

1. **A bench method for comparing pen digitisers.** Programmed movements, an independent camera reference, replication over mountings, and equivalence tests against limits fixed before the main runs.
2. **Equivalence limits tied to a consequence.** Three of the six limits are the device bias that alone gives a single-feature screening model an AUC of 0.55 under full confounding. Three are stated judgements.
3. **A correction between the devices with a lack-of-fit check.**
4. **The consequence for screening models.** Generated cohorts with the measured device bias and noise, in three scenarios, without and with the correction.
5. **Open release** of the bench data, the capture and analysis code, the motion programs, and the fixture files. The paper states it only when the release exists.

The SCRIPT platform appears only as "the capture application" and "the analysis software". The paper names no project. It makes no mental health claim.

### 2.2 Claim

For each prespecified movement feature, the measurement chain on one iPad Pro with an Apple Pencil Pro and the chain on one Wacom Intuos Pro give equivalent values for the same executed movement, within prespecified limits, or they do not. Where they do not, a correction learned on the bench reduces the difference to a stated size on held-out mountings. A difference of the measured size changes a screening model's apparent performance by a stated amount when devices differ between groups. The claim covers only the tested units, software versions, surfaces, mountings, and speeds.

Title: *A camera-referenced robotic bench method for comparing pen digitisers, applied to an Apple Pencil and a Wacom tablet.*

### 2.3 Design in brief

- **Devices.** P: iPad Pro (M4 or M5) with Apple Pencil Pro, SCRIPT Capture in bench mode. W: a Wacom Intuos Pro Medium (2025) with Pro Pen 3 on a Mac, with SCRIPT Bench. Both captures write journal format 2.0 (`docs/DESIGN.md` section 3).
- **Motion.** A pen plotter (NextDraw 8511) holds the stylus at a fixed angle with a constant load. The camera records the executed movement.
- **Programs.** The SCRIPT battery loops and spiral at two sizes and two speeds, one fast loops program, and three line programs with a designed 5 to 11 Hz oscillation (12 programs), plus three fault programs for the quality checks.
- **Replication.** Six mountings per device and five runs per program per mounting: 720 main runs and 108 fault runs.
- **Statistics, correction, consequence experiment.** As in [BENCH_PROTOCOL.md](BENCH_PROTOCOL.md) and [CONSEQUENCE_PROTOCOL.md](CONSEQUENCE_PROTOCOL.md).

### 2.4 Ethics and the ParkinsonHW example

The project cannot get ethics approval. The paper uses no new data from people. The bench study has no participants.

The draft does **not** contain the ParkinsonHW example. The reasons: the example is not run, `AGENTS.md` still limits public CC BY datasets to tests of readers and features, the consequence protocol keeps the example disabled until a written decision aligns that rule, and MST is a measurement journal in which a small disease classifier adds risk without adding to the measurement advance. The paper stands without it. If the owner wants it back, it goes into the supplement, and it is removed at the first request for ethics approval.

### 2.5 Length

MST states no page limit. The draft has about 13 000 words with captions, tables, and references, on 22 pages in a single column, and a supplement of 5 pages. Keep the supplement under 8 pages after the results arrive.

## 3. Upgrades from the EMBC version

| EMBC version | Current version | Reason |
| --- | --- | --- |
| 4 pages, at most 20 references | No page limit | Room for the method and its checks |
| 3 mountings per device | 6 mountings per device | A stable estimate of the mounting effect |
| No correction | Correction with held-out mountings | The second use of the bench data |
| Consequence experiment as a short add-on | Full experiment, with and without the correction | The consequence for health models |
| No release | Open data, code, programs, and fixture files | Reproducibility |

## 4. Schedule

| Date | Work | Gate | State on 9 November |
| --- | --- | --- | --- |
| 3 to 9 Nov | Plan; bench protocol; protocol review; contract change | Protocol reviewed | Done |
| 3 to 7 Nov | Build: bench mode, Mac capture, programs, tracker, statistics, correction, parity test, consequence experiment, paper skeleton | `./check` passes | Done (main `013cfcc`) |
| 9 Nov | Venue change; full draft without results; results script; build script; referee review | Draft builds | Done on branch `paper-draft` |
| by 4 Dec | Owner assembles the bench hardware | Hardware ready | Open |
| 5 to 13 Dec | Calibration, camera validation, pilot, grid run, lock | Lock file and its dated registration | Open |
| 14 Dec to 10 Jan | Main runs and fault runs | Data complete | Open |
| 11 to 31 Jan | Analysis, measured consequence run, results prose, uncertainty budget | Draft with results | Open |
| 1 to 20 Feb | Independent review, revisions, release with an anonymous review link | Review closed | Open |
| by 28 Feb 2027 | The owner submits to MST | | Open |

If the bench hardware is not ready by 13 December, move every later date by the same delay. Do not cut the replication or the camera validation to keep a date.

## 5. What the owner must settle

1. The bench hardware: the motion system, the camera, the length standard with its certificate, the crystal-timed LED, the force gauge, and the dial indicator.
2. A second iPad or Wacom unit. The referee names the single unit of each device as a main weakness. A second unit of each device would let the paper speak about more than two units.
3. The licence for the released data and code, and an anonymous review link for them.
4. Authorship and the AI-use disclosure text (`paper/sections/statements.tex` holds a draft).
5. A dated external registration of the lock (for example an OSF registration with a view-only link), so that the paper can cite it.
6. The open points of section 8.

Settled on 3 November 2026: the Wacom tablet is an Intuos Pro Medium (2025) with Pro Pen 3.

## 6. State of the manuscript (9 November 2026)

### 6.1 What exists

| Part | State |
| --- | --- |
| Title, abstract (five parts) | Written. The result sentences are a pending note. |
| Introduction, related work | Written. 57 cited references, each checked against the DOI registry or its publisher page. |
| Measurement problem and design, with Fig. 1 (bench and programs) | Written. The figure shows the design only. |
| Methods (devices, fixture, motion system, programs, replication, lock, camera, uncertainty of the camera reference, motion interval, measures, features, limits, statistics, correction, precision of the design, consequence experiment) | Written in the present tense as the frozen protocol. Hardware facts are pending notes. |
| Results | Structure, pending notes, and generated tables and figures. No result text. |
| Discussion | Threats to validity and use of the method are written. The interpretation is a pending note. |
| Statements | Ethics written. Data and code availability and the AI-use disclosure are drafts with pending notes. No acknowledgements, funding, or author data (double-anonymous review). |
| Supplement | Programs and fault criteria, camera procedures, precision tables, consequence settings, generated additional results. |

### 6.2 Results pipeline

`paper/tools/make_results.py` reads `bench/results/` and the two consequence folders and writes every result table and figure and a file of numbers. `paper/build.sh --results bench/results` runs it and builds both PDFs. A missing result file gives a red note in the draft. The pipeline was tested with a synthetic study from the analysis tests (`paper/tools/dry_run.py`), which is not data. Every table and figure from a dry run carries a red mark, a dry run cannot be written inside the repository, and the submission build refuses it.

### 6.3 Independent review

An Opus 5.5 referee read the draft as a referee of MST on 9 November 2026. Verdict: major revision. The fixes that needed no hardware are in the draft. The points that need hardware or a protocol decision are in sections 7 and 8.

### 6.4 What is blocked on hardware

Every result. Also: the device and software versions, the measured load, friction and stiffness, the choice and settings of the motion system, the camera facts and each validation result, the uncertainty budget of the camera reference, the lock record and its registration, the deviations, and the interpretation in the discussion. The draft has 29 pending notes in the main text; `paper/build.sh` counts them.

## 7. Owner checklist for the hardware phase

Run every command from the repository root in the `./check` environment. `RUN` below stands for:

```sh
UV_PROJECT_ENVIRONMENT="$HOME/Library/Caches/SCRIPT/<checkout>-<hash>/venv" uv run --project python --frozen --extra camera
```

`./check` prints nothing about this folder; it is `~/Library/Caches/SCRIPT/` plus the checkout name and a hash. `ROOT` is the repository root that holds `bench/`. The protocol sections in brackets define each step. Do the steps in this order.

**A. Before the hardware**

1. Decide the open points of section 8 and change the protocols before the lock.
2. Read the Measurement (Elsevier) policy page by hand if a second target is wanted.

**B. Assemble and qualify (protocol sections 2 to 4 and 7)**

3. Assemble the base plate, the clamps, the holder with its free slide, the camera mount, the LED clock, and the DC lighting [3.1, 3.2, 7.1].
4. Run the iPad and Mac device checks of `docs/DEVICE_TESTING.md` [2.1, 2.2].
5. Measure the load on both devices (100 g, equal within 1 g), the friction, the stiffness, and the nib deflection (below 0.02 mm) [3.2]. Record every value in `bench/registry.json`.
6. Write the programs and the controller files:
   ```sh
   RUN python -I python/studies/bench_programs.py --write bench/programs --orders bench/order
   RUN python -I python/studies/bench_controller.py bench/programs
   RUN scriptkin contract regenerate
   ./check
   ```
   Build both capture applications after this step, because they bundle the programs file [2.4, 14 step 4].
7. Calibrate the camera once: the lens (`RUN scriptkin bench lens`), the frame clock from a 60 s clip of the 1 Hz LED (`RUN scriptkin bench clock --out FILE --led LEFT TOP WIDTH HEIGHT CLIP`), and, for a rolling shutter, the row readout from a clip of the 1 kHz LED (`RUN scriptkin bench readout --out FILE --region LEFT TOP WIDTH HEIGHT CLIP`) [7.2, 7.3]. Each command has `--help`.
8. Validate the camera: clock, static, speed, background, and synthetic tests [7.5].
9. Qualify the motion system: steps 1 to 9 [4.2]. If the fast loops program fails step 7, write the programs again with `--exclude loops-l-v80`.
10. **For the paper:** write down, during steps 5 to 9, every value that the uncertainty budget needs: the certificate and the measured pitch of the calibration board, each plane residual, the marker height on each device, the camera height above the marker plane, the measured frame rate, the row readout time, and the still-marker noise.

**C. Pilot, grid run, and lock (protocol sections 6.5 and 14)**

11. Pilot with two mountings per device. At each mounting: the plane calibration (`RUN scriptkin bench plane`), the marker offset (`RUN scriptkin bench marker-offset --out FILE --plane PLANE STILL`), the axis check, and the qualification session [6.1, 6.2, 8.1].
12. Track every clip: `RUN scriptkin bench start-point ROOT RUN_ID`, then `RUN scriptkin bench track --calibration FILE --clock FILE (--readout FILE | --global-shutter) --start-mm X Y CLIP`.
13. `RUN scriptkin bench pilot ROOT`. All pilot checks must pass [6.5]. Write the pilot results into the protocol and the registry: the dwell expectation of device P, the band-power program set, and the decision on the fast loops program [14 step 2].
14. Run the three precision commands of protocol section 11.8 and copy the precision report to `bench/results/bench_precision.json` [14 step 3].
15. Run the grid of the consequence experiment and archive it **before** any measured result exists. It is long (232 200 units); run it at a time when the laptop does nothing else, with few workers:
    ```sh
    RUN python -I python/studies/consequence.py --mode grid --workers 2 --out bench/results/consequence_grid/
    ```
16. Lock: `RUN scriptkin bench lock --commit <full hash> --build P <build> <revision> --build W <build> <revision> --out bench/BENCH_LOCK.json ROOT`. Commit the lock file. Write the first two rows of `bench/DEVIATIONS.md` [14 steps 5 to 8].
17. Register the lock commit and the SHA-256 of the lock file with a dated external service. The paper cites this registration.

**D. Main runs (protocol section 6)**

18. Blocks 1 to 6 in the order W P P W P W. Each block: mounting steps, 69 runs in the order file, reruns at the end. Track each clip as in step 12.
19. `RUN scriptkin bench interim ROOT`. Add its row to `bench/DEVIATIONS.md`. Add mountings M7 and M8 if the rule says so [6.6].
20. Blocks 7 to 12 in the order W P W P P W (and 13 to 16 if added). Nobody compares feature values between the devices before the last block.

**E. From the bench output to the paper**

21. Analyse: `RUN scriptkin bench analyse ROOT`. It writes `bench/results/`.
22. Run the consequence experiment once with the measured inputs. The script prints the plan hash that `--expect-plan-sha256` needs:
    ```sh
    RUN python -I python/studies/consequence.py --mode measured \
        --deliverables bench/results/deliverables.json --expect-plan-sha256 <hash> \
        --out bench/results/consequence/
    ```
23. Fill the tables and figures with one command:
    ```sh
    paper/build.sh --results bench/results
    ```
    It prints the page count, the count of pending notes, and the output folder under `~/Library/Caches/SCRIPT/`.
24. Write the result prose, the uncertainty budget, the interpretation, and every pending note in `paper/sections/`. Type no number that a generated table holds; cite the table.
25. Get an independent review. Do the final prose pass with GPT-6.1 Sol.
26. Release the data and code with an anonymous review link. Complete the two statements.
27. Build for submission: `paper/build.sh --submission --results bench/results`. It stops at a pending note, a missing result, a dry run, an undefined reference, an author in the document properties, or a word of `paper/anonymity_words.txt`. Add the owner's name and affiliation words to that file first. Strip the comments from the LaTeX sources if the journal asks for the sources.
28. Write the novelty statement (fewer than 100 words) and choose double-anonymous review in the submission form.

## 8. Open points before the lock

The referee review found points that the paper cannot repair by wording. Each needs a decision and, where accepted, a protocol change before the lock.

| Point | Why it matters | Option |
| --- | --- | --- |
| No uncertainty budget of the camera reference in the protocol | A camera error that differs between the devices (marker height, contrast) enters the bias. The paper now describes the budget but has no numbers. | Add the budget to protocol section 7 and compute it in the analysis. Record the values of checklist step 10. |
| Speed CV and band power have no camera reference | The executed-movement check (0.2 mm) does not exclude a different oscillation size on the two surfaces. The pilot window of 0.5 to 2 times the command is wider than the band-power limit. | The results script already writes a camera control table for both features from `bench_features.csv`. Decide whether the protocol names it, and whether the camera resolves the 0.18 to 0.4 mm oscillations (test with rendered frames). |
| The correction needs the program speed and scale | A human recording has neither. The correction applies outside the bench only at the nearest program. | Keep it as a description of the device difference, or define the covariates from the recording. |
| The slope of the mean-speed correction may not be identified | After the covariates, the designed mean speeds differ little between programs. The precision study used an assumed spread. | Run `bench_precision.py` with the designed values. If the slope is not identified, fix it at 1 for that feature before the lock. |
| The consequence experiment is partly fixed by construction | All inputs scale with L, a shift cannot change a refitted AUC, and the confounding result follows from a closed form. | The paper now states this. Decide whether to shorten the experiment in the paper to the closed forms plus the calibration and net-benefit results. |
| Interim rule | The protocol computes the mounting variance on raw values and on camera-referenced differences and does not say which decides. The error rate with the rule was not simulated. | Name the deciding basis. Add the rule to the precision simulation. |
| One unit of each device | The paper can speak about two units only. | A second unit of each device. |
| Nib change at mountings 1 and 4 | Two clusters of three mountings. | State exchangeability (done in the paper) or add a sensitivity analysis by nib. |
| Pressure, tilt, latency | The introduction uses a pressure dispute as an example, and the bench does not test pressure. | A static force-to-pressure curve with calibrated weights is cheap. Decide before the lock. |
| The two protocols differ on "lower bound" | Bench protocol section 16 calls the consequence a lower bound for people. Consequence protocol section 11 forbids a numerical lower bound. | Align section 16. The paper follows the consequence protocol. |
| `python/tests/test_paper.py` | It still passes. It does not know the new generated-result macros. | Extend it when the branch merges. |
