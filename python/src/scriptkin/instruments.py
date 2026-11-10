"""Reference questionnaires, written to contract/instruments.json.

The app shows the exact text here and records item identifiers and response values. It never
computes or shows a score. Scoring rules are for the library, which turns responses into labels.
Licence sources are in docs/DESIGN.md section 15.1.

The PHQ-9 and GAD-7 texts follow the English forms of the PHQ Screeners site word for word. The GAD-7
form has seven items and no difficulty question. The PHQ-8 is items 1 to 8 of the PHQ-9 with its
difficulty question, as the instruction manual defines it.
"""

from __future__ import annotations

import json
from typing import Any

PHQ_SOURCE = "https://www.phqscreeners.com/images/sites/g/files/g10016261/f/201412/instructions.pdf"
DASS_SOURCE = "https://www2.psy.unsw.edu.au/dass/"

PHQ_STEM = "Over the last 2 weeks, how often have you been bothered by any of the following problems?"
GAD_STEM = "Over the last 2 weeks, how often have you been bothered by the following problems?"
FREQUENCY = [
    {"value": 0, "label": "Not at all"},
    {"value": 1, "label": "Several days"},
    {"value": 2, "label": "More than half the days"},
    {"value": 3, "label": "Nearly every day"},
]
DIFFICULTY = {
    "id": "difficulty",
    "text": "If you checked off any problems, how difficult have these problems made it for you to do "
    "your work, take care of things at home, or get along with other people?",
    "options": [
        {"value": 0, "label": "Not difficult at all"},
        {"value": 1, "label": "Somewhat difficult"},
        {"value": 2, "label": "Very difficult"},
        {"value": 3, "label": "Extremely difficult"},
    ],
    "scored": False,
    "safety": False,
}

PHQ_ITEMS = [
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself — or that you are a failure or have let yourself or your family down",
    "Trouble concentrating on things, such as reading the newspaper or watching television",
    "Moving or speaking so slowly that other people could have noticed? Or the opposite — being so "
    "fidgety or restless that you have been moving around a lot more than usual",
    "Thoughts that you would be better off dead or of hurting yourself in some way",
]
GAD_ITEMS = [
    "Feeling nervous, anxious or on edge",
    "Not being able to stop or control worrying",
    "Worrying too much about different things",
    "Trouble relaxing",
    "Being so restless that it is hard to sit still",
    "Becoming easily annoyed or irritable",
    "Feeling afraid as if something awful might happen",
]
DASS_ITEMS = [
    ("s", "I found it hard to wind down"),
    ("a", "I was aware of dryness of my mouth"),
    ("d", "I couldn't seem to experience any positive feeling at all"),
    (
        "a",
        "I experienced breathing difficulty (eg, excessively rapid breathing, breathlessness in the "
        "absence of physical exertion)",
    ),
    ("d", "I found it difficult to work up the initiative to do things"),
    ("s", "I tended to over-react to situations"),
    ("a", "I experienced trembling (eg, in the hands)"),
    ("s", "I felt that I was using a lot of nervous energy"),
    ("a", "I was worried about situations in which I might panic and make a fool of myself"),
    ("d", "I felt that I had nothing to look forward to"),
    ("s", "I found myself getting agitated"),
    ("s", "I found it difficult to relax"),
    ("d", "I felt down-hearted and blue"),
    ("s", "I was intolerant of anything that kept me from getting on with what I was doing"),
    ("a", "I felt I was close to panic"),
    ("d", "I was unable to become enthusiastic about anything"),
    ("d", "I felt I wasn't worth much as a person"),
    ("s", "I felt that I was rather touchy"),
    (
        "a",
        "I was aware of the action of my heart in the absence of physical exertion (eg, sense of heart "
        "rate increase, heart missing a beat)",
    ),
    ("a", "I felt scared without any good reason"),
    ("d", "I felt that life was meaningless"),
]
DASS_OPTIONS = [
    {"value": 0, "label": "Did not apply to me at all"},
    {"value": 1, "label": "Applied to me to some degree, or some of the time"},
    {"value": 2, "label": "Applied to me to a considerable degree, or a good part of time"},
    {"value": 3, "label": "Applied to me very much, or most of the time"},
]


def _items(
    prefix: str, texts: list[str], options: list[dict[str, Any]], safety: set[int]
) -> list[dict[str, Any]]:
    return [
        {"id": f"{prefix}-{i}", "text": text, "options": options, "scored": True, "safety": i in safety}
        for i, text in enumerate(texts, start=1)
    ]


def _phq(instrument_id: str, count: int, title: str, ages: dict[str, Any]) -> dict[str, Any]:
    items = _items(instrument_id, PHQ_ITEMS[:count], FREQUENCY, {9} if count == 9 else set())
    return {
        "id": instrument_id,
        "version": "1.0",
        "title": title,
        "licence": {"status": "publicDomain", "source": PHQ_SOURCE},
        "textStatus": "verified",
        "ages": ages,
        "locale": "en",
        "stem": PHQ_STEM,
        "items": [*items, DIFFICULTY],
        "scoring": {
            "kind": "sum",
            "items": [item["id"] for item in items],
            "range": [0, 3 * count],
            "maxMissing": 0,
        },
    }


def instruments() -> list[dict[str, Any]]:
    gad_items = _items("gad7", GAD_ITEMS, FREQUENCY, set())
    dass_items = [
        {
            "id": f"dass21-{i}",
            "text": text,
            "options": DASS_OPTIONS,
            "scored": True,
            "safety": False,
            "scale": scale,
        }
        for i, (scale, text) in enumerate(DASS_ITEMS, start=1)
    ]
    return [
        _phq(
            "phq9",
            9,
            "Patient Health Questionnaire-9",
            {"min": 13, "max": None, "note": "Adults (Kroenke 2001); ages 13-17 (Richardson 2010)."},
        ),
        _phq(
            "phq8",
            8,
            "Patient Health Questionnaire-8",
            {
                "min": 13,
                "max": None,
                "note": "Adult validation (Kroenke 2009). Items 1-8 of the PHQ-9, which is validated at "
                "ages 13-17 (Richardson 2010); a separate adolescent validation of the PHQ-8 was not found.",
            },
        ),
        {
            "id": "gad7",
            "version": "1.0",
            "title": "Generalized Anxiety Disorder-7",
            "licence": {"status": "publicDomain", "source": PHQ_SOURCE},
            "textStatus": "verified",
            "ages": {"min": 12, "max": None, "note": "Adults; ages 12-17 exploratory; ages 15-24 community."},
            "locale": "en",
            "stem": GAD_STEM,
            "items": gad_items,
            "scoring": {
                "kind": "sum",
                "items": [item["id"] for item in gad_items],
                "range": [0, 21],
                "maxMissing": 0,
            },
        },
        {
            "id": "dass21",
            "version": "1.0",
            "title": "Depression Anxiety Stress Scales-21",
            "licence": {
                "status": "publicDomain",
                "source": DASS_SOURCE,
                "condition": "Only for a defined group such as enrolled research participants, with results "
                "going to the researcher and never to the respondent. Not on a website or app open to the "
                "public. The scales must not be modified.",
            },
            "textStatus": "verified",
            "ages": {"min": 14, "max": None, "note": "Developers: suitable down to 14 years."},
            "locale": "en",
            "stem": "Please read each statement and circle a number 0, 1, 2 or 3 which indicates how much "
            "the statement applied to you over the past week. There are no right or wrong answers. Do not "
            "spend too much time on any statement.",
            "items": dass_items,
            "scoring": {
                "kind": "subscales",
                "multiplier": 2,
                "maxMissingPerScale": 1,
                "missing": "proRate",
                "scales": {s: [i["id"] for i in dass_items if i["scale"] == s] for s in ("d", "a", "s")},
            },
        },
    ]


def instruments_bytes() -> bytes:
    return (json.dumps({"instruments": instruments()}, indent=1, ensure_ascii=False) + "\n").encode()


def instrument(instrument_id: str) -> dict[str, Any]:
    for item in instruments():
        if item["id"] == instrument_id:
            return item
    raise KeyError(instrument_id)
