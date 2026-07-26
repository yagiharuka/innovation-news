"use strict";

const PAGE_SIZE = 20;

const LABELS = {
  "Technology Innovation": "技術イノベーション",
  "Innovation Policy": "イノベーション政策",
  "Artificial Intelligence": "AI",
  Robotics: "ロボット",
  "Semiconductors & Telecom": "半導体・通信",
  Quantum: "量子",
  "Fusion Energy": "フュージョンエネルギー",
  Biotechnology: "バイオ",
  Healthcare: "ヘルスケア",
  Space: "宇宙",
  "R&D Funding & Tax Incentives": "研究開発資金・税制",
  "National Programs & Strategy": "国家プロジェクト・戦略",
  "Patents & Intellectual Property": "特許・知財",
  "Regulation & Governance": "規制・ガバナンス",
  "Standards & Safety": "標準・安全",
  "Public Procurement & Industrial Policy": "政府調達・産業政策",
  "Research System & Talent": "研究システム・人材",
};

const state = {
  items: [],
  filtered: [],
  page: 1,
};

const elements = {
  articles: document.querySelector("#articles"),
  articleCount: document.querySelector("#article-count"),
  sourceCount: document.querySelector("#source-count"),
  visibleCount: document.querySelector("#visible-count"),
  updatedAt: document.querySelector("#updated-at"),
  feedNote: document.querySelector("#feed-note"),
  search: document.querySelector("#search"),
  region: document.querySelector("#region"),
  frame: document.querySelector("#frame"),
  topic: document.querySelector("#topic"),
  policyArea: document.querySelector("#policy-area"),
  sourceType: document.querySelector("#source-type"),
  policyScore: document.querySelector("#policy-score"),
  sortOrder: document.querySelector("#sort-order"),
  reset: document.querySelector("#reset-filters"),
  previous: document.querySelector("#previous-page"),
  next: document.querySelector("#next-page"),
  pageStatus: document.querySelector("#page-status"),
};

function text(value) {
  return String(value ?? "");
}

function normalized(value) {
  return text(value).normalize("NFKC").toLocaleLowerCase("ja");
}

function displayLabel(value) {
  return LABELS[value] || value;
}

function formatDate(value, withTime = false) {
  if (!value) return "未更新";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return text(value);
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "long",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function optionValues(items, getter) {
  return [...new Set(items.flatMap(getter).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, "ja")
  );
}

function populateSelect(select, values) {
  const fragment = document.createDocumentFragment();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = displayLabel(value);
    fragment.append(option);
  }
  select.append(fragment);
}

function tag(label, modifier = "") {
  const span = document.createElement("span");
  span.className = `tag ${modifier}`.trim();
  span.textContent = label;
  return span;
}

function articleCard(item) {
  const article = document.createElement("article");
  article.className = "article";

  const meta = document.createElement("div");
  meta.className = "article__meta";

  const region = document.createElement("p");
  region.className = "article__region";
  region.textContent = item.region || "Global";

  const date = document.createElement("p");
  date.textContent = formatDate(item.published_at);

  const source = document.createElement("p");
  source.className = "article__source";
  source.textContent = item.source || item.organization || "情報源不明";

  const sourceType = document.createElement("p");
  sourceType.textContent = item.source_type || "";
  meta.append(region, date, source, sourceType);

  const body = document.createElement("div");
  const title = document.createElement("h3");
  const link = document.createElement("a");
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = item.title || "無題";
  title.append(link);

  const summary = document.createElement("p");
  summary.className = "article__summary";
  summary.textContent = item.summary || "要約はありません。原文をご確認ください。";

  const tags = document.createElement("div");
  tags.className = "tags";
  for (const frame of item.article_frames || []) {
    tags.append(
      tag(
        displayLabel(frame),
        frame === "Innovation Policy" ? "tag--policy" : "tag--frame"
      )
    );
  }
  for (const topic of item.topics || []) {
    tags.append(tag(displayLabel(topic)));
  }
  for (const area of item.policy_areas || []) {
    tags.append(tag(`政策：${displayLabel(area)}`, "tag--policy"));
  }
  tags.append(tag(`政策関連度 ${Number(item.policy_relevance || 0)}/5`, "tag--policy"));

  body.append(title, summary, tags);
  article.append(meta, body);
  return article;
}

function renderEmpty(title, message) {
  const empty = document.createElement("div");
  empty.className = "empty";
  const strong = document.createElement("strong");
  strong.textContent = title;
  const detail = document.createElement("span");
  detail.textContent = message;
  empty.append(strong, detail);
  elements.articles.replaceChildren(empty);
}

function render() {
  const totalPages = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
  state.page = Math.min(Math.max(1, state.page), totalPages);
  const start = (state.page - 1) * PAGE_SIZE;
  const pageItems = state.filtered.slice(start, start + PAGE_SIZE);

  if (!pageItems.length) {
    const hasData = state.items.length > 0;
    renderEmpty(
      hasData ? "該当する記事はありません" : "初回収集を待っています",
      hasData
        ? "条件を変更するか、フィルターをリセットしてください。"
        : "GitHub Actionsの初回実行後、ここに記事が表示されます。"
    );
  } else {
    const fragment = document.createDocumentFragment();
    for (const item of pageItems) fragment.append(articleCard(item));
    elements.articles.replaceChildren(fragment);
  }

  elements.articles.setAttribute("aria-busy", "false");
  elements.visibleCount.textContent = state.filtered.length.toLocaleString("ja-JP");
  elements.feedNote.textContent = `${state.filtered.length.toLocaleString("ja-JP")}件を表示対象にしています`;
  elements.pageStatus.textContent = `${state.page} / ${totalPages}`;
  elements.previous.disabled = state.page <= 1;
  elements.next.disabled = state.page >= totalPages;
}

function applyFilters(resetPage = true) {
  const query = normalized(elements.search.value).trim();
  const region = elements.region.value;
  const frame = elements.frame.value;
  const topic = elements.topic.value;
  const policyArea = elements.policyArea.value;
  const sourceType = elements.sourceType.value;
  const minimumPolicy = Number(elements.policyScore.value);
  const sortOrder = elements.sortOrder.value;

  state.filtered = state.items.filter((item) => {
    const haystack = normalized(
      [
        item.title,
        item.title_original,
        item.summary,
        item.summary_original,
        item.source,
        item.organization,
        item.country,
        item.region,
        ...(item.article_frames || []).flatMap((value) => [value, displayLabel(value)]),
        ...(item.topics || []).flatMap((value) => [value, displayLabel(value)]),
        ...(item.policy_areas || []).flatMap((value) => [value, displayLabel(value)]),
      ].join(" ")
    );
    return (
      (!query || haystack.includes(query)) &&
      (!region || item.region === region) &&
      (!frame || (item.article_frames || []).includes(frame)) &&
      (!topic || (item.topics || []).includes(topic)) &&
      (!policyArea || (item.policy_areas || []).includes(policyArea)) &&
      (!sourceType || item.source_type === sourceType) &&
      Number(item.policy_relevance || 0) >= minimumPolicy
    );
  });

  state.filtered.sort((a, b) => {
    if (sortOrder === "policy") {
      return (
        Number(b.policy_relevance || 0) - Number(a.policy_relevance || 0) ||
        text(b.published_at).localeCompare(text(a.published_at))
      );
    }
    if (sortOrder === "source") {
      return text(a.source).localeCompare(text(b.source), "ja");
    }
    return text(b.published_at).localeCompare(text(a.published_at));
  });

  if (resetPage) state.page = 1;
  render();
}

function bindControls() {
  for (const control of [
    elements.search,
    elements.region,
    elements.frame,
    elements.topic,
    elements.policyArea,
    elements.sourceType,
    elements.policyScore,
    elements.sortOrder,
  ]) {
    control.addEventListener(control === elements.search ? "input" : "change", () =>
      applyFilters(true)
    );
  }

  elements.reset.addEventListener("click", () => {
    elements.search.value = "";
    elements.region.value = "";
    elements.frame.value = "";
    elements.topic.value = "";
    elements.policyArea.value = "";
    elements.sourceType.value = "";
    elements.policyScore.value = "0";
    elements.sortOrder.value = "newest";
    applyFilters(true);
  });

  elements.previous.addEventListener("click", () => {
    state.page -= 1;
    render();
    document.querySelector("#feed-title").scrollIntoView({ behavior: "smooth" });
  });

  elements.next.addEventListener("click", () => {
    state.page += 1;
    render();
    document.querySelector("#feed-title").scrollIntoView({ behavior: "smooth" });
  });
}

async function loadData() {
  try {
    const response = await fetch("./data/news.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.items = Array.isArray(payload.items) ? payload.items : [];
    elements.articleCount.textContent = Number(payload.article_count || state.items.length).toLocaleString("ja-JP");
    elements.sourceCount.textContent = Number(payload.source_count || 0).toLocaleString("ja-JP");
    elements.updatedAt.textContent = payload.updated_at_jst
      ? `${formatDate(payload.updated_at_jst, true)} 更新`
      : "初回収集待ち";

    populateSelect(elements.region, optionValues(state.items, (item) => [item.region]));
    populateSelect(elements.frame, optionValues(state.items, (item) => item.article_frames || []));
    populateSelect(elements.topic, optionValues(state.items, (item) => item.topics || []));
    populateSelect(elements.policyArea, optionValues(state.items, (item) => item.policy_areas || []));
    populateSelect(elements.sourceType, optionValues(state.items, (item) => [item.source_type]));
    applyFilters(true);
  } catch (error) {
    console.error(error);
    elements.articleCount.textContent = "—";
    elements.sourceCount.textContent = "—";
    elements.visibleCount.textContent = "—";
    elements.updatedAt.textContent = "データを取得できませんでした";
    elements.feedNote.textContent = "しばらくしてから再読み込みしてください";
    elements.articles.setAttribute("aria-busy", "false");
    renderEmpty("読み込みに失敗しました", "Excel台帳は上部のボタンから確認できます。");
  }
}

bindControls();
loadData();
