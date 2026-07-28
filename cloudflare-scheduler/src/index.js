const WORKFLOW_DISPATCH_URL =
  "https://api.github.com/repos/yagiharuka/innovation-news/actions/workflows/review-backlog.yml/dispatches";

async function dispatchBacklogReview(env) {
  if (!env.GITHUB_ACTIONS_TOKEN) {
    throw new Error("GITHUB_ACTIONS_TOKEN is not configured");
  }

  const response = await fetch(WORKFLOW_DISPATCH_URL, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "innovation-news-cloudflare-scheduler",
      "X-GitHub-Api-Version": "2026-03-10",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: {
        force: "false",
        review_limit: "16",
      },
    }),
  });

  if (!response.ok) {
    const responseBody = await response.text();
    throw new Error(
      `GitHub workflow dispatch failed: ${response.status} ${responseBody}`,
    );
  }

  console.log(
    JSON.stringify({
      event: "github_workflow_dispatched",
      scheduled_time: new Date().toISOString(),
      status: response.status,
    }),
  );
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatchBacklogReview(env));
  },
};
