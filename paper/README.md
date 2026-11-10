# First SCRIPT paper

This folder holds the manuscript of the bench paper and the tools that fill it with results.
The target is Measurement Science and Technology with double-anonymous review.
`docs/PAPER_PLAN.md` gives the venue evidence, the state, and the owner checklist.

The manuscript contains no study result. The hardware runs are not done.
Red boxed notes mark every unfinished passage.

## Build

```sh
paper/build.sh                                        # draft without results
paper/build.sh --results bench/results                # draft with the results of the bench analysis
paper/build.sh --submission --results bench/results   # the submission check
```

The script copies the sources to the build folder of `./check` (`~/Library/Caches/SCRIPT/<checkout>-<hash>/paper`), because the repository can sit on an exFAT drive. It builds `main.pdf` and `supplement.pdf` with tectonic and prints the output folder.

The build stops at an undefined reference, an undefined citation, a repeated label, or a bibliography warning.
The submission build also stops at:

- an unfinished passage (`\pending`),
- a missing generated result,
- a dry run,
- an author in the document properties,
- a word of `anonymity_words.txt`. Add the names and the affiliation words of the authors to this file before submission. One word or phrase per line, no comments.

## Files

| File | Content |
| --- | --- |
| `main.tex`, `supplement.tex` | The two documents. Class `article`; IOP accepts any LaTeX format. |
| `macros.tex` | `\pending`, `\resultfile`, the dry-run mark, and the build switches. |
| `sections/` | One file per section. One sentence per line. |
| `figures/` | Data of the design figure (commanded paths and speeds). Not a measurement. |
| `references.bib` | The bibliography. Every entry was checked against the DOI registry or its publisher page. |
| `tools/make_results.py` | Writes every result table, figure, and number from the analysis outputs. Standard library only. |
| `tools/make_design.py` | Writes `figures/` from the move lists of the programs. |
| `tools/dry_run.py` | Writes a synthetic study to test the tools. Not data. |
| `anonymity_words.txt` | Words that must not appear in a submission. |

## Results

Do not type a result into a section file. `tools/make_results.py` writes these files into the build folder, and `\resultfile{name}` loads each:

| Generated file | Source files | Place |
| --- | --- | --- |
| `tab_completeness` | `run_status.csv`, `agreement.csv` | Results |
| `tab_sampling` | `sampling.csv`, `accuracy.csv` | Results |
| `tab_agreement`, `fig_agreement` | `agreement.csv` (main analysis) | Results |
| `tab_camera_control` | `bench_features.csv` (camera values), `run_status.csv` | Results |
| `tab_correction` | `correction.csv`, `agreement.csv` | Results |
| `tab_gate` | `gate.csv` | Results |
| `tab_consequence` | `consequence/table_primary.csv` | Results |
| `fig_consequence` | `consequence_grid/figure_bias_response.csv`, `figure_decision_curves.csv`, `consequence_cells.csv`; measured points from `consequence/` | Results |
| `tab_bland_altman`, `fig_bland_altman` | `bland_altman.csv` | Supplement |
| `tab_selection` | `consequence/consequence_inputs.csv` | Supplement |
| `tab_consequence_grid` | `consequence_grid/table_primary.csv`, bias of 1 L at nominal noise | Supplement |
| `numbers` | all of the above | Macros `\Res...` for the text |

Rules of the script:

- A missing value prints as "n/a". A missing input file gives no output file, and the draft then shows a red note.
- The main tables use the rows with `analysis` equal to `main`. The sensitivity analyses are in the same CSV files and need their own table when the results exist.
- The consequence table holds the primary rows only. The bias-bound and other sensitivity cells are not in it.
- The uncertainty budget of the camera reference is not generated. It needs the calibration records (`docs/PAPER_PLAN.md`, checklist step 10).

## Dry run

A dry run tests the tools with a synthetic study. It is not data.

```sh
UV_PROJECT_ENVIRONMENT=<venv of ./check> uv run --project python --frozen --extra camera \
    python paper/tools/dry_run.py --out <folder outside the repository> --consequence --grid
paper/build.sh --results <that folder>/study/bench/results
```

`--consequence` adds a smoke run of the measured consequence experiment. `--grid` adds a reduced grid with the real file format. Both take several minutes.
The result folders hold the file `DRY_RUN_SYNTHETIC`. Every table and figure from them carries a red mark, the script refuses to write them inside the repository, and the submission build refuses them.

## Design figure

`figures/*.dat` come from the move lists:

```sh
UV_PROJECT_ENVIRONMENT=<venv of ./check> uv run --project python --frozen \
    python -I python/studies/bench_programs.py --write <folder>
python3 paper/tools/make_design.py --programs <folder> --out paper/figures
```

Write them again when a program changes before the lock.

## Writing rules

- Short plain sentences, at most 25 words. One term for one concept. `docs/PAPER_PLAN.md` section 2 and the methods define the terms.
- Nothing internal in the manuscript: no file names, tool names, project names, earlier venues, or revision history. The AI-use disclosure is the only exception.
- No result that an output file does not hold. No number, device fact, or citation without a checked source.
- The manuscript is anonymous: no author, affiliation, funding, acknowledgement, or repository that names the authors.
- The ParkinsonHW example is not in the paper (`docs/PAPER_PLAN.md` section 2.4).

## Source checks

`python/tests/test_paper.py` checks input targets, citation keys, internal identifiers, and planning captions in the manuscript source.
`paper/build.sh` checks the compiled documents.
