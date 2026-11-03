# Instructions for agents

Read `docs/DESIGN.md` before you change code. The design is the contract.

## Check your work

- Run `./check` before you report a change as done. Run one section with `./check python`, `./check core`, `./check contract`, or `./check app`.
- Build output, the Python environment, and test exports live outside the repository, in `~/Library/Caches/SCRIPT/<checkout>-<hash>/`. `./check` prints nothing about it and creates it on demand. Set `SCRIPT_BUILD` to use another folder.
- The repository may sit on an exFAT drive. macOS then writes a `._` file beside each file. Every tool must skip names that start with `._`.
- Write each test to detect one named defect. State the defect in the first comment line of the test. Do not test source spelling.

## Change the contract

The recording format is shared by Swift and Python. To change it:

1. Change `contract/journal.schema.json` and `docs/DESIGN.md` section 3.
2. Change the generator in `python/src/scriptkin/contract.py`.
3. Run `scriptkin contract regenerate`. Never edit a generated file by hand.
4. Make the Swift fold and the Python fold agree on every fixture.

## Data governance

- No local human dataset may be read, extracted, or analysed. This includes EMOTHAW, Mergl, PaHaW, and every dataset in the earlier project's study materials. A written governance decision is necessary first.
- Public datasets with a CC BY licence may be used to test readers and features only. Do not train or evaluate a screening model on them.
- `screening()` accepts synthetic cohorts and cleared datasets only.
- The app must never show a result, score, or interpretation to the person who draws.

## Commits

- Commit only verified units. Do not add `Co-Authored-By` or any other attribution line.
- Keep build output, environments, and recordings out of Git.

## Writing

Write documentation, comments, and messages in short, plain sentences. Use one term for one concept. Keep comments for a reason that the code cannot show.
