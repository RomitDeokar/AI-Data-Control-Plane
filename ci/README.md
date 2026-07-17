# CI configuration

The GitHub Actions workflow is provided here as a ready-to-use template:
[`github-actions-ci.yml`](./github-actions-ci.yml).

To enable it, copy the file into the workflows directory and commit it:

```bash
mkdir -p .github/workflows
cp ci/github-actions-ci.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "ci: enable GitHub Actions workflow"
git push
```

> It is kept under `ci/` (rather than committed directly to
> `.github/workflows/`) because the automation account that authored this
> change does not hold the GitHub `workflows` permission. Adding the file
> from your own account — which does — activates CI in one step.

Once enabled it runs automatically on every push to
`main` / `genspark_ai_developer` and on every PR into `main`.

| Job | What it does |
| --- | --- |
| `lint` | `ruff check controlplane/ services/ tests/` |
| `typecheck` | `mypy` over the `controlplane` package |
| `test` | `pytest` with coverage |
| `e2e` | `python scripts/e2e_local.py` — full lifecycle, no Docker |
| `validate-flows` | static validation of every Kestra flow YAML |
| `docker-build` | builds the gateway image to catch Dockerfile regressions |
