import json
from pathlib import Path

import pytest

from scanner.location import classify, timezone_fit
from scanner.scoring import ATS_SOURCES

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("field,desc,tier", [
    ("Remote - Worldwide", "", "confirmed"),
    ("Remote (APAC)", "", "confirmed"),
    ("Remote, United States", "", "excluded"),
    ("Home based - EMEA", "", "excluded"),
    ("Hybrid - Bangkok", "", "excluded"),
    ("Remote", "You must reside in the United States.", "excluded"),
    ("Remote", "We hire globally and pay in USD.", "confirmed"),
    ("Remote", "This is an independent contractor role paid via Deel.", "likely"),
    ("Remote", "Great team, great product.", "unknown"),
    # US work authorization alone is not a location restriction (the candidate holds it).
    ("Remote", "Candidates must be authorized to work in the United States.", "unknown"),
])
def test_tiers(field, desc, tier):
    assert classify(field, desc)[0] == tier


def test_ats_job_without_remote_signal_is_office_based():
    assert classify("Bangkok, Thailand", "", remote_implied=False)[0] == "excluded"
    assert classify("Remote - Thailand", "", remote_implied=False)[0] == "confirmed"


def test_himalayas_thailand_flag_confirms():
    assert classify("Worldwide", "", {"thailand_eligible": True})[0] == "confirmed"


def test_timezone_fit():
    assert timezone_fit("Must overlap with US Eastern hours") == "us_overlap"
    assert timezone_fit("Team works in CET") == "good"


def test_precision_on_hand_labeled_sample():
    """PRD M2: >=90% precision for Confirmed and Likely on a blind hand-labeled sample."""
    labels_path = FIXTURES / "location_labels.json"
    if not labels_path.exists():
        pytest.skip("no labels yet")
    labels = {x["id"]: x["label"] for x in json.loads(labels_path.read_text())}
    inputs = json.loads((FIXTURES / "location_inputs.json").read_text())
    predicted_positive = correct = 0
    for job in inputs:
        remote_implied = any(s not in ATS_SOURCES for s in job["sources"])
        tier, _ = classify(job["location_raw"], job["description"], job["extra"], remote_implied)
        if tier in ("confirmed", "likely"):
            predicted_positive += 1
            correct += labels[job["id"]] in ("confirmed", "likely")
    assert predicted_positive and correct / predicted_positive >= 0.9, f"{correct}/{predicted_positive}"


@pytest.mark.parametrize("desc,tier", [
    ("Must be based in Latin America.", "excluded"),
    ("We are hiring anywhere in Poland.", "excluded"),
    ("Headquarters: Remote - USA", "excluded"),
    ("Language proficiency: Ukrainian. We work with EU clients.", "unknown"),
    ("THIS ROLE IS FOR OVERSEAS APPLICANTS, NON-U.S. ONLY.", "unknown"),
])
def test_description_region_restrictions(desc, tier):
    assert classify("Remote", desc)[0] == tier


def test_worldwide_tag_overridden_by_description():
    assert classify("Anywhere in the World", "Location - Remote Poland")[0] == "excluded"
    assert classify("Anywhere in the World", "Great team.")[0] == "confirmed"
