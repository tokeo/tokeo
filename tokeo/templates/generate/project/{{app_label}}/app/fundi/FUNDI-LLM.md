# fundi -- the project's own micro language model

`fundi` is a real, trained language model that belongs to this application:
a few hundred thousand parameters learned from scratch on the project's own
synthetic data, running in-process with plain NumPy -- no host to start, no
network, no third-party weights. It does one thing, and does it exactly: it
turns a natural-language request (English or German) into a **plan** of tool
calls over the project's calendar toolset, including nested requests like
*"the weekday of today plus 2 days"*, which become real three-step chains.

It complements the framework's built-in `mock` provider: `mock` is the
neutral, deliberately dumb test double that proves the machinery (loop,
guards, budgets, trace) without any prerequisite; `fundi` is the content --
the proof that a generated project can own, train, and operate its model.
The agents stay model-free compositions: the same `audited` or `guarded`
agent runs against `mock`, `fundi`, or a remote profile unchanged.

Train-first: the weights are a project asset created by you. Run

    python -m {{ app_label }}.app.fundi.train

once; it reports the held-out accuracy and writes `weights.npz` into this
package. Until then the `fundi` profile raises a clear hint and the fundi
test cases skip.

## The pieces in this package

### `dsl.py` -- the plan language and its gatekeeper

The tiny target language the model speaks: one line, steps joined by `;`,
for example

    current();add_days(date=@1,days=2);weekday(date=@2)

where `@k` means "the result of step k", and `<nomatch>` marks a request
outside the domain. The module holds `DOMAIN` (which tool has which slots,
the single place that knows the toolset), `render`/`parse` (plan to line and
back), and the `Constrainer`: a byte-level automaton that answers, for any
partial line, which characters are legal next -- only active tool names,
only their slots, dates only as `YYYY-MM-DD` or `@k`, at most three steps.
The decoder may only pick what the automaton allows, so the model can never
emit a malformed plan or a tool outside the injection, no matter how it was
trained.

### `tokenizer.py` -- the byte vocabulary

No trained vocabulary: the 256 byte values plus `PAD`, `SEP`, and `EOS`
(vocabulary size 259). Byte level is the decisive choice here -- dates and
numbers consist of the same characters as the sentence, so the model can
copy them character by character instead of treating them as unknown words.
That property is what keeps a model this small exact.

### `data.py` -- the synthetic data generator

The calendar domain is closed, so the dataset is generated, not collected.
Every example is a *(sentence, plan line)* pair, built from phrase templates
per tool in English and German, nested compositions, time words
(today/now/current and heute/jetzt/aktuell) that put a `current()` step in
front of the plan, distractor preambles and polite lead-ins (also in front
of negatives, so chatter never becomes a positive signal by itself), and
out-of-domain negatives that map to `<nomatch>` -- the anti-hallucination
training. A fixed seed makes the dataset reproducible at any time; run
`python -m {{ app_label }}.app.fundi.data` to print samples.

### `train.py` -- the training tool (the only torch in the project)

A from-scratch decoder-only transformer: byte embeddings plus position
embeddings, three blocks of multi-head attention and a small MLP, projected
back against the embedding matrix (tied head) -- about 380k parameters. The
training sequence is `sentence + SEP + plan + EOS`, and the loss only counts
the plan side. AdamW with a one-cycle schedule, environment knobs
(`FUNDI_STEPS`, `FUNDI_BATCH`, `FUNDI_DATA`), and interruptible chunked runs
via `FUNDI_CKPT`/`FUNDI_CHUNK`. At the end it evaluates exact-plan accuracy
on held-out examples and saves `weights.npz`. Torch is a dev-side tool only;
the application never imports it.

### `infer.py` -- the runtime (plain NumPy)

Loads `weights.npz` and reruns the exact forward pass in NumPy (verified
bit-compatible with torch). Decoding is greedy and constrained: from the
model's ranking, the best *legal* character wins. A KV cache makes it fast
(the sentence is processed once, every generated character is a single-step
forward, in the order of tens of milliseconds per request), and if
[numba](https://numba.pydata.org) is installed, the attention loop is
JIT-compiled automatically -- without it the same numbers run as pure NumPy.

### `weights.npz` -- the model itself

A compressed archive of named float32 matrices, one per parameter, plus the
architecture and the achieved accuracy as embedded metadata, so inference
and weights can never drift apart. Created exclusively by training.

## Training, in plain words

Training asks the network the same kind of question millions of times:
*here is the sentence, the separator, and the beginning of the plan line --
which character comes next?* The network guesses, the truth is in the
dataset, and the gap between guess and truth is measured as a loss. Then
backpropagation computes, for every one of the ~380k numbers, in which
direction a tiny turn would shrink that loss -- and turns them all a little.
Repeat with fresh batches, first with large steps, then fine ones. That is
all training is: organized trial and error with feedback, until the right
continuations are the most probable ones.

What ends up in the weights is **no rule, no word list, no if-then** -- only
matrices that together form a function. Roles can be ascribed: the
embeddings place every byte in a 96-dimensional space; attention matrices
are learned gazes (when writing a date slot, look back at the date
characters in the sentence -- which is why copies are exact; other heads
attend to intent words like *weekday* or *wochentag*); the MLPs combine what
was seen into patterns (*time word present, so the plan starts with*
`current()`). The knowledge lives distributed across all of them; no single
number means anything, only their interplay does. That is why it shows real
(small) language-model behavior -- it generalizes over phrasings never seen
verbatim -- and why its competence honestly ends where the data generator's
teaching ends. Determinism holds throughout: fixed weights, greedy decoding,
and the grammar automaton -- same sentence, same plan, every time.

## Extending the model

The path is always the same: teach it in `data.py` (new templates, new
tools in `DOMAIN`, more phrasings, more languages), retrain with
`python -m {{ app_label }}.app.fundi.train`, and check the reported accuracy
plus the project's test suite. The provider (`core/ai/fundi.py`), the
guards, and the agents need no change -- the plan grammar adapts to the
active tools at runtime.
