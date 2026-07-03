# CI configuration

`github-actions-ci.yml` is the GitHub Actions workflow for this project.

> **Why does it live here instead of `.github/workflows/`?**
> GitHub only *runs* workflows from `.github/workflows/`, but the automation
> account used to open PRs here cannot create files under that path (GitHub
> blocks apps without the `workflows` permission). So the workflow ships in
> `ci/` and **you** — the repo owner, who *does* have the permission — enable it
> with one command:

```bash
mkdir -p .github/workflows
git mv ci/github-actions-ci.yml .github/workflows/ci.yml
git commit -m "ci: enable GitHub Actions workflow"
git push
```

Once moved, the CI badge in the README goes green. The pipeline runs on every
push to `main` / `genspark_ai_developer` and on every PR into `main`:

| Job | What it does |
| --- | --- |
| `lint` | `ruff check controlplane/ services/ tests/` |
| `test` | `pytest` (79 tests) with coverage |
| `e2e` | `python scripts/e2e_local.py` — full lifecycle, no Docker |
| `validate-flows` | static validation of every Kestra flow YAML |
| `docker-build` | builds the gateway image to catch Dockerfile regressions |
