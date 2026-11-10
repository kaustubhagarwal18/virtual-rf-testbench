# Development

The commands, checks and CI jobs behind the project. The README lists the three you need
most.

You need [uv](https://docs.astral.sh/uv/) and `make`. uv installs Python 3.13 for the project,
so it doesn't matter which Python your system has.

```
make setup
make lint
make test
```

## Commands

| Command | What it does |
|---|---|
| `make setup` | Installs Python 3.13 and the dependencies, and installs the pre-commit hooks |
| `make lint` | Runs ruff, mypy (strict), pylint, pydoclint (docstring Args, Returns and Raises), bandit and codespell, checks the Makefile with mbake and the Dockerfile with hadolint |
| `make fmt` | Formats the code and the Makefile, and applies safe lint fixes |
| `make test` | Runs the tests and fails if coverage is below 80%; writes `reports/junit.xml` and `reports/coverage.xml` |
| `make summary` | Prints a results summary from those files: tests run, passed, failed and skipped per suite, coverage, failures and the slowest tests |
| `make bench-up` / `bench-down` / `bench-logs` | Starts, stops or follows the containerised bench |
| `make bench-test` / `bench-summary` | Runs the bench suite against the containers, writing `reports/bench-junit.xml` and `reports/bench-results.json`, and prints its summary |
| `make fault NAME=… [SIZE=…]` | Runs the bench suite with one fault on and saves the run with its label in `results/faults/` |
| `BENCH_LEVEL_OFFSET_DB`, `BENCH_CHANNEL_BANDWIDTH_HZ` | The bench configuration the setup sequence applies; unset means a healthy bench |
| `make fault-dataset` | Rebuilds the triage data set in `triage/data/runs/`: every fault, three history runs and three question runs each |
| `make triage RUN=…` | Explains one saved run: names the likely cause and fix from its evidence alone, without retrieving past runs (needs a local Ollama model) |
| `make triage-demo RUN=…` | Triages one run with and without retrieval, side by side, to show what the past runs add (needs local Ollama answering and embedding models) |
| `make triage-api` | Serves triage over HTTP on localhost:8000: `POST /triage`, `POST /history`, `GET /health` (needs local Ollama answering and embedding models) |
| `make image` / `image-scan` | Builds the simulator image, or scans it with Trivy as CI does (needs Trivy installed) |
| `make eval [MODEL=…]` | Scores the triage against the labelled question runs and writes `triage/results/rag.md` (needs local Ollama answering and embedding models) |
| `make clean` | Removes the virtual environment, the tool caches and the pre-commit hook; `make setup` restores them |

The pre-commit hooks run `make lint` before every commit, so the same checks run locally and
in CI.

## Continuous integration

GitHub Actions runs on every pull request and on every push to `main`:

| Job | What it checks |
|---|---|
| Lint | Every pre-commit hook on all files: trailing whitespace, final newlines, YAML and TOML syntax, large files, merge conflict markers, then `make lint` |
| Test (Python 3.13) | `make test`, including the 80% coverage floor, then `make summary`, whose table appears on the run's summary page |
| Image (build and scan) | Builds the simulator image once and scans it with Trivy 0.76.1 (pinned, checksum-verified); fails on a fixable high or critical vulnerability |
| Bench (containers) | Loads that same image, `make bench-up BENCH_BUILD=--no-build`, `make bench-test`, then `make bench-summary` on the run's summary page; shows the simulator logs if anything failed |
| Publish image | On pushes to `main` only, once every other job passed: pushes the scanned image to GitHub Container Registry as `virtual-rf-testbench-simulators:<version>-<commit>` |
| Secret scan | gitleaks 8.30.1 over the full git history, including merge commits; the download is checksum-verified |
| Results comment | On pull requests, once the other jobs finish: posts one comment with the test and bench results, coverage, any failures and each job's result, and updates that same comment on every push |

Actions are pinned to commit SHAs, and dependencies install from `uv.lock` exactly
(`uv sync --locked`).
