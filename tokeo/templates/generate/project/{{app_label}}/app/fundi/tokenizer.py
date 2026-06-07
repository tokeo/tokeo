"""
Byte tokenizer for the {{ app_name }} fundi micro model.

No trained vocabulary: the 256 byte values plus three special tokens. Dates
and numbers pass through character by character, so the model copies them
literally -- the property that keeps a tiny model exact.
"""

PAD = 256
SEP = 257
EOS = 258

VOCAB = 259


def encode(text):
    """Encode text as a list of byte token ids."""
    return list(text.encode('utf-8', errors='replace'))


def decode(tokens):
    """Decode byte token ids (specials are skipped) back to text."""
    return bytes(token for token in tokens if token < 256).decode('utf-8', errors='replace')
