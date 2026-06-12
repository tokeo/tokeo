"""
Byte tokenizer for the Spiral akili micro model.

A tokenizer turns text into integers the network can embed, and turns the
network's integer outputs back into text. Most language models learn a
*subword* vocabulary (tens of thousands of merged fragments) from a large
corpus. akili does the opposite and on purpose: it has **no trained
vocabulary at all**. The alphabet is simply the 256 possible byte values
plus three control tokens.

### Why bytes, not subwords

The whole job of akili is to copy dates and numbers exactly -- "2026-12-24"
must come out as "2026-12-24", and "2" must stay "2", never "two" and never
"26". A subword vocabulary would merge "202", "6-", "12" into learned
chunks whose splitting depends on the training corpus, which makes faithful
copying harder for a tiny model. With raw bytes every character is its own
token, so copying a date is just "emit the same token ids you saw" -- a
pattern a small network learns reliably. The price (a few tokens per word
instead of one) is irrelevant here: requests are short.

### The vocabulary, exactly

- ids 0..255 -- the raw byte values; any utf-8 text maps onto these
- id 256 = PAD -- fills a batch row out to a fixed length; the loss
    ignores these positions, they carry no meaning
- id 257 = SEP -- the boundary between the request and the plan; the
    model learns "after SEP, start planning"
- id 258 = EOS -- end of the plan; emitting it stops generation

That is VOCAB = 259 rows in the embedding table and 259 scores at the
output head. Change any of these constants and you must retrain: every id
is a row index into the learned weights, so the meaning of "257" is baked
into the model. Adding a fourth special token (260) would widen the
embedding and head by one row and break old weights.
"""

# the three control ids live just past the 256 byte values, so a single
# comparison (token < 256) cleanly separates "real text" from "control"
PAD = 256
SEP = 257
EOS = 258

# the embedding table and the output head both have exactly this many rows;
# it is part of the saved architecture, not a free runtime knob
VOCAB = 259


def encode(text):
    """
    Encode text as a list of byte token ids.

    ### Args

    - **text** (str): The text to encode

    ### Returns

    - **list**: The utf-8 byte values, each already a valid token id in
        ```0..255```

    """
    # utf-8 maps every character to one to four bytes; errors='replace'
    # guarantees a result even for malformed input instead of raising.
    # the bytes object is iterable as ints, so list() yields the token ids
    return list(text.encode('utf-8', errors='replace'))


def decode(tokens):
    """
    Decode byte token ids back to text.

    ### Args

    - **tokens** (list): Token ids, possibly including PAD/SEP/EOS

    ### Returns

    - **str**: The decoded text; control tokens are dropped

    """
    # keep only real bytes (token < 256): PAD/SEP/EOS carry no character and
    # are filtered out, then the byte sequence is decoded back to utf-8.
    # errors='replace' keeps decoding total even if generation stopped in
    # the middle of a multi-byte character
    return bytes(token for token in tokens if token < 256).decode('utf-8', errors='replace')
