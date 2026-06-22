# Tokeo Principles — Why It Is Built This Way

This document states what Tokeo is and the principles it is built on. It is not a
feature list and not a tutorial -- it explains *why* the pieces are the way they
are, so a developer can tell what Tokeo will and will not do for them, and what
"opinionated" means here.

Tokeo is an event-driven backend framework for Python. The governed AI runtime
is one capability on top of it -- not the centre, but the one that sets Tokeo
apart. This document covers the whole, with the AI principles as one section
among the rest.


## What Tokeo is

Tokeo is a robust CLI first framework for task automation, message queues, and
web interfaces. It is built for Python backend projects in an event-driven
architecture (EDA) world, where you need a fast and reliable development cycle. It
is a great starting environment for both ends of the range: small enough that a
quick prototype or a single scheduled job is productive in minutes, and complete
enough that the same project grows into a large, multi-service backend without
changing its shape. You start small without painting yourself into a corner, and
you scale up without rebuilding.

This is not a promise on paper: the core is rock-solid and has been in
production use for years, across projects of very different sizes. It just runs,
and keeps running.

It is not one library; it is an integrated stack, assembled entirely from proven,
battle-tested foundations and wired together so they work as one. Rather than
reinventing the wheel for background processing, scheduling, or CLI management,
Tokeo integrates mature, industry-standard projects.

As one capability among these — yet the one that sets Tokeo apart — it includes
a governed AI runtime. You build AI-assisted processes, agentic microservices,
automate workflows, or prototype, and the structure is already there.

The reason it is assembled rather than invented: Tokeo's value is not in
replacing solid engineering with novelties, but in making existing, robust parts
a coherent whole with one configuration model, one CLI, and one development cycle.
You do not spend your first week wiring a broker to a scheduler to a web layer.

The setup is there from the first minute, not something you assemble later.
A generated project already carries the full developer toolchain: a `Makefile`
with one-liners for formatting, linting, testing (with coverage), building the
docs, and packaging. The inner loop is set up before you write a line of your
own. Configuration is environment-based (prod, stage, dev, test) with secrets
kept safe through the vault, so real credentials are pleasant and safe to work
with from the start. Shipping is the same: the application deploys as a standard
Python build (sdist, wheel) right through to a Docker image. One touch to build,
one touch to run. The groundwork is done, so your energy goes into what you are
building.


## The architecture principles (these hold across the whole framework)

These convictions shape the whole framework -- they apply to tasks, scheduling,
automation, web, secrets, and the AI alike.

**Everything is configuration, in YAML.** Tasks, automations, agents, profiles,
schedules, and secrets are declared in `config/`, with ENV overrides on top. A
system you can read is one you can audit, diff, review, and reproduce -- which is
why the AI's agents and profiles are plain YAML too, not a separate concept.

**The CLI is the surface, code is the entry point.** Everything is reachable as a
`spiral <command>`, but the real integration point is programmatic -- a task, a
scheduler job, a gRPC handler calling into your logic. The CLI drives, inspects,
and tests; production runs call the functions.

**Secrets stay encrypted at rest, transparent on read.** The vault handler
resolves `!vault:`-tagged values at the leaf, on read, so your YAML never holds a
plaintext credential and consumer code never changes. The encrypted path is the
default, invisible path -- which is what makes it actually used.

**Local-first and sovereign.** From the RabbitMQ broker to the AI provider, the
parts run on your machine or your infrastructure, talking over open protocols. A
backend you cannot run offline, inspect, or own is a backend you have rented;
Tokeo keeps the system in your own hands.

**Transparency over convenience, where they conflict.** Inspectable states,
explicit configuration, honest tiering -- chosen over hidden magic that "just
works" until it does not. The cost of opaque automation is paid later, at
debugging time, by whoever did not write it; Tokeo pays it up front, in
visibility.

**Determinism is the target.** The framework aims for reproducible, pattern-true
behaviour: the same config and the same input give the same run -- an event-driven
backend processing real work cannot be a slot machine. Where a deterministic path
exists, Tokeo takes it; variability is allowed only where it is the point (and,
for the AI, only in *which* function runs, never in whether the outcome is
trustworthy).

**Assembled from the proven, not reinvented.** Cement, Dramatiq, RabbitMQ, gRPC,
APScheduler, Fabric, NiceGUI are mature projects; Tokeo integrates them. The
opinion Tokeo adds is *how they fit together*, not a replacement for any of them.


## What "opinionated" means here

Tokeo is opinionated, and it matters to be exact about *where*. It is **not**
opinionated about your domain, your tasks, your tools, your models, or your
workflows -- those are yours. It is opinionated about the **shape**: how a thing
is declared, configured, made visible, and governed.

Concretely, the framework gives you one way to do the structural things, on
purpose:

- **One configuration model** (YAML + ENV overrides) for every part, so learning
  one part teaches you the next.
- **One project layout** (`core/logic`, `core/tasks`, `core/ai`, `controllers`,
  `site`, ...), generated, so every Tokeo project is navigable by anyone who
  knows one.
- **One development cycle** (`make fmt`, `lint`, `test`, `doc`, `sdist`), so the
  inner loop is the same across projects.
- **One way to add a building block** -- a named entry with a `type` and its
  settings -- whether it is a task, an agent, a guard, or a tool.

These opinions are deliberately narrow: they make the *scaffolding* uniform so
the *substance* is entirely yours. That is the sense in which Tokeo is
opinionated -- strong opinions on the rails, none on the cargo.


## The AI principles -- orchestration and governance

Tokeo AI provides the governance to build AI-orchestrated processes that are safe
and verifiable: the functionality to let a model drive real tools transparently,
deterministically, and under control. It is wired into a generated project, never
into the framework itself, and it activates nothing on its own.

**The AI orchestrates; it does not compute.** The model's job is the variant-rich
orchestration of existing functions -- it decides *which* tool runs *when*. The
work is done by your plain functions, in deterministic, inspectable code. A
language model is good at choosing and sequencing and bad at being a source of
truth; Tokeo uses it for the former and forbids it the latter.

**Declare, then enforce.** A description or a prompt is influence, never a
guarantee -- a model is free to ignore any text. Control comes from code that
enforces. This is why every tool call passes a guard pipeline (validate, policy,
audit) and why a skill's declared tool list is something a guard grants, not
something trusted because it was written down.

**Normalize first, then govern.** A tool call is reduced to one shape -- whether
the model emitted it natively or it was parsed from prose -- before the guards
run, so the same rules apply regardless of provider or model. Governance that
depends on the model's output format is governance with holes.

**A guard decides, a sandbox contains.** The two are never mixed: a guard
authorizes, a sandbox is a dumb wall around execution. Kept apart, you can read
exactly what was allowed and exactly what was contained.

**The trace is the single source of truth.** Every object a run produces is a
step on one ordered trace; the typed views (messages, tool calls, results) are
references into it, not copies. The trace is how Tokeo keeps "what the agent did"
honest -- the same model pipeline from a 1.5 MB local micro model up to any large
provider.

**The honest guarantee.** Tokeo can guarantee, deterministically, that a tool's
returned value is carried into further processing exactly as returned, with no
model modification. It cannot guarantee that free model prose weaving several
results is faithful. The precise promise is about the *value channel*, not about
the model's prose, and not about whether the model called the tool correctly --
but it does guarantee the traceability and verifiability of the results.

**External capability is treated like a code dependency.** A tool, a skill, a
request is reviewed, pinned, audited, and run under containment -- not trusted
because it loaded. This is why **skills are declared, not discovered**: a skill
is part of the delivered application, named in the config, never picked up by
dropping a folder on disk. Discovery would make the agent's capabilities depend
on filesystem state instead of the config -- breaking the auditability and
determinism the whole framework is built on. Tokeo adopts the portable
agentskills.io format but mirrors it into its own config and runs it through the
same guards and sandbox.


## Framework versus application

Tokeo ships the mechanism; your application supplies the matter. The framework
can be upgraded without touching your logic, and your logic is never entangled
with framework internals.

The framework ships the base classes, the registries, the CLI, the extensions,
and -- for the AI -- the loop, the guards, the deterministic mock provider, the
agent, and the sandbox seam. Real models, real tools, and showcase wiring live in
the generated template and in **Spiral**, the reference application. Spiral is
always a cleanly generated version of the template, never hand-edited -- so the
framework is continuously proven against itself.

Extension points are provided ahead of need: a guard type with no implementation
yet, a typed exception per family, an empty default a future slice fills. This is
not over-engineering; it is what a framework is -- a place for *your* code to
attach. Tokeo is not built with speculative *features*.


## What Tokeo deliberately does not do

The boundaries are a position as much as the features:

- **It does not overrun your proven infrastructure.** It talks to a running
  RabbitMQ, a running LLM endpoint, a running database, and all the other
  services you depend on. A Docker-built container may ramp up a self-contained,
  run-only environment with volatile services like a RabbitMQ for a
  "flow-through" app that only needs them while it runs.
- **It does not carry a heavy provider abstraction for AI.** A thin client over
  the OpenAI-compatible transport plus a built-in mock, not a fat multi-provider
  matrix -- the 80/20 is one protocol, and native providers are added only when
  actually needed.
- **It does not bundle AI models in core.** Core ships the deterministic mock
  only; any real model lives in Spiral, an extension, or your project. A model is
  a heavy, opinionated dependency, and the framework stays light and
  provider-agnostic by keeping it out.


## How Tokeo evolves -- engineering guardrails

Tokeo is built and maintained under strict methodological rules. These principles
keep the framework from accumulating technical debt, half-baked features, or
contradictory patterns over time.

- **Complete execution (no broken windows).** When an architecture agreement or a
  convention changes, the change is carried through in full, including
  retroactively across existing code. No historical artifacts or contradictory
  patterns are left behind in the system.
- **Agreements are inviolable.** Settled specifications and architectural
  conventions are not changed unilaterally or silently. If a constraint blocks
  progress, the design is questioned and renegotiated, never quietly bypassed.
- **Driven by need, not speculation.** An extension point is provided ahead of
  need to keep the architecture open, but no speculative features are built. When
  a feature is added, it is built out completely -- but only as far as the
  framework strictly requires it.
- **Deliberate scope.** An observation is not an order. Noticing or flagging a
  problem is crucial, but it is not an automatic license to refactor outside the
  current scope. Changes stay focused, predictable, and exact to what was asked.
- **Transparency in delivery.** Every change, every trade-off, and every side
  effect is named in full. Documentation states the what and the why for
  developers and users, never serving merely as a memory aid for the author.
- **Tests check reality, not agreements.** Tests verify actual system behaviour
  and edge cases, not an idealized state, and never just to satisfy a coverage
  metric.
- **Zero-assumption verification.** Read twice, question the premise, and verify
  before reporting. Nothing is called "done" until the checks explicitly pass.


## How to read this document

These principles are stable; the code catches up to them slice by slice. Where a
principle and the current code disagree, the principle is the intent and the gap
is a known next step, not a contradiction. The README shows what Tokeo does and
this document carries the why.
