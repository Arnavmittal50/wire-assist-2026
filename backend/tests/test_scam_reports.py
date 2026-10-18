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
    assert len(reports.reports) == 23
    assert sum(report.is_synthetic for report in reports.reports) == 1
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


INVOICE_TRANSCRIPT = [
    'My roofing contractor emailed an amended invoice after the roof repair.',
    'The email replaced the original bank account with a new beneficiary for a wire.',
    'I have not called the contractor using the number in my signed contract.',
]


def test_added_invoice_scam_matches_member_transcript_without_changing_score():
    session = _session(INVOICE_TRANSCRIPT)
    before = session.attention.model_dump()
    matches = ScamReports(ROOT / 'data').match(session)
    assert matches[0].report_id == 'SYNTHETIC-INVOICE-001'
    assert matches[0].is_synthetic
    assert matches[0].similarity >= 0.3
    assert any(reason.startswith('Shared wording:') for reason in matches[0].reasons)
    assert session.attention.model_dump() == before


def test_added_invoice_scam_does_not_match_an_unrelated_conversation():
    session = _session(['I am paying college tuition.', 'The university portal shows the semester balance.',
                        'The bursar confirmed my student enrollment.'])
    assert not any(m.report_id == 'SYNTHETIC-INVOICE-001' for m in ScamReports(ROOT / 'data').match(session))


def test_teller_questions_and_unconfirmed_speakers_cannot_supply_matching_evidence():
    for role, finalized, status in [('teller', True, 'CONFIRMED'), ('unknown', True, 'UNCERTAIN'),
                                     ('member', False, 'CONFIRMED'), ('member', True, 'PENDING')]:
        session = _session(INVOICE_TRANSCRIPT)
        for segment in session.segments:
            segment.speaker, segment.finalized, segment.diarization_status = role, finalized, status
        assert ScamReports(ROOT / 'data').match(session) == []
