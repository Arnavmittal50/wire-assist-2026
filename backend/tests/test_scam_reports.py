from datetime import datetime, timezone

from app.core.config import ROOT
from app.schemas.contracts import Attention, Payment, Segment, Session, Signal
from app.services.scam_reports import ScamReports


def _session(texts, codes=(), rail="Wire"):
    signals = [Signal(signal_id=c, code=c, title=c, points=5, evidence=[], origin="conversation") for c in codes]
    return Session(member_id="M1", as_of=datetime(2026, 9, 27, tzinfo=timezone.utc), consent=True,
                   retain_transcript=False, mode="demo", payment=Payment(rail=rail), data_source="test",
                   segments=[Segment(segment_id=f"s{i}", sequence=i, text=t, speaker="member") for i, t in enumerate(texts)],
                   attention=Attention(signals=signals))


def test_real_file_loads_without_personal_fields():
    reports = ScamReports(ROOT / "data")
    assert len(reports.reports) == 22
    header = (ROOT / "data" / "scam_reports.csv").read_text().splitlines()[0]
    assert "phone" not in header and "email" not in header and "police" not in header


def test_waits_for_a_cue_or_enough_turns():
    assert ScamReports(ROOT / "data").match(_session(["Hello there."])) == []


def test_matches_when_a_cue_appears():
    matches = ScamReports(ROOT / "data").match(
        _session(["The bank called and said the FBI is investigating, don't tell anyone."],
                 codes=["AUTHORITY_PRESSURE", "SECRECY"]))
    assert matches and matches[0].scam_type == "Bank/Credit Card Company Imposter"
    assert len(matches) <= 3


def test_missing_file_is_empty(tmp_path):
    assert ScamReports(tmp_path).match(_session(["x"], codes=["URGENCY"])) == []
