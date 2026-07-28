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

## Deploy from GitHub

1. In Cloudflare, open **Workers & Pages** and select **Create application**.
2. Under **Import a repository**, connect GitHub and select
   `yagiharuka/innovation-news`.
3. Set the Worker name to `innovation-news-scheduler`.
4. Set the production branch to `main` and the root directory to
   `cloudflare-scheduler`.
5. Keep the deploy command as `npx wrangler deploy`, then select
   **Save and Deploy**.
6. Open the deployed Worker and add the encrypted runtime secret
   `GITHUB_ACTIONS_TOKEN` under **Settings > Variables & Secrets**.

## Schedule

`wrangler.jsonc` configures the Worker to run at minute `00` and `30` of every
hour (UTC). Since Japan Standard Time has a whole-hour offset, it also runs at
minute `00` and `30` in Japan.

## GitHub request

Each invocation dispatches `.github/workflows/review-backlog.yml` on `main`
with a 16-item limit. The workflow's quota gate still prevents duplicate or
over-budget model requests.
