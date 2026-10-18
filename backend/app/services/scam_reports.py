"""Known scam-report patterns shown to the teller as context, never as member evidence.

Matching is deterministic and explainable: scam-type cues from detected signal codes,
overlap between the member's own words and a report narrative, and payment method.
A match means "resembles a reported pattern", not that fraud occurred, and it never
changes the attention score.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from app.schemas.contracts import ScamMatch, Session

MIN_SCORE = 0.3
MAX_MATCHES = 3
MIN_MEMBER_TURNS = 3
STOPWORDS = frozenset(
    "a an and are as at be been but by for from had has have he her him his i if in into is it its me my "
    "not of on or our she so that the their them then they this to was we were what when which with you your "
    "said says told asked victim caller".split())
# Which scam types each detected conversation cue points toward.
TYPE_CUES = {
    "Bank/Credit Card Company Imposter": {"AUTHORITY_PRESSURE", "PROTECT_FUNDS", "SECRECY", "URGENCY", "PRESSURE"},
    "Romance": {"UNVERIFIED_RELATIONSHIP", "ROMANCE_INVESTMENT", "GUARANTEED_RETURNS", "SECRECY", "COACHING"},
    "Family/Friend Emergency": {"URGENCY", "SECRECY", "PRESSURE", "COACHING"},
}
RAIL_METHODS = {"Wire": ("wire", "western union", "moneygram", "xoom"), "ACH": ("ach", "bank transfer"),
                "External Transfer": ("transfer",), "P2P": ("zelle", "venmo", "cash app", "paypal"),
                "Cash": ("cash", "gift card")}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]{3,}", text.lower()) if t not in STOPWORDS}


@dataclass(frozen=True)
class ScamReport:
    report_id: str
    scam_type: str
    persona: str
    payment_methods: str
    dollars_lost: float | None
    narrative: str

    @property
    def tokens(self) -> set[str]:
        return _tokens(self.narrative)


class ScamReports:
    def __init__(self, data_dir: Path | str):
        path = Path(data_dir) / "scam_reports.csv"
        self.reports: list[ScamReport] = []
        if not path.exists():
            return
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                loss = row.get("dollars_lost", "").strip()
                self.reports.append(ScamReport(
                    report_id=row["report_id"], scam_type=row["scam_type"], persona=row.get("persona", ""),
                    payment_methods=row.get("payment_methods", ""),
                    dollars_lost=float(loss) if loss else None, narrative=row.get("narrative", "")))

    def match(self, session: Session) -> list[ScamMatch]:
        speech = [s for s in session.segments if s.finalized and s.speaker in {"member", "unknown"}]
        codes = {s.code for s in session.attention.signals if s.origin == "conversation" and s.status == "active"}
        # Check at a meaningful point: a conversation risk cue appeared, or enough has been said.
        if not self.reports or not (codes or len(speech) >= MIN_MEMBER_TURNS):
            return []
        spoken = _tokens(" ".join(s.text for s in speech))
        rail_words = RAIL_METHODS.get(session.payment.rail, ())
        scored = []
        for report in self.reports:
            reasons = []
            cue_hits = codes & TYPE_CUES.get(report.scam_type, set())
            score = min(0.45, 0.15 * len(cue_hits))
            if cue_hits:
                reasons.append("Similar cues: " + ", ".join(sorted(c.replace("_", " ").lower() for c in cue_hits)))
            shared = spoken & report.tokens
            overlap = len(shared) / max(1, min(len(spoken), len(report.tokens)))
            if len(shared) >= 2:
                score += min(0.4, overlap)
                reasons.append("Shared wording: " + ", ".join(sorted(shared)[:5]))
            if any(word in report.payment_methods.lower() for word in rail_words):
                score += 0.15
                reasons.append(f"Same payment type ({session.payment.rail})")
            if reasons and score >= MIN_SCORE:
                scored.append((score, report, reasons))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [ScamMatch(report_id=r.report_id, scam_type=r.scam_type, persona=r.persona,
                          payment_methods=r.payment_methods, dollars_lost=r.dollars_lost,
                          summary=r.narrative[:400], similarity=round(min(score, 1), 2), reasons=reasons)
                for score, r, reasons in scored[:MAX_MATCHES]]
