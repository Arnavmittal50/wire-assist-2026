"""Conservative audio/text alignment. Batch-local IDs never imply identity.

Only shared timed words in overlapping audio establish cross-batch continuity.
Text is used to locate the live bubble, never to infer a voice or job role.
"""
from collections import Counter, deque
from dataclasses import dataclass
from dataclasses import replace
from difflib import SequenceMatcher
import re


def tokens(text):
    return re.findall(r"\w+", text.casefold(), re.UNICODE)


@dataclass(frozen=True)
class AlignedWord:
    text: str
    speaker_id: str | None
    start: float | None
    end: float | None


class SpeakerReconciler:
    def __init__(self, session, recording_id, origin):
        self.session = session
        self.recording_id = recording_id
        self.recent = deque(maxlen=6)
        self.next_index = 0
        self.completed = {}
        self.can_anchor_first_voice = origin == 0 and not session.speaker_roles
        self.failed_ranges = deque(maxlen=6)

    def _new_identity(self):
        number = len(self.session.speaker_roles) + 1
        name = f"speaker_{number}"
        while name in self.session.speaker_roles:
            number += 1
            name = f"speaker_{number}"
        self.session.speaker_roles[name] = "unknown"
        return name

    def accept(self, batch, words, error=None):
        if batch.recording_id != self.recording_id or batch.index < self.next_index:
            return
        # Scheduler is sequential; this also safely holds limited reordered replies.
        if batch.index > self.next_index + 600:
            return
        self.completed[batch.index] = (replace(batch, pcm=b""), words, error)
        while self.next_index in self.completed:
            item, annotations, failure = self.completed.pop(self.next_index)
            self.next_index += 1
            if failure:
                self.can_anchor_first_voice = False
                self.failed_ranges.append((item.start, item.end))
                continue
            self._map(item, annotations)

    def _map(self, batch, words):
        previous = [w for old, content in self.recent for w in content
                    if w.start is not None and w.end is not None
                    and w.end > batch.start and w.start < batch.end and w.speaker_id]
        labels = list(dict.fromkeys(w.speaker for w in words if w.speaker))
        labels.sort(key=lambda label: min((w.start for w in words if w.speaker == label and w.start is not None), default=float("inf")))
        continuity = {}
        for label in labels:
            votes = Counter()
            evidence = set()
            for word in words:
                if word.speaker != label or word.start is None or word.end is None:
                    continue
                matches = {old.speaker_id for old in previous if tokens(old.text) == tokens(word.text)
                           and abs(old.start - word.start) <= .35 and abs(old.end - word.end) <= .35}
                if len(matches) == 1:
                    identity = next(iter(matches))
                    key = (round(word.start, 2), tuple(tokens(word.text)))
                    if key not in evidence:
                        evidence.add(key)
                        votes[identity] += 1
            if len(votes) == 1 and next(iter(votes.values())) >= 2:
                continuity[label] = next(iter(votes))
        # Two local voices cannot both inherit the same identity.
        counts = Counter(continuity.values())
        continuity = {label: identity for label, identity in continuity.items() if counts[identity] == 1}
        mapping = {label: continuity.get(label) or self._new_identity() for label in labels}
        if self.can_anchor_first_voice and words and any(
                w.speaker is None or w.start is None or w.end is None for w in words):
            # If the opening voice itself was unidentified, a later identified
            # voice cannot safely inherit the first-speaker convention.
            self.can_anchor_first_voice = False
        if self.can_anchor_first_voice and labels and all(w.start is not None for w in words):
            # Explicit user convention applies to voices in the first observed audio,
            # never to text chunks or to a label called spk_1 in later batches.
            self.session.speaker_roles[mapping[labels[0]]] = "teller"
            if len(labels) == 2:
                self.session.speaker_roles[mapping[labels[1]]] = "member"
            self.can_anchor_first_voice = False
        elif len(labels) == 2 and len(continuity) == 1:
            # If only the teller has ever been established, a distinct co-occurring
            # voice is the second speaker. Do not reuse IDs without overlap evidence.
            known_roles = [role for role in self.session.speaker_roles.values() if role != "unknown"]
            known = next(iter(continuity.values()))
            if known_roles == ["teller"] and self.session.speaker_roles[known] == "teller":
                new_label = next(label for label in labels if label not in continuity)
                self.session.speaker_roles[mapping[new_label]] = "member"
        aligned = [AlignedWord(w.text, mapping.get(w.speaker), w.start, w.end) for w in words]
        # Drop PCM references immediately; retained results contain only bounded annotations.
        self.recent.append(((batch.start, batch.end), aligned))

    def match(self, segment):
        wanted = tokens(segment.text)
        if not wanted or segment.recording_id != self.recording_id:
            return None
        candidates = []
        covered = False
        for (start, end), words in self.recent:
            if segment.end_time < start - 1 or segment.start_time > end + 1:
                continue
            covered = True
            flat = [(token, word) for word in words for token in tokens(word.text)]
            heard = [token for token, _ in flat]
            blocks = SequenceMatcher(None, wanted, heard, autojunk=False).get_matching_blocks()
            matched = sum(block.size for block in blocks)
            threshold = 1 if len(wanted) <= 4 else .85
            if matched / len(wanted) < threshold:
                continue
            positions = [i for block in blocks for i in range(block.b, block.b + block.size)]
            if not positions:
                continue
            # Repeated short responses cannot be located reliably by wording alone.
            if len(wanted) <= 4:
                occurrences = [i for i in range(len(heard) - len(wanted) + 1)
                               if heard[i:i + len(wanted)] == wanted]
                if len(occurrences) != 1:
                    continue
            selected = [word for _, word in flat[min(positions):max(positions) + 1]]
            if len(selected) > len(wanted) * 1.4 + 1:
                continue
            identities = {word.speaker_id for word in selected}
            timed = [word for word in selected if word.start is not None and word.end is not None]
            if not timed or len(identities) != 1 or None in identities:
                candidates.append((None, None, None))
                continue
            first, last = min(w.start for w in timed), max(w.end for w in timed)
            if last < segment.start_time - 1 or first > segment.end_time + 1:
                continue
            identity = next(iter(identities))
            simultaneous = any(w.speaker_id != identity and w.start is not None and w.end is not None
                               and any(min(w.end, t.end) - max(w.start, t.start) > .1 for t in timed)
                               for w in words)
            candidates.append((None if simultaneous else identity, first, last))
        if candidates:
            identities = {item[0] for item in candidates}
            if len(identities) == 1 and None not in identities:
                identity, start, end = candidates[-1]
                return identity, start, end, "CONFIRMED"
            return None, None, None, "UNCERTAIN"
        if any(segment.start_time < end and segment.end_time > start for start, end in self.failed_ranges):
            return None, None, None, "FAILED"
        if covered:
            return None, None, None, "UNCERTAIN"
        return None

    def clear(self):
        self.recent.clear()
        self.completed.clear()
        self.failed_ranges.clear()
