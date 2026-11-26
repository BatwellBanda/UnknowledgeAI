"""Word error rate for judging transcripts against a human reference transcript.

Transcription quality is measured on its own, separately from the quality of notes or answers
built on top of it, so a weak transcript is visible before it is blamed on anything else.
"""
import re

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def words(text: str) -> list[str]:
    """Lower-case words with punctuation removed, so formatting differences are not counted as errors."""
    return _WORD.findall(text.lower().replace("’", "'"))


def word_errors(reference: str, hypothesis: str) -> dict:
    """Minimum substitutions, deletions and insertions turning the reference into the hypothesis."""
    ref, hyp = words(reference), words(hypothesis)
    # Each cell holds (total, substitutions, deletions, insertions) for the best alignment so far.
    previous = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i in range(1, len(ref) + 1):
        current = [(i, 0, i, 0)]
        for j in range(1, len(hyp) + 1):
            if ref[i - 1] == hyp[j - 1]:
                current.append(previous[j - 1])
                continue
            sub, dele, ins = previous[j - 1], previous[j], current[j - 1]
            current.append(min(
                (sub[0] + 1, sub[1] + 1, sub[2], sub[3]),
                (dele[0] + 1, dele[1], dele[2] + 1, dele[3]),
                (ins[0] + 1, ins[1], ins[2], ins[3] + 1),
            ))
        previous = current
    total, substitutions, deletions, insertions = previous[-1]
    if ref:
        rate = total / len(ref)
    else:
        rate = 0.0 if not hyp else 1.0
    return {"reference_words": len(ref), "hypothesis_words": len(hyp), "substitutions": substitutions,
            "deletions": deletions, "insertions": insertions, "wer": rate}
