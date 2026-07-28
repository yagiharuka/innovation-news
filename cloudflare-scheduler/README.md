# Innovation News Cloudflare Scheduler

This Worker triggers the GitHub Actions backlog-review workflow every 30
minutes. GitHub's native schedule remains as a fallback until the Cloudflare
trigger has been verified.

## Required secret

Create a fine-grained GitHub personal access token restricted to
`yagiharuka/innovation-news` with:

- Repository permission: **Actions — Read and write**

Store it in the Worker as an encrypted secret named:

```text
GITHUB_ACTIONS_TOKEN
```

Never commit the token to this repository.

## Schedule

`wrangler.jsonc` configures the Worker to run at minute `00` and `30` of every
hour (UTC). Since Japan Standard Time has a whole-hour offset, it also runs at
minute `00` and `30` in Japan.

## GitHub request

Each invocation dispatches `.github/workflows/review-backlog.yml` on `main`
with a 16-item limit. The workflow's quota gate still prevents duplicate or
over-budget model requests.
