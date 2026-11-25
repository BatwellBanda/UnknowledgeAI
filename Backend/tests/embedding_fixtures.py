"""Deterministic model doubles; regression tests never download model weights."""
from types import SimpleNamespace


class CharacterTokenizer:
    def encode(self, text, *, add_special_tokens=True, truncation=False):
        assert truncation is False
        return [1] * (len(text) + (2 if add_special_tokens else 0))


def embedding_model(*, zero_vectors=False):
    return SimpleNamespace(
        tokenizer=CharacterTokenizer(), max_seq_length=256,
        encode=lambda texts, **kwargs: [([0.0] * 384 if zero_vectors else [1.0] + [0.0] * 383) for _ in texts],
    )
