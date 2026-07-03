# Contributing

Thanks for your interest! This project is primarily a portfolio/reference
implementation, but contributions and suggestions are welcome.

## Development setup

```bash
pip install -e ".[dev]"
make test        # run the test suite
make lint        # ruff
python scripts/e2e_local.py   # no-Docker end-to-end simulation
```

## Ground rules

- **Keep flows thin, logic in Python.** Business logic belongs in the
  `controlplane` library (where it's unit-tested), not in Kestra YAML.
- **Add tests** for any new behaviour. The bar is: the E2E simulation and the
  full suite stay green.
- **Run `make lint` and `make test`** before opening a PR. CI runs lint →
  tests+coverage → E2E → flow-YAML validation → Docker build.
- **Stages stay stateless.** Hand off through the object store so every stage is
  independently retryable.

## Commit style

Conventional commits are appreciated: `feat:`, `fix:`, `docs:`, `test:`,
`refactor:`, `chore:`.

## Opening a PR

1. Fork & branch from `main`.
2. Make your change with tests.
3. Ensure `make lint test` passes and `python scripts/e2e_local.py` prints
   `E2E RESULT: ✅ PASS`.
4. Open the PR with a clear description of the change and its motivation.
