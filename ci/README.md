# CI configuration

`github-actions-ci.yml` is the GitHub Actions workflow for this project
(lint with ruff + run the pytest suite on every push / PR).

It lives here because the automation account that opened the initial PR did
not have GitHub's `workflows` permission (GitHub blocks apps from creating
workflow files without it). To enable CI, move this file into place:

```bash
mkdir -p .github/workflows
git mv ci/github-actions-ci.yml .github/workflows/ci.yml
git commit -m "ci: enable GitHub Actions workflow"
git push
```
