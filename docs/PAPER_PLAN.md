# First paper: venue, quality bar, and plan

This plan starts on 3 November 2026. It replaces the earlier EMBC plan and bench protocol 4.8 in the older `Screening_Device` folder. It reuses their tested parts only where they still earn their place.

## 1. The venue

### 1.1 Choice

**Target: IEEE Journal of Biomedical and Health Informatics (JBHI).** It is the most prestigious venue where a paper without data from people has a realistic chance after the upgrades in section 2.

| Venue | Prestige | Chance for this paper | Decision |
| --- | --- | --- | --- |
| npj Digital Medicine | Highest | Very low: it expects clinical or human evidence, which this project cannot collect now | Rejected |
| ACM IMWUT | High | Low: its papers almost always include a study with people | Rejected |
| **IEEE JBHI** | High: impact factor 6.8 (2024), CiteScore 13.5 | Low today, reasonable after the upgrades | **Target** |
| IEEE TBME | High | Reasonable: strong fit for bench measurement | First fallback |
| Behavior Research Methods | Medium to high | Good: a robot-only bench study has a precedent there | Second fallback |
| IEEE EMBC 2027 | Medium | Good, but the 2027 reciprocal reviewer rule and the 4-page limit apply | Dropped |

The EMBC research reports stay in `/Volumes/T7 Shield/SCRIPT-records/embc/`.

### 1.2 What JBHI requires

| Item | Requirement | Source |
| --- | --- | --- |
| Deadline | None. JBHI reviews submissions all year. This plan sets an internal date (section 4). | |
| Format | IEEE double-column template. A regular paper is free up to 8 pages. Pages 9 and 10 cost USD 250 each. Page 11 and later cost USD 350 each. The maximum is 14 pages, supplementary material included. | [prepare and submit](https://embs.org/jbhi/prepare-and-submit-your-manuscript), [editorial policy](https://www.embs.org/jbhi/editorial-policy/) |
| Selectivity | Acceptance 14.4 percent for papers submitted in the last 12 months. First decision in about 27 days (the page omits the unit; an earlier version gave 33 days). | [statistics](https://www.embs.org/jbhi/statistics/) |
| Review duty | None. Authors may suggest four independent reviewers. | prepare and submit |
| Ethics | Studies with people need ethics approval with its reference number. Public datasets need their source and citation. | same |
| AI use | IEEE policy: disclose AI-generated text, figures, or code in the acknowledgments. | [IEEE policy](https://journals.ieeeauthorcenter.ieee.org/become-an-ieee-journal-author/publishing-ethics/guidelines-and-policies/submission-and-peer-review-policies/) |
| Presentation | None. | |

### 1.3 What the quality bar means for this paper

- An acceptance rate of 14.4 percent means that a measurement report alone is not sufficient. The paper needs a clear informatics contribution and a clear consequence for health models.
- No peer-reviewed study compares Apple Pencil and Wacom for the same movement. A scoping review of 120 digitised-spiral studies found no direct device comparison and no robot reference (Wang 2025).
- Every public labelled handwriting dataset for mental health or neurology was recorded on a Wacom tablet. Real deployments use iPads. Whether models and norms transfer between the two devices is an open translation question.
- No handwriting paper uses synthetic cohorts with known truth to measure what a device difference does to a screening model.

## 2. The paper

### 2.1 Contributions

1. **Measurement agreement.** A robotic bench study of Apple Pencil Pro on an iPad Pro against a Wacom tablet, for the same programmed drawing movements, with a 240 frames-per-second camera as an independent reference.
2. **Cross-device correction.** A per-feature mapping from Wacom values to Pencil values, learned on bench data and checked on mountings that the fit did not see.
3. **Consequence for screening models.** Synthetic cohorts with the measured device bias and noise show how much a device difference changes apparent model performance, and how much of that change the correction removes. One real-data example repeats the check on a participant-wise model of the public ParkinsonHW data (section 2.4).
4. **Open release.** The bench data, the capture and analysis code, the motion programs, and the fixture files.

The SCRIPT platform appears only in the methods. The paper makes no mental health claim.

### 2.2 Claim

For each prespecified movement feature, the SCRIPT measurement chain on an iPad Pro with Apple Pencil Pro and on a Wacom tablet gives equivalent values for the same executed movement, within prespecified limits, or it does not. Where it does not, a correction learned on the bench reduces the difference to a stated size on unseen mountings. A difference of the measured size changes a screening model's apparent performance by a stated amount when devices differ between groups. The claim covers only the tested units, software versions, surfaces, mountings, and speeds.

Working title: *Do Apple Pencil and Wacom measure the same drawing movement? A robotic bench study, a cross-device correction, and the consequence for screening models.*

### 2.3 Design in brief

- **Devices.** P: iPad Pro (M4 or M5) with Apple Pencil Pro, SCRIPT Capture in bench mode. W: a Wacom tablet on a Mac, with a SCRIPT Bench capture that writes the same journal format.
- **Motion.** A motion system (a pen plotter or a 3D printer) holds the stylus at a fixed angle with a constant load. The choice changes the motion model in the analysis. The camera records the executed movement.
- **Programs.** The SCRIPT battery loops and spiral at two sizes and two speeds, one fast loops program, and three line programs with a designed 5 to 11 Hz oscillation (12 programs), plus three fault programs for the quality gate.
- **Replication.** Six mountings per device and five runs per program per mounting: 720 main runs and 108 fault runs.
- **Measures.** Sampling interval, jitter, and noise floor; spatial accuracy against the camera and the commanded path; and agreement of a fixed feature set on the motion interval (duration, path length, mean speed, loop height, speed variability, tremor-band power on the designed oscillations). Four of these are referenced to the camera track of each run. Normalised jerk is descriptive.
- **Statistics.** For each feature: the device difference per program with a confidence interval that includes mounting effects, a two-one-sided-tests (TOST) equivalence decision against prespecified limits on every program of the feature's set, Holm-adjusted across features, and Bland-Altman limits with repeated measures. Per-program results are descriptive. The limits and the analysis are fixed and hashed before the main runs.
- **Correction.** Per-feature Deming regression from W to P with speed and scale as covariates, checked for lack of fit on held-out mountings. The residual difference is tested against the same limits.
- **Consequence experiment.** Synthetic cohorts with the measured per-feature device bias and noise, in three scenarios: one device for everyone, training on W and testing on P, and device confounded with case status. Report AUC, calibration, and decision curves under record-wise, participant-wise, and device-held-out validation, without and with the correction.

### 2.4 Ethics

The project cannot get ethics approval. The paper therefore uses no new data from people.

- The bench study has no participants.
- The real-data example is a secondary analysis of the public, anonymised ParkinsonHW release (CC BY 4.0). The paper cites the dataset and the original study, and states that no new data were collected.
- The example uses only features that need no millimetre scale, because the public files have no known coordinate scale. It counts each duplicated trial once. It makes no clinical claim.
- The paper must stand without the example. If an editor or a reviewer asks for ethics approval for it, remove the example. Do not seek approval.

### 2.5 Page budget (8 free pages)

| Part | Pages |
| --- | --- |
| Title, abstract, introduction | 1.0 |
| Related work | 0.5 |
| Methods: bench, devices, programs, features, statistics, correction, synthetic experiment | 2.5 |
| Results: sampling and accuracy, feature agreement (table and figure), correction, quality gate, consequence experiment (figure) | 2.3 |
| Discussion, limits, statements (ethics, AI use, data) | 0.9 |
| References | 0.8 |

## 3. Upgrades from the EMBC version

| EMBC version | JBHI version | Reason |
| --- | --- | --- |
| 4 pages, at most 20 references | 8 pages, no reference limit | Room for all four contributions |
| 3 mountings per device | 6 mountings per device | A stable estimate of the mounting effect |
| No correction | Cross-device correction with held-out mountings | The informatics contribution |
| Consequence experiment as a short add-on | Full experiment, with and without the correction | The consequence for health models |
| No release | Open data, code, programs, and fixture files | Reproducibility |

## 4. Schedule

| Date | Work | Gate |
| --- | --- | --- |
| 3 to 9 Nov | This plan; bench protocol 1.0; independent review of the protocol; contract change for bench sessions | Protocol reviewed |
| from 5 Nov | Owner runs the iPad device tests (`docs/DEVICE_TESTING.md`) | Real Pencil journals |
| 10 Nov to 4 Dec | Build: bench mode, Mac Wacom capture, motion programs, camera tracker, bench statistics, correction, consequence experiment, paper skeleton | `./check` passes |
| by 4 Dec | Owner assembles the bench hardware | Hardware ready |
| 5 to 13 Dec | Calibration, camera validation, pilot, lock of the protocol and analysis digests | Lock file |
| 14 Dec to 10 Jan | Main runs and fault runs | Data complete |
| 11 to 31 Jan | Analysis, correction, consequence experiment with measured values, full draft | Draft |
| 1 to 20 Feb | Independent multi-model review, revisions, release preparation | Review closed |
| by 28 Feb 2027 | The owner submits to JBHI | |
| about 4 weeks later | First decision. After a rejection, revise and submit to IEEE TBME within two weeks. | |

If the bench hardware is not ready by 13 December, move every later date by the same delay. Do not cut the replication or the camera validation to keep a date.

## 5. What the owner must settle

1. The bench hardware: a motion system, a Wacom tablet with a Mac that runs its driver, and the 240 fps camera.
2. A second iPad or Wacom unit, to measure unit-to-unit variation (optional).
3. The licence for the released data and code.
4. Authorship and the AI-use disclosure text.

Settled on 3 November 2026: the Wacom tablet is an Intuos Pro Medium (2025) with Pro Pen 3, and the paper may use the ParkinsonHW labels for the one example in section 2.4.
