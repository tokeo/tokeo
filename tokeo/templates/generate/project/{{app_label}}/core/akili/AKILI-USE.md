# akili in use -- a guided demo in three acts

One sentence carries the whole design: **fundi** (the master) wields the
tools, **akili** (the mind) plans them, and **tokeo** (the result) is what
they produce together. This document is the stage script for showing that
live: act 1 demonstrates the fundi agent (guards, sandboxes, denials) with
the deterministic mock model, act 2 puts the real, self-trained akili
micro model behind the very same governance, and act 3 -- on purpose --
shows where the model breaks, and why. Every command sits in its own
shell block, so each one is a single copy away from your terminal.

`AKILI-LLM.md` explains how the model works; this file shows what it does.

## Before the show

Train the weights once. They are a project asset (gitignored), built from
the synthetic data in `AKILI-LEX.yaml` in a few minutes on a CPU:

```shell
python -m {{ app_label }}.core.akili.train
```

Give the file tools something to read:

```shell
echo "fundi wields the tools, akili plans them" > tmp/notes.txt
```

## Act 1 -- the fundi agent (the mock model as a deterministic driver)

The built-in `mock` provider is the test driver: it makes a tool call
whenever the first word of the prompt names an active tool, otherwise it
echoes. Deterministic, offline, perfect for demos -- everything in this
act runs before any model is trained.

### 1. A tool call, contained: calc runs in a subprocess

```shell
{{ app_label }} ai ask "calc 2 + 3" --agent guarded
```

Expected: `Done. The tool returned: 5` (plus an audit log line). The
point is *where* it ran: the guarded agent's sandbox chain is
`jailed -> allow`, and `jailed` (a subprocess sandbox) lists the
`mathematics` group -- so the calculator executed in a fresh interpreter
with a wall-clock timeout, not in your application process.

### 2. The chain walks: current falls through to in-process

```shell
{{ app_label }} ai ask "current" --agent guarded
```

Expected: the current timestamp. `current` is not listed by `jailed`, so
the chain walks on to `allow` (`in_process`, `tools: _all`) -- same
agent, different "where". Remove `allow` from the chain and unlisted
tools would be denied instead: the chain is the single truth.

### 3. Reading is permitted

```shell
{{ app_label }} ai ask "read_file notes.txt" --agent guarded
```

Expected: the note's content. The readonly policy guard lets reads pass.

### 4. Writing is denied -- and the loop survives it

```shell
{{ app_label }} ai ask "append_file hello" --agent guarded
```

Expected: `denied: tool 'append_file' is not permitted by policy`. The
guard pipeline records the denial, audits it, and feeds it back to the
model as text -- deny-and-continue, no crash, no retry storm.

### 5. The agent IS the composition

```shell
{{ app_label }} ai ask "calc 2 + 3" --agent audited
```

Expected: a plain `[mock] calc 2 + 3` echo. The `audited` agent carries
no tools, so the very same prompt executes nothing. Switching the agent
swaps the whole composition -- tools, guards, sandboxes, budgets.

### 6. Inspect, don't trust: the trace

```shell
{{ app_label }} ai ask "calc 6 * 7" --agent guarded --json
```

Expected: the full machine-readable result -- every invocation with its
arguments, the tool's declared schema, the guard decision, the result,
and the token usage. Nothing about the call is hidden.

### 7. Governance starts before the first call

```shell
{{ app_label }} ai lint
```

Expected: `ai config ok`. Unknown tools, dangling sandbox names, or
options a sandbox cannot enforce surface here, not at runtime.

## Act 2 -- the akili model (a real, self-trained micro model)

Now the same governance, but the plan comes from a real language model:
~651k parameters, trained from scratch on this project's synthetic
calendar data, running in plain NumPy -- no server, no API key. The
model plans, the tools compute: facts never come from the weights.

### 8. A nested request, in German

```shell
{{ app_label }} ai ask "wochentag von heute plus 14 tage" --profile akili
```

Expected: the weekday two weeks from now. The model emitted a chain --
resolve today, shift it, read its weekday -- and every step was executed
as a real tool call under the guarded agent.

### 9. The plan, made visible

```shell
{{ app_label }} ai ask "weekday of 2026-12-24" --profile akili --json
```

Expected: the trace shows the tool chain the model planned, step by
step, with the exact date copied byte-for-byte into the arguments (the
byte tokenizer at work -- no subword vocabulary to mangle a date).

### 10. Shared governance, own subset: profile deny

```shell
{{ app_label }} ai ask "calc 2 + 3" --profile akili --json
```

Expected: no calculation. The akili profile shares the `guarded` agent
(same guards, same sandbox chain) but denies the `mathematics` and
`filesystem` groups -- look at the specs in the JSON: the calculator is
simply not offered. One agent, several profiles, each carving out only
the tools it needs: `agent.tools - agent.deny - profile.deny`.

## Act 3 -- where akili breaks, and why (limits, on purpose)

`AKILI-LLM.md` explains why akili cannot hallucinate *form*: the plan
grammar admits no invented tools, and ~15% of the training mixture maps
off-domain requests to an honest `<nomatch>`. But abstention is a trained
pattern like any other -- and patterns have edges. *Meaning* can still go
wrong: a well-formed plan for a question that was never asked, exactly
where a request falls into the gap between the trained patterns and the
trained refusals. This act demonstrates those gaps deliberately.

A note on reproducibility: the exact outputs depend on your local
training run (seed, epochs). The failure *classes* below follow from the
lexicon, the mixture, and the plan grammar -- break 2 is even forced by
the grammar itself. Run all four once and pick the most telling ones for
your stage.

### The anchor: honesty works where it was trained

```shell
{{ app_label }} ai ask "what is the capital of france" --profile akili
```

```shell
{{ app_label }} ai ask "wann ist ostern" --profile akili
```

```shell
{{ app_label }} ai ask "heute plus -2 tage" --profile akili
```

Expected: three honest labelled echoes. All three are in-distribution
refusals -- plain chatter, a calendar-near hard negative, and a signed
count (signs on a bare count are taught as `<nomatch>` in every
phrasing). So far the abstention training holds. Now the edges.

### Break 1 -- keyword hijack: pattern completion beats intent

```shell
{{ app_label }} ai ask "erinnere mich in 3 tagen an annas geburtstag" --profile akili
```

Expected failure: a confident date answer (an `add_days(+3)` plan) to a
reminder request akili cannot serve at all. Why: this prompt is neither a
trained positive (there is no reminder pattern) nor a trained negative --
the negatives are deliberately kept free of calendar words, so "in 3
tagen" sits in no-man's-land while being a high-frequency fragment of the
positive patterns. A 651k-parameter transformer has no concept that
reminding is not date arithmetic; attention locks onto the strongest
learned byte pattern and completes it. **Lesson: a language model does
not answer your question -- it answers the most similar question from its
training data.**

### Break 2 -- numbers are bytes, not magnitudes (grammar-forced)

```shell
{{ app_label }} ai ask "heute plus 12345 tage" --profile akili
```

Expected failure: a plan with a mangled count (three digits of the five)
and a precisely computed, wrong date. Why: day counts were trained small
on purpose, and the plan grammar caps a count at three digits (0..999) --
a correct plan for 12345 is *syntactically impossible*, so the model must
drop digits. The byte tokenizer reads any number, but the model has no
concept of magnitude: digits are tokens, not quantities. Note the second
half of the lesson: the tool then computes the wrong date *correctly* --
the hallucination lives in the plan, never in the execution. **Lesson:
extrapolation beyond the training distribution is not a capability; here
the grammar even proves it must fail.**

### Break 3 -- negation is invisible

```shell
{{ app_label }} ai ask "welcher wochentag ist nicht heute sondern morgen" --profile akili
```

Expected failure: the weekday of *today* -- the explicit negation is
steamrolled (depending on your run it may also tip into `<nomatch>`;
both are demonstrable). Why: not a single training pattern contains
negation, and "heute" and "morgen" are both strong cues. The model
weighs surface patterns; it has no mechanism to read "not X but Y" as a
choice. **Lesson: negation is the classic LLM weakness -- here you can
see, under the microscope, why: it simply is not in the distribution.**

### Break 4 -- composition ends at the training depth

```shell
{{ app_label }} ai ask "heute plus 2 tage plus 3 wochen" --profile akili
```

Expected failure: only one of the two shifts lands in the plan (or the
numbers blend). Why: the generator produces numeric shifts only *singly*;
chains exist only as relative-word chains ("tomorrow next year"). Two
numeric shifts have never been seen as a *shape* -- and what was never
seen as a shape cannot be composed. **Lesson: generalization is not
composition.**

### The punchline: governance catches what the model gets wrong

```shell
{{ app_label }} ai ask "erinnere mich in 3 tagen an annas geburtstag" --profile akili --json
```

The hallucinated plan is right there in the trace -- arguments, schema,
decision, result. And this closes the circle back to act 1: *because*
models break like this, the fundi machinery exists. The validate guard
checks arguments against the schema, deny and the sandbox chain bound
what a wrong plan can touch, `max_loops` stops a model stuck on
refusals, and the trace makes every step inspectable. **The model may be
wrong -- the architecture makes the error visible, bounded, and cheap.**

## Curtain

The ladder is the story: the **mock** shows the loop with no model at
all, **akili** shows a real model you trained yourself planning real
tool chains, and a **remote profile** (the OpenAI-compatible provider)
puts a large model behind the very same agents, guards, and sandboxes --
nothing about the governance changes. Bigger models move the edges of
act 3; they do not remove them. That is the point of the whole exercise.
