# Contributing

Thanks for taking an interest. Issues and pull requests are welcome: bug reports, fixes,
new faults, better docs, or a port to real instruments.

## Before you start

- **Found a bug or have an idea?** Open an issue with the matching template first, so we can
  agree on the approach before you spend time on code.
- **Want to adapt the project to your own bench?** [docs/ADAPTING.md](docs/ADAPTING.md)
  covers your own failures, new faults and real instruments.
- **New to the code?** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how the pieces
  fit, and the [decision records](docs/adr/) explain why.

## Set up

You need git, `make`, [uv](https://docs.astral.sh/uv/) and Docker; the triage assistant also
needs [Ollama](https://ollama.com).

```
git clone https://github.com/kaustubhagarwal18/virtual-rf-testbench.git
cd virtual-rf-testbench
make setup
make lint
make test
```

`make setup` also installs the pre-commit hooks, which run `make lint` before every commit.

## Make a change

1. **Branch from an up-to-date `main`,** one branch per change.
2. **Write a failing test first,** then the code that makes it pass. New behaviour gets a
   focused unit test; a bug fix gets a test for the whole class of the bug, not one input.
3. **Keep the docs in step:** update the README, the guides and `CHANGELOG.md` in the same
   pull request as the code.
4. **Record a design choice** as a short ADR in `docs/adr/`.
5. **Run `make lint test`.** It must pass before a pull request is reviewed.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat(triage): …`, `fix(drivers): …`, `docs: …`). The subject says what changed; the body
says why, with real numbers when something was measured.

## What the checks expect

| Check | Expectation |
|---|---|
| ruff, ruff format | clean |
| mypy | strict, clean |
| pylint | 10/10 |
| pydoclint | every module, class and function has a Google-style docstring with Args, Returns and Raises; test functions need one line |
| bandit, codespell | clean |
| pytest | passes, with at least 80% coverage overall |

Fix the code rather than silencing a check. If a suppression is truly needed, scope it to
one line and say why.

## Project rules

These keep the bench honest and the results trustworthy:

- **Never swallow instrument errors.** The drivers check the instrument's status after every
  command and fail on any error ([ADR 0001](docs/adr/0001-rsinstrument-session-and-test-backend.md)).
- **Simulators identify as VirtualRF,** never as a real vendor's product, and the drivers
  accept only that identity.
- **Every instrument call has a timeout,** and long operations synchronise with `*OPC?`.
- **Every measurement records its value, limit and margin;** no bare asserts on readings.
- **Tests are independent:** fixtures reset both instruments, and no test depends on
  another's state.
- **Fault switches live on the admin ports,** never in the SCPI interface.
- **The triage service treats logs as untrusted data,** and the model gets no tools.
- **Report results as measured.** A score is a count with its interval, from `make eval`;
  say what was not verified.
- **No secrets in the repository.**

## Pull requests

Describe what changed and why, what you verified, what you didn't, and any follow-ups. CI
runs lint, the tests, the bench suite against the containers, an image scan and a secret
scan on every pull request.

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
