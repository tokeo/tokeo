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

    python -m {{ app_label }}.core.fundi.train

once; it reports the held-out accuracy and writes `weights.npz` into this
package. Until then the `fundi` profile raises a clear hint and the fundi
test cases skip.

## What you can ask it

Its competence is exactly the trained domain: the calendar tools, in
English and German, in plain and nested wordings, with time words and
signed offsets. Small, but real tasks:

    # appointments and weekdays
    {{ app_label }} ai ask "der wochentag von heute" --profile fundi
    {{ app_label }} ai ask "weekday of 2026-12-24" --profile fundi

    # deadlines and countdowns
    {{ app_label }} ai ask "count the days from today until 2026-12-24" --profile fundi
    {{ app_label }} ai ask "add 90 days to 2026-06-08" --profile fundi
    {{ app_label }} ai ask "das datum 14 tage vor 2026-12-24" --profile fundi

    # planning
    {{ app_label }} ai ask "die kalenderwoche von heute plus 30 tagen" --profile fundi
    {{ app_label }} ai ask "die mondphase am 2000-01-06" --profile fundi

    # relative words and month/year shifts
    {{ app_label }} ai ask "welches datum ist übermorgen" --profile fundi
    {{ app_label }} ai ask "the week number of next month" --profile fundi
    {{ app_label }} ai ask "heute plus 1 jahr" --profile fundi

    # and the three-step chains, its signature move
    {{ app_label }} ai ask "the weekday of today plus 14 days" --profile fundi
    {{ app_label }} ai ask "der wochentag von vor 2 tagen" --profile fundi

Deadline calculators, release countdowns, week-number lookups, "x days of
lead time before date y" -- deterministic, offline, tens of milliseconds,
and fully audited under `--agent guarded`.

### A planner, not a texter

The model's only learned output language is the plan DSL (plus
`<nomatch>`). The answer you see (`[fundi] weekday: Tuesday`) is always
the result of the last tool in the chain: the facts come from the
computation, never from the model. That split is deliberate -- dates and
arithmetic are exactly what language models, small or large, get wrong
when they memorize, and exactly what tools compute precisely. A 380k
model could never store a million date facts, but it can learn perfectly
*which computation is meant*. This is also why fundi cannot hallucinate:
what it does not understand becomes an honest `<nomatch>`, and what it
understands gets computed.

So there are three kinds of answers: a **tool result** (the normal
case), **honest ignorance** (`[fundi] sing me a song` -- the labelled
echo marks "outside my domain"), and **explanations** when the guard
pipeline reports `denied:` or `error:` ("not permitted: ..." instead of
retrying). Free-form text is not this model's job: where wording matters,
the clean place is a remote profile behind the very same agents, guards,
and tools -- the ladder mock -> fundi -> remote model tells the full
story. To give fundi new *tasks*, give it new tools and teach them (see
below); the provider, guards, and agents stay untouched.

## From ask to answer, as a picture

How one question travels through the whole stack -- the handler, the
agent's guard pipeline, the provider, the NumPy engine, and the tools.
Follow the numbers; the example is the signature three-step chain:

```mermaid
%%{init: {"theme": "base", "themeVariables": {"actorBkg": "#dbeafe", "actorBorder": "#2563eb", "actorTextColor": "#1f2937", "signalColor": "#334155", "signalTextColor": "#334155", "noteBkgColor": "#fef9c3", "noteBorderColor": "#ca8a04", "noteTextColor": "#1f2937", "labelBoxBkgColor": "#ede9fe", "labelBoxBorderColor": "#7c3aed", "loopTextColor": "#1f2937", "sequenceNumberColor": "#ffffff"}}}%%
sequenceDiagram
    autonumber
    actor U as user (cli)
    participant H as app.ai handler
    participant G as guard pipeline (agent guarded)
    participant P as fundi provider
    participant E as engine (core/fundi, NumPy)
    participant T as calendar tools

    U->>H: ask "der wochentag von heute plus 2 tagen"
    H->>H: resolve profile fundi + agent guarded, activate calendar tools

    rect rgb(237, 233, 254)
        Note right of U: the model plans -- nothing is executed yet
        H->>P: chat(messages, tool specs)
        P->>E: load weights.npz once (lazy)
        Note over P,E: no weights yet? clear train-first error instead
        E->>E: prompt bytes through the net once (KV cache), then greedy and grammar-constrained, best LEGAL character wins
        E-->>P: plan current()#59;add_days(date=@1,days=2)#59;weekday(date=@2)
        Note over P: plan is nomatch? answer the labelled echo instead
    end

    rect rgb(219, 234, 254)
        Note right of U: the pipeline governs, the tools compute
        loop one tool call per round
            P-->>H: ToolCall, round 1 is current()
            H->>G: validate, then policy, then audit
            alt allowed
                G->>T: exec
                T-->>G: result, e.g. 2026-06-08
                G-->>H: Invocation recorded into the trace
                H->>P: result as tool feedback
                Note over P: @1 and @2 resolve from earlier feedback
            else denied or error
                G-->>H: denied by policy
                H->>P: feedback
                P-->>H: answer "[fundi] not permitted ..." and stop, no retry
            end
        end
    end

    rect rgb(220, 252, 231)
        Note right of U: the answer is the last tool's result
        P-->>H: text "[fundi] weekday: Wednesday" plus raw.plan
        H-->>U: ChatResult with text, raw, and trace (3 entries)
    end
```

Color key: blue boxes are the framework actors, the violet band is the
planning phase, the blue band the governed execution loop, the green
band the answer; yellow notes mark the designed failure paths.

Three things to take away from the picture. First, the model never
executes anything: it only ever emits the next tool call, and every
execution runs through the agent's guards -- the model plans, the
pipeline governs, the tools compute. Second, the engine appears exactly
once per round and is pure NumPy: load the weights lazily, run the
prompt through the net, then pick, character by character, the best
*legal* continuation -- which is why a malformed plan is impossible.
Third, every failure mode has a designed answer: missing weights raise
the train-first hint, an out-of-domain request becomes the honest
labelled echo, and a denial ends the run with an explanation instead of
a retry loop.

### Inside the engine: how the weights answer

Step 6 of the sequence above, zoomed in. This is everything that happens
when the trained model is *used* -- pure NumPy over the matrices from
`weights.npz`, one character at a time, with the grammar as a fence:

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryTextColor": "#1f2937", "lineColor": "#64748b", "fontSize": "14px"}}}%%
flowchart TD
    IN("<b>the request</b><br/>'der wochentag von heute plus 2 tagen'<br/>as bytes, plus the SEP token")
    PRE("<b>prefill, once</b><br/>the whole prompt runs through the net,<br/>the keys and values of every layer<br/>are kept: the KV cache")
    LOG("<b>259 scores</b><br/>the tied head turns the last hidden state<br/>into one score per possible next byte")
    GRAM("<b>the grammar automaton (dsl.Constrainer)</b><br/>which characters are LEGAL here?<br/>only active tool names, only their slots,<br/>dates as YYYY-MM-DD or @k, at most 3 steps")
    PICK("<b>greedy pick</b><br/>the best-scored LEGAL character wins --<br/>deterministic: same request, same plan")
    STEP("<b>one-position forward</b><br/>only the chosen byte runs through the net,<br/>against the KV cache: cheap")
    DONE{"grammar allows<br/>the line to end?"}
    PLAN("<b>the plan line</b><br/>current();add_days(date=@1,days=2);weekday(date=@2)<br/>parsed into tool calls for the provider")

    IN --> PRE --> LOG
    LOG --> GRAM --> PICK --> STEP
    STEP -->|"next character"| LOG
    PICK --> DONE
    DONE -->|"yes"| PLAN
    DONE -->|"no"| STEP

    EX1("example: after 'days=' only digits<br/>and the minus sign are legal --<br/>chatty bytes can never appear") -.-> GRAM
    EX2("example: at a step start only the<br/>injected tools' names are legal --<br/>an unavailable tool cannot be planned") -.-> GRAM

    classDef data fill:#dbeafe,stroke:#2563eb,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    classDef model fill:#ffedd5,stroke:#ea580c,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    classDef grammar fill:#ede9fe,stroke:#7c3aed,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    classDef out fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    class IN data
    class PRE,LOG,STEP model
    class GRAM,EX1,EX2,DONE grammar
    class PICK,PLAN out
```

Color key: blue is the input, orange the model's matrices at work,
violet the grammar fence, green the decisions and the result.

Why this loop is fast and safe at once: the expensive part (the whole
prompt through the net) happens exactly once; every generated character
afterwards is a single-position forward against the cache -- tens of
milliseconds for a full plan. And the model's freedom is exactly the
grammar's freedom: it chooses *which* legal continuation, never
*whether* to be legal. The weights supply the judgement, the automaton
supplies the fence, and greedy picking makes the whole thing
reproducible -- the three properties (smart, safe, deterministic) come
from three separate, inspectable parts.

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
training. Day offsets are signed: plus/after/in wordings map to positive
day values, minus/before/ago (minus/vor in German) to negative ones.
Relative words (tomorrow, übermorgen, last week, next year ...) live in a
lookup table -- one line per word, mapping it to its shift from today --
and every shift shape speaks all units alike -- the unit word (days,
months, years) picks the tool.
Teaching a new word is adding a table line and retraining. A fixed seed makes the dataset reproducible at any time; run
`python -m {{ app_label }}.core.fundi.data` to print samples.

### `FUNDI-LEX.yaml` -- the editable language definition

The complete language of the training data as a richly commented yaml
file in three parts: **words** (time words, relative words with their
shift from today, the units with their declensions, consumer names),
**chatter** (negatives, preambles, lead-ins), and **patterns** -- four
groups only (`single`, `shift`, `shift_minus`, `relative`), held
together by one rule: *a `{c}` in any pattern means a consumer reads the
result*. `data.py` loads and validates it at import time (unknown tools
and missing placeholders fail loudly), a weight-free test guards the
format, and pdoc renders the whole file syntax-highlighted into the data
module's page. One file to read what the model is taught -- and to
extend it.

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

The `--no-minus` switch is a built-in ablation experiment:

    python -m {{ app_label }}.core.fundi.train             # with minus teaching
    python -m {{ app_label }}.core.fundi.train --no-minus  # without

Both runs share the architecture, the budget, and the schedule -- only the
dataset differs: with the switch, every signed-offset wording is left out,
and the resulting model has no notion of minus days (a request like
*"today minus 2 days"* falls back to the nearest learned pattern). Training
two weights files this way makes the central lesson of the lab tangible:
capability lives in the data, not in the code. The choice is recorded in
the exported metadata (`minus: true/false`), so a weights file always tells
what it was taught; the sample printer takes the same switch
(`python -m {{ app_label }}.core.fundi.data --no-minus`).

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

### The pipeline as a picture

The same story as a diagram, with real numbers from a default run. Read
it top to bottom: language in, weights out -- and the box at the bottom
shows what the weights file actually contains:

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryTextColor": "#1f2937", "lineColor": "#64748b", "fontSize": "14px"}}}%%
flowchart TD
    subgraph LEXP["FUNDI-LEX.yaml -- the language, three parts"]
        direction LR
        LW("<b>1. words</b><br/>time words, relative words,<br/>units with declensions,<br/>consumer names")
        LC("<b>2. chatter</b><br/>negatives, preambles,<br/>lead-ins")
        LP("<b>3. patterns</b><br/>four groups: single, shift,<br/>shift_minus, relative --<br/>one rule: a consumer slot<br/>in a pattern reads the result")
    end

    GEN("<b>data.dataset(30000, seed=7)</b><br/>the mixture: 15% nomatch, 12% relative words,<br/>45% shifts plain and composed, 28% single-step<br/>example pair:<br/>'der wochentag von heute plus 2 tagen'<br/>current();add_days(date=@1,days=2);weekday(date=@2)")
    HELD("<b>600 held-out pairs</b><br/>never trained on")
    TRAIN("<b>29400 training pairs</b>")
    TOK("<b>tokenizer: one fixed row of bytes</b><br/>'heute' becomes 104 101 117 116 101,<br/>then SEP=257, the plan bytes, EOS=258,<br/>padded with PAD=256 to 184 positions")
    NET("<b>FundiNet forward</b><br/>byte embedding 259x96 plus position embedding 184x96,<br/>3 blocks of: norm, attention with 4 heads, norm, MLP 96-384-96,<br/>final norm, tied head: 259 scores for every position")
    LOSS("<b>cross-entropy, masked</b><br/>request side before SEP: ignored, counts zero<br/>plan side after SEP: graded character by character")
    OPT("<b>AdamW + OneCycle</b><br/>1400 steps x 96 examples,<br/>gradient clipping")
    EVAL("<b>evaluate, the honest number</b><br/>greedy-decode the 600 held-out requests<br/>without the grammar automaton --<br/>exact plan-line match, e.g. accuracy 0.98")
    SAVE("<b>save</b>")

    LW --> GEN
    LC --> GEN
    LP --> GEN
    GEN -->|"never seen in training"| HELD
    GEN --> TRAIN
    TRAIN --> TOK --> NET --> LOSS --> OPT
    OPT -->|"repeat, 1400 batches"| TOK
    OPT -->|"after the last step"| EVAL
    HELD --> EVAL
    EVAL --> SAVE

    subgraph NPZ["weights.npz -- 378240 float32 numbers, about 1.5 MB; this IS the model"]
        direction TB
        W1("<b>embed.weight</b> 259x96 = 24864 numbers<br/>what each byte means")
        W2("<b>position.weight</b> 184x96 = 17664 numbers<br/>where in the sentence it stands")
        W3("<b>3 blocks, about 111800 numbers each:</b><br/>attention 288x96 + 96x96 -- learned gazes:<br/>copy date characters, spot intent words<br/>MLP 384x96 + 96x384 -- combine what was<br/>seen into patterns, plus the layer norms")
        W4("<b>__config__</b> embedded json<br/>architecture, accuracy, minus flag --<br/>the runtime reads its dimensions here,<br/>weights and inference can never drift")
    end
    SAVE --> NPZ

    classDef lang fill:#ede9fe,stroke:#7c3aed,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    classDef data fill:#dbeafe,stroke:#2563eb,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    classDef train fill:#ffedd5,stroke:#ea580c,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    classDef eval fill:#ccfbf1,stroke:#0d9488,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    classDef out fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#1f2937,rx:4px,ry:4px
    class LW,LC,LP lang
    class GEN,HELD,TRAIN,TOK data
    class NET,LOSS,OPT train
    class EVAL eval
    class SAVE,W1,W2,W3,W4 out
    style LEXP fill:#f5f3ff,stroke:#7c3aed,stroke-width:1.5px
    style NPZ fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px
```

Color key: violet is the language source, blue the data side, orange the
learning loop, teal the honest measurement, green the shipped artifact.

How to read the loop in the middle: one step takes 96 fresh examples,
asks the net for its next-character guesses, measures the gap on the
plan side only, and turns all 378240 numbers a tiny bit -- 1400 times,
first with large turns, then fine ones (the OneCycle schedule). The
held-out 600 never enter that loop, which is what makes the final
accuracy an honest number. And the weights box is the whole model: no
code, no rules ship with it -- the named matrices plus the embedded
architecture are everything `infer.py` needs, which is why inference
can be plain NumPy and why weights and runtime can never drift apart.

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
`python -m {{ app_label }}.core.fundi.train`, and check the reported accuracy
plus the project's test suite. The provider (`core/ai/fundi.py`), the
guards, and the agents need no change -- the plan grammar adapts to the
active tools at runtime.
