"use strict";

const PAGE_SIZE = 20;
const RETIRED_SOURCE_NAMES = new Set(["OpenAI News"]);

const LABELS = {
  "Technology Innovation": "技術",
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
  "News & Official Release": "ニュース・公式発表",
  "Journal Article": "学術誌論文",
  "Conference Paper": "学会・会議論文",
  Preprint: "プレプリント",
  "Journal record — confirm peer review on publisher page": "掲載誌で査読状況を要確認",
  "Conference proceedings — review status varies": "会議により査読状況が異なります",
  "Not peer reviewed": "未査読",
};

const SOURCE_GROUPS = [
  {
    value: "public",
    label: "政府系機関（各国政府・政府間機関）",
    sourceTypes: ["Government", "Intergovernmental"],
  },
  {
    value: "company",
    label: "事業会社（公式情報）",
    sourceTypes: ["Official Company"],
  },
  {
    value: "research",
    label: "非政府調査・研究機関",
    sourceTypes: ["Policy Institute"],
  },
  {
    value: "membership",
    label: "会員制団体（業界・専門・標準化）",
    sourceTypes: ["Industry Association"],
  },
  {
    value: "academic",
    label: "学術情報（大学・論文誌・論文DB）",
    sourceTypes: [
      "Scientific Publication",
      "Journal Article",
      "Conference Paper",
      "Preprint",
    ],
  },
  {
    value: "media",
    label: "報道機関",
    sourceTypes: ["Major Media"],
  },
];

// Some legacy feed types describe the publication format rather than the
// publisher. These overrides keep every source in one primary source class.
const SOURCE_GROUP_BY_SOURCE = new Map([
  ["JST CRDS STI Policy Reports", "public"],
  ["KISTEP", "public"],
  ["STEPI", "public"],
  ["Technology Innovation Institute", "public"],
  ["Science Japan", "public"],
  ["日本人工知能学会", "membership"],
  ["Japan Space Systems", "research"],
]);

const SOURCE_GROUP_BY_TYPE = new Map(
  SOURCE_GROUPS.flatMap((group) =>
    group.sourceTypes.map((sourceType) => [sourceType, group.value])
  )
);

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
  academicKind: document.querySelector("#academic-kind"),
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

function academicKind(item) {
  return item.academic_kind || "News & Official Release";
}

function sourceGroup(item) {
  return (
    SOURCE_GROUP_BY_SOURCE.get(item.source) ||
    SOURCE_GROUP_BY_TYPE.get(item.source_type) ||
    "other"
  );
}

function sourceGroupLabel(value) {
  return SOURCE_GROUPS.find((group) => group.value === value)?.label || "その他";
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

function populateSourceGroupSelect(items) {
  const presentGroups = new Set(items.map(sourceGroup));
  const groups = SOURCE_GROUPS.filter((group) => presentGroups.has(group.value));
  if (presentGroups.has("other")) {
    groups.push({ value: "other", label: "その他" });
  }

  const fragment = document.createDocumentFragment();
  for (const group of groups) {
    const option = document.createElement("option");
    option.value = group.value;
    option.textContent = group.label;
    fragment.append(option);
  }
  elements.sourceType.append(fragment);
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

  meta.append(region, date, source);

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
  if (academicKind(item) !== "News & Official Release") {
    tags.append(tag(displayLabel(academicKind(item)), "tag--academic"));
    if (item.review_status) {
      tags.append(tag(displayLabel(item.review_status), "tag--academic-status"));
    }
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
  const selectedAcademicKind = elements.academicKind.value;
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
        sourceGroup(item),
        sourceGroupLabel(sourceGroup(item)),
        academicKind(item),
        displayLabel(academicKind(item)),
        item.review_status,
        item.venue,
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
      (!sourceType || sourceGroup(item) === sourceType) &&
      (!selectedAcademicKind || academicKind(item) === selectedAcademicKind) &&
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
    elements.academicKind,
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
    elements.academicKind.value = "";
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

const DATA_SOURCES = ["./data/news-lite.json", "./data/news.json"];
const RETRY_DELAYS_MS = [0, 800, 2000];

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function fetchNewsPayload() {
  let lastError = new Error("記事データを取得できませんでした");
  for (const source of DATA_SOURCES) {
    for (let attempt = 0; attempt < RETRY_DELAYS_MS.length; attempt += 1) {
      if (RETRY_DELAYS_MS[attempt]) await wait(RETRY_DELAYS_MS[attempt]);
      try {
        const separator = source.includes("?") ? "&" : "?";
        const response = await fetch(`${source}${separator}attempt=${attempt}`, {
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`${source}: HTTP ${response.status}`);
        return await response.json();
      } catch (error) {
        lastError = error;
      }
    }
  }
  throw lastError;
}

async function loadData() {
  try {
    elements.updatedAt.textContent = "記事データを読み込んでいます";
    elements.feedNote.textContent = "通信状況により数秒かかる場合があります";
    const payload = await fetchNewsPayload();
    const items = Array.isArray(payload.items) ? payload.items : [];
    state.items = items.filter((item) => !RETIRED_SOURCE_NAMES.has(item.source));
    elements.articleCount.textContent = state.items.length.toLocaleString("ja-JP");
    elements.sourceCount.textContent = Number(payload.source_count || 0).toLocaleString("ja-JP");
    elements.updatedAt.textContent = payload.updated_at_jst
      ? `${formatDate(payload.updated_at_jst, true)} 更新`
      : "初回収集待ち";

    populateSelect(elements.region, optionValues(state.items, (item) => [item.region]));
    populateSelect(elements.frame, optionValues(state.items, (item) => item.article_frames || []));
    populateSelect(elements.topic, optionValues(state.items, (item) => item.topics || []));
    populateSelect(elements.policyArea, optionValues(state.items, (item) => item.policy_areas || []));
    populateSourceGroupSelect(state.items);
    populateSelect(elements.academicKind, optionValues(state.items, (item) => [academicKind(item)]));
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
