# Contributing to Tokeo

Thank you for considering a contribution. Issues, ideas, and pull requests are
welcome. Please read this page before you open one.

<br/>

## How this project is licensed

Tokeo is released under the **Apache License, Version 2.0**, see
```LICENSE.txt``` and ```NOTICE.md```. That will not change.

Contributions are covered by Section 5 of that license. Unless you state
otherwise, anything you intentionally submit for inclusion in Tokeo is
submitted under the Apache License, Version 2.0, with no further agreement
needed. You keep your copyright. No assignment is asked for, and none is
wanted.

Please confirm two things when you submit:

- The contribution is your own work, or you have the rights to submit it under
  the Apache License, Version 2.0.
- If your employer holds rights in your work, you have permission to
  contribute.

One part of the sources is separate. The ```akili``` module generated into
projects is Apache-2.0 as well, and its file headers and ```LICENSE.txt``` copy
must stay intact in any change you make there.

<br/>

## What we accept

**Keep the human in the loop.** We use AI as an exoskeleton, not a replacement.
Purely AI-generated issues or pull requests are not accepted. If AI helped you
write code or text, that is fine. You must have read it, understood it, and be
able to explain and defend it.

**Small and focused beats large and sweeping.** One concern per pull request.
A change that touches an extension should say in the description what it
changes about the behaviour that extension guarantees.

**Tests belong to the change.** New behaviour comes with tests. Changed
behaviour comes with changed tests. Tests must run without any external service.

**Documentation belongs to the change.** If a change affects how the runtime is
configured or used, update the relevant document in the same pull request.

<br/>

## Before you open a pull request

```bash
# Format the code
make fmt

# Run linting checks
make lint

# Run the test suite
make test
```

A pull request that does not pass ```make lint``` and ```make test``` will be
asked to do so before review.

<br/>

## Reporting security issues

Please do not open a public issue for a security problem. Write to
th.freudenberg@gmail.com with a description and, if possible, a way to
reproduce it. You will get an answer.

<br/>

## Questions

If something here is unclear, or the terms above do not fit your situation, ask
before you invest work. An issue or an email is enough.
