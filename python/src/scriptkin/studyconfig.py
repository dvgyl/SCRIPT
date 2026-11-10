"""Study configurations: the demo configuration and the semantic rules every configuration must pass.

`contract/config.schema.json` checks the shape. `validate` checks the rules that a schema cannot express.
The app applies the same rules when it imports a configuration (docs/DESIGN.md section 14).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from scriptkin import battery, hpke, instruments

AGE_BANDS = ("13-17", "18-24")
ADMINISTRATIONS = ("supervised", "self")
CRISIS_LISTS_VERIFIED_ON = "2026-11-03"
# Hours after `openedAt` in which an interrupted self-administered session can resume, when the configuration
# has no `selfAdministered` member.
DEFAULT_RESUME_WITHIN_HOURS = 24


def _crisis(jurisdiction: str, entries: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "jurisdiction": jurisdiction,
        "locale": "en",
        "version": f"{jurisdiction}-{CRISIS_LISTS_VERIFIED_ON}",
        "verifiedOn": CRISIS_LISTS_VERIFIED_ON,
        "entries": entries,
    }


def demo_config() -> dict[str, Any]:
    tasks = [{"task": "practice", "practice": True, "attempts": 1, "hands": "dominant"}]
    tasks += [
        {"task": t, "practice": False, "attempts": 2, "hands": "dominant"}
        for t in ("spiral-trace", "loops", "pentagons", "house", "clock", "sentence")
    ]
    blocks = [
        {
            "administration": "supervised",
            "ageBand": band,
            "position": "afterTasks",
            "instruments": ["phq9", "gad7"],
        }
        for band in AGE_BANDS
    ]
    blocks += [
        {"administration": "self", "ageBand": band, "position": "afterTasks", "instruments": ["phq8", "gad7"]}
        for band in AGE_BANDS
    ]
    return {
        "format": "script.config",
        "version": "1.0",
        "demo": True,
        "study": {"id": "DEMO", "version": "1.0", "protocolVersion": "demo"},
        "sites": ["DEMO"],
        "jurisdictions": ["US", "GB", "IN", "AU"],
        "administration": list(ADMINISTRATIONS),
        "battery": {
            "id": battery.BATTERY_ID,
            "version": battery.BATTERY_VERSION,
            "sha256": battery.battery_sha256(),
        },
        "tasks": tasks,
        "questionnaires": blocks,
        "locales": {"en": {"instructionVersion": "1.0", "instructions": {}}},
        "safety": {
            "actionCodes": [
                "clinicianAssessment",
                "referred",
                "emergencyServices",
                "guardianContacted",
                "participantDeclinedHelp",
            ],
            "remoteReview": None,
            "crisisLists": [
                _crisis(
                    "US",
                    [
                        {
                            "name": "988 Suicide & Crisis Lifeline",
                            "contact": "Call or text 988",
                            "url": "https://988lifeline.org/",
                        },
                        {"name": "Emergency services", "contact": "Call 911"},
                    ],
                ),
                _crisis(
                    "GB",
                    [
                        {
                            "name": "Samaritans",
                            "contact": "Call 116 123",
                            "url": "https://www.samaritans.org/",
                        },
                        {"name": "Childline (under 19)", "contact": "Call 0800 1111"},
                        {
                            "name": "Shout",
                            "contact": "Text SHOUT to 85258",
                            "url": "https://giveusashout.org/",
                        },
                        {"name": "Emergency services", "contact": "Call 999"},
                    ],
                ),
                _crisis(
                    "IN",
                    [
                        {
                            "name": "Tele-MANAS",
                            "contact": "Call 14416 or 1-800-891-4416",
                            "url": "https://telemanas.mohfw.gov.in/",
                        },
                        {"name": "Emergency services", "contact": "Call 112"},
                    ],
                ),
                _crisis(
                    "AU",
                    [
                        {
                            "name": "Lifeline",
                            "contact": "Call 13 11 14 or text 0477 13 11 14",
                            "url": "https://www.lifeline.org.au/",
                        },
                        {
                            "name": "Kids Helpline (ages 5 to 25)",
                            "contact": "Call 1800 55 1800",
                            "url": "https://kidshelpline.com.au/",
                        },
                        {"name": "Emergency services", "contact": "Call 000"},
                    ],
                ),
            ],
        },
        "consentModel": {
            "13-17": {
                "model": "guardianAndAssent",
                "documents": [
                    {"id": "demo-guardian-permission", "version": "1"},
                    {"id": "demo-assent", "version": "1"},
                ],
            },
            "18-24": {"model": "adult", "documents": [{"id": "demo-consent", "version": "1"}]},
        },
        "ethics": {
            "approvalID": "DEMO-NOT-APPROVED",
            "protocolVersion": "demo",
            "safetyProtocolVersion": "demo",
        },
        "export": {"recipientPublicKey": None, "keyID": None},
        "operatorPin": None,
        "selfAdministered": {"resumeWithinHours": DEFAULT_RESUME_WITHIN_HOURS},
    }


def config_bytes(config: dict[str, Any]) -> bytes:
    return (json.dumps(config, indent=1, ensure_ascii=False) + "\n").encode()


def _key_id_problems(export: dict[str, Any]) -> list[str]:
    if export["recipientPublicKey"] is None or export["keyID"] is None:
        return []
    try:
        public = base64.b64decode(export["recipientPublicKey"], validate=True)
    except binascii.Error:
        return ["export.recipientPublicKey is not valid base64"]
    if hpke.key_id(public) != export["keyID"]:
        return ["export.keyID is not the SHA-256 of export.recipientPublicKey"]
    return []


def resume_within_hours(config: dict[str, Any]) -> float:
    """How long an interrupted self-administered session can resume, in hours after it opened."""
    return config.get("selfAdministered", {}).get("resumeWithinHours", DEFAULT_RESUME_WITHIN_HOURS)


def validate(config: dict[str, Any]) -> list[str]:
    """Rules a schema cannot express. An empty list means the configuration passes."""
    problems: list[str] = []
    known = {t["id"] for t in battery.battery()["tasks"]}
    # The purpose that owns each task that a study session must not use.
    reserved = dict.fromkeys(battery.QUALIFICATION_TASKS, "qualification") | dict.fromkeys(
        battery.BENCH_TASKS, "bench"
    )
    if config["battery"]["sha256"] != battery.battery_sha256():
        problems.append("battery.sha256 does not match the battery that this build ships")
    for task in config["tasks"]:
        if task["task"] not in known:
            problems.append(f"task {task['task']!r} is not in the battery")
        elif task["task"] in reserved:
            problems.append(
                f"task {task['task']!r} belongs to {reserved[task['task']]} sessions, not study sessions"
            )
    for block in config["questionnaires"]:
        band = block["ageBand"]
        low = int(band.split("-")[0])
        for instrument_id in block["instruments"]:
            try:
                spec = instruments.instrument(instrument_id)
            except KeyError:
                problems.append(f"instrument {instrument_id!r} is not in contract/instruments.json")
                continue
            if spec["textStatus"] != "verified":
                problems.append(f"instrument {instrument_id!r} text is not verified")
            if low < spec["ages"]["min"]:
                problems.append(f"instrument {instrument_id!r} is not validated for age band {band}")
            has_safety = any(item["safety"] for item in spec["items"])
            if has_safety and block["administration"] == "self":
                review = config["safety"]["remoteReview"]
                if review is None:
                    problems.append(
                        f"self-administered block uses safety item in {instrument_id!r} "
                        "without safety.remoteReview"
                    )
                else:
                    if review["imminentReviewWithinHours"] > 24:
                        problems.append("safety.remoteReview.imminentReviewWithinHours exceeds 24")
                    if review["routineReviewWithinWeekdays"] > 1:
                        problems.append("safety.remoteReview.routineReviewWithinWeekdays exceeds 1")
                    if review["exportEveryHours"] > review["imminentReviewWithinHours"]:
                        problems.append(
                            "safety.remoteReview.exportEveryHours is longer than the review interval"
                        )
                    if resume_within_hours(config) > review["imminentReviewWithinHours"]:
                        problems.append(
                            "selfAdministered.resumeWithinHours is longer than the review interval"
                        )
    lists = {c["jurisdiction"] for c in config["safety"]["crisisLists"]}
    if config["questionnaires"]:
        for jurisdiction in config["jurisdictions"]:
            if jurisdiction not in lists:
                problems.append(f"no crisis list for jurisdiction {jurisdiction}")
    for band in AGE_BANDS:
        if band not in config["consentModel"]:
            problems.append(f"consentModel has no entry for age band {band}")
    if not config["demo"]:
        if config["export"]["recipientPublicKey"] is None:
            problems.append("a study configuration needs export.recipientPublicKey")
        if config["operatorPin"] is None:
            problems.append("a study configuration needs operatorPin")
    pin = config["operatorPin"]
    if pin is not None:
        try:
            salt = base64.b64decode(pin["salt"], validate=True)
        except (ValueError, binascii.Error):
            problems.append("operatorPin.salt is not valid padded base64")
        else:
            if len(salt) < 16:
                problems.append("operatorPin.salt needs at least 16 bytes")
        if pin["iterations"] > 4294967295:
            problems.append("operatorPin.iterations exceeds 4294967295")
    problems += _key_id_problems(config["export"])
    return problems
