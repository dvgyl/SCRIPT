# SCRIPT

SCRIPT is a research platform that records drawing movement and tests whether it can support mental health screening.

- **SCRIPT Capture** is an iPad app. A participant draws six short tasks with Apple Pencil, and the app records every pen sample.
- **scriptkin** is a Python library. It reads the recordings, checks their quality, and calculates movement features.
- **The evaluation harness** in scriptkin tests screening models and measurement reliability without data leakage.

SCRIPT is not a screening or diagnostic tool. The app never shows a result to the person who draws.

## Requirements

- macOS with Xcode 27.2 or later, and the iOS simulator "iPad Pro 11-inch (M5)".
- [uv](https://docs.astral.sh/uv/) and Python 3.12 or later.
- For real recordings: an iPad Pro (M4 or M5) with Apple Pencil Pro. Other iPads with Apple Pencil also work and record fewer channels.

## Check everything

```sh
./check
```

This runs the contract check, the Python lint, type check, and tests, the Swift core tests, and the app build. To run one part, name it:

```sh
./check python
```

The sections are `contract`, `python`, `core`, and `app`. Build output goes to `~/Library/Caches/SCRIPT/`, outside the repository, because the repository can sit on an exFAT drive.

## Find your way

| Path | Contents |
| --- | --- |
| [docs/DESIGN.md](docs/DESIGN.md) | The design and the recording format |
| [contract/](contract/) | The journal schema, the task battery, and shared test fixtures |
| [app/](app/) | The iPad app and its `ScriptCore` Swift package |
| [python/](python/) | The scriptkin library and its tests |
