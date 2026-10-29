# Why Tokeo Is Built This Way

This document states what Tokeo is and the principles it is built on. It explains
*why* the pieces are the way they are, so a developer can tell what Tokeo will
and will not do for them, and what "opinionated" means here. The features are
described in the README.

Tokeo is an event-driven backend framework for Python. The governed AI runtime
is one capability on top of it and the one that sets Tokeo apart. This document
covers the whole, with the AI principles as one section among the rest.


## What Tokeo is

Tokeo is a CLI first framework for task automation, message queues and web
interfaces. It is built for Python backend projects in an event-driven
architecture (EDA) world, where you need a fast and reliable development cycle.
It fits both ends of the range: small enough that a quick prototype or a single
scheduled job is productive in minutes, and complete enough that the same
project grows into a large, multi-service backend without changing its shape.
You start small without painting yourself into a corner, and you scale up
without rebuilding.

The core has been in production use for years, across projects of very
different sizes.

Tokeo is an integrated stack, assembled from proven foundations and wired
together so they work as one. Rather than reinventing the wheel for background
processing, scheduling or CLI management, Tokeo integrates mature,
industry-standard projects. One of its capabilities is a governed AI runtime:
you build AI-assisted processes, agentic microservices, automated workflows or
prototypes, and the structure is already there.

Tokeo is assembled rather than invented because its value lies in making
existing parts a coherent whole, with one configuration model, one CLI and one
development cycle. You do not spend your first week wiring a broker to a
scheduler to a web layer.

A generated project is set up from the first minute. It already carries the
full developer toolchain: a ```Makefile``` with one-liners for formatting,
linting, testing (with coverage), building the docs and packaging. The inner
loop is set up before you write a line of your own. Configuration is
environment-based (prod, stage, dev, test), with secrets kept safe through the
vault, so real credentials are safe to work with from the start. Shipping works
the same way: the application deploys as a standard Python build (sdist, wheel)
right through to a Docker image, with one command to build and one to run. The
groundwork is done, so your energy goes into what you are building.


## Architecture principles

These convictions apply across the whole framework: to tasks, scheduling,
automation, web, secrets and the AI alike.

### Everything is configuration, in YAML

Tasks, automations, agents, profiles, schedules and secrets are declared in
```config/```, with ENV overrides on top. A system you can read is one you can
audit, diff, review and reproduce. That is why the agents and profiles of the AI
are plain YAML too and use no separate concept.

### The CLI is the surface, code is the entry point

Everything is reachable as a ```spiral <command>```, but the real integration
point is programmatic: a task, a scheduler job or a gRPC handler calling into
your logic. The CLI drives, inspects and tests; production runs call the
functions.

### Secrets stay encrypted at rest, transparent on read

The vault handler resolves ```!vault:```-tagged values at the leaf, on read, so
your YAML never holds a plaintext credential and consumer code never changes.
The encrypted path is the default and invisible path, which is what makes it
actually used.

### Local-first and sovereign

From the RabbitMQ broker to the AI provider, the parts run on your machine or
your infrastructure and talk over open protocols. A backend you cannot run
offline, inspect or own is a backend you have rented; Tokeo keeps the system in
your own hands.

### Transparency over convenience, where they conflict

Tokeo chooses inspectable states, explicit configuration and honest tiering over
hidden magic that "just works" until it does not. The cost of opaque automation
is paid later, at debugging time, by whoever did not write it; Tokeo pays it up
front, in visibility.

### Determinism is the target

The framework aims for reproducible, pattern-true behaviour: the same config and
the same input give the same run. An event-driven backend processing real work
cannot behave like a slot machine. Where a deterministic path exists, Tokeo
takes it. Variability is allowed only where it is the point, and for the AI only
in *which* function runs, never in whether the outcome is trustworthy.

### Assembled from the proven

Cement, Dramatiq, RabbitMQ, gRPC, APScheduler, Fabric and NiceGUI are mature
projects, and Tokeo integrates them. The opinion Tokeo adds is *how they fit
together*; it replaces none of them.


## What "opinionated" means here

Tokeo is opinionated, and it matters to be exact about *where*. Your domain,
your tasks, your tools, your models and your workflows are yours, and Tokeo has
no opinion on them. It is opinionated about the shape: how a thing is declared,
configured, made visible and governed.

The framework gives you one way to do the structural things, on purpose:

- One configuration model (YAML with ENV overrides) for every part, so learning
  one part teaches you the next.
- One project layout (```core/logic```, ```core/tasks```, ```core/ai```,
  ```controllers```, ```site```, ...), generated, so anyone who knows one Tokeo
  project finds their way in another.
- One development cycle (```make fmt```, ```lint```, ```test```, ```doc```,
  ```sdist```), so the inner loop is the same across projects.
- One way to add a building block, a named entry with a ```type``` and its
  settings, whether it is a task, an agent, a guard or a tool.

These opinions are deliberately narrow: they make the *scaffolding* uniform so
the *substance* is entirely yours.


## Orchestration and governance of the AI

Tokeo AI provides the governance to build AI-orchestrated processes that are
safe and verifiable: a model drives real tools transparently, deterministically
and under control. It is wired into a generated project and never into the
framework itself, and it activates nothing on its own.

### The AI orchestrates, the functions compute

The model orchestrates existing functions in many variants: it decides *which*
tool runs *when*. The work is done by your plain functions, in deterministic,
inspectable code. A language model is good at choosing and sequencing and bad
at being a source of truth; Tokeo uses it for the former and forbids it the
latter.

### Declare, then enforce

A description or a prompt influences a model and guarantees nothing, since a
model is free to ignore any text. Control comes from code that enforces. This is
why every tool call passes a guard pipeline (validate, policy, audit), and why a
guard grants the tool list a skill declares instead of trusting it because it
was written down.

### Normalize first, then govern

A tool call is reduced to one shape before the guards run, whether the model
emitted it natively or it was parsed from prose. The same rules then apply
regardless of provider or model. Governance that depends on the output format of
the model has holes.

### A guard decides, a sandbox contains

The two are never mixed: a guard authorizes, a sandbox is a dumb wall around
execution. Kept apart, you can read exactly what was allowed and exactly what
was contained.

### The trace is the single source of truth

Every object a run produces is a step on one ordered trace; the typed views
(messages, tool calls, results) are references into it and no copies. The trace
keeps "what the agent did" honest, with the same model pipeline from a local
micro model of a few megabytes up to any large provider.

### What Tokeo guarantees

Tokeo guarantees, deterministically, that a value returned by a tool is carried
into further processing exactly as returned, with no model modification. It
cannot guarantee that free model prose weaving several results is faithful, nor
that the model called the tool correctly. The promise covers the *value channel*
and the traceability and verifiability of the results.

### External capability is treated like a code dependency

A tool, a skill or a request is reviewed, pinned, audited and run under
containment instead of being trusted because it loaded. This is why skills are
declared and never discovered: a skill is part of the delivered application,
named in the config, and never picked up by dropping a folder on disk. Discovery
would make the capabilities of the agent depend on filesystem state instead of
the config, and break the auditability and determinism the whole framework is
built on. Tokeo adopts the portable agentskills.io format, mirrors it into its
own config and runs it through the same guards and sandbox.


## Framework versus application

Tokeo ships the mechanism; your application supplies the matter. The framework
can be upgraded without touching your logic, and your logic is never entangled
with framework internals.

The framework ships the base classes, the registries, the CLI, the extensions
and, for the AI, the loop, the guards, the deterministic mock provider, the
agent and the sandbox seam. Real models, real tools and showcase wiring live in
the generated template and in Spiral, the reference application. Spiral is
always a cleanly generated version of the template and never edited by hand, so
the framework is continuously proven against itself.

Extension points are provided ahead of need: a guard type with no implementation
yet, a typed exception per family, an empty default a future slice fills. A
framework is the place where *your* code attaches, and these points are that
place. Speculative *features* are not built.


## What Tokeo deliberately does not do

The boundaries are a position as much as the features:

- Tokeo does not overrun your proven infrastructure. It talks to a running
  RabbitMQ, a running LLM endpoint, a running database and all the other
  services you depend on. A container built with Docker may start a
  self-contained, run-only environment with volatile services, like a RabbitMQ
  for a "flow-through" app that only needs them while it runs.
- Tokeo does not carry a heavy provider abstraction for AI. It has a thin client
  over the OpenAI-compatible transport plus a built-in mock instead of a
  multi-provider matrix: one protocol covers most uses, and native providers are
  added only when actually needed.
- Tokeo does not bundle AI models in core. Core ships the deterministic mock
  only; any real model lives in Spiral, an extension or your project. A model is
  a heavy, opinionated dependency, and keeping it out keeps the framework light
  and provider-agnostic.


## How Tokeo evolves

Tokeo is built and maintained under strict rules. They keep the framework from
accumulating technical debt, half-built features or contradictory patterns over
time.

- When an architecture agreement or a convention changes, the change is carried
  through in full, also retroactively across existing code. No old patterns stay
  behind next to the new ones.
- Settled specifications and architectural conventions are never changed
  unilaterally or silently. If a constraint blocks progress, the design is
  questioned and renegotiated, never quietly bypassed.
- An extension point is provided ahead of need to keep the architecture open,
  but no speculative features are built. A feature that is added is built out
  completely, but only as far as the framework requires it.
- An observation is no order. Noticing and flagging a problem is welcome, but it
  gives no license to refactor outside the current scope. Changes stay focused,
  predictable and exact to what was asked.
- Every change, every trade-off and every side effect is named in full.
  Documentation states the what and the why for developers and users and never
  serves as a memory aid for the author.
- Tests check actual system behaviour and edge cases, never an idealized state,
  and are never written just to satisfy a coverage metric.
- Read twice, question the premise and verify before reporting. Nothing is
  called "done" until the checks pass.


## How to read this document

These principles are stable, and the code catches up to them slice by slice.
Where a principle and the current code disagree, the principle is the intent and
the gap is a known next step. The README shows what Tokeo does, and this
document carries the why.
