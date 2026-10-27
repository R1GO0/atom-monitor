const TOPICS = {
  construction: "Строительство",
  reactors: "Реакторы",
  safety: "Безопасность",
  fuel: "Топливо",
  economy: "Экономика",
  international: "Международка",
};

const state = { articles: [], sources: [], language: "all", query: "", loaded: false };
const themeToggle = document.querySelector("#theme-toggle");
const filterPanel = document.querySelector(".filters");
const filterToggle = document.querySelector("#filter-toggle");
const filterControls = document.querySelector("#filter-controls");
const backToTop = document.querySelector("#back-to-top");
const elements = {
  search: document.querySelector("#search-input"),
  source: document.querySelector("#source-filter"),
  topic: document.querySelector("#topic-filter"),
  date: document.querySelector("#date-filter"),
  feed: document.querySelector("#feed-list"),
  empty: document.querySelector("#empty-state"),
  emptyMessage: document.querySelector("#empty-message"),
  results: document.querySelector("#result-count"),
  total: document.querySelector("#total-count"),
  sourceCount: document.querySelector("#source-count"),
  updated: document.querySelector("#updated-at"),
  sources: document.querySelector("#source-list"),
  export: document.querySelector("#export-button"),
  exportCount: document.querySelector("#export-count"),
};

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function applyTheme(theme, persist = false) {
  const isDark = theme === "dark";
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  themeToggle.setAttribute("aria-pressed", String(isDark));
  themeToggle.setAttribute("aria-label", isDark ? "Включить светлую тему" : "Включить тёмную тему");
  themeToggle.title = isDark ? "Включить светлую тему" : "Включить тёмную тему";
  document.querySelector('meta[name="theme-color"]').content = isDark ? "#0e1510" : "#f3f5f1";
  if (persist) {
    try { localStorage.setItem("atom-monitor-theme", isDark ? "dark" : "light"); } catch { /* Storage may be disabled. */ }
  }
}

let savedTheme = "light";
try { savedTheme = localStorage.getItem("atom-monitor-theme") || "light"; } catch { /* Use the light theme when storage is unavailable. */ }
applyTheme(savedTheme === "dark" ? "dark" : "light");
themeToggle.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
});

const heroArt = document.querySelector(".hero-art");
if (window.matchMedia("(hover: hover) and (pointer: fine)").matches
    && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  window.addEventListener("pointermove", (event) => {
    const x = event.clientX / window.innerWidth - 0.5;
    const y = event.clientY / window.innerHeight - 0.5;
    heroArt.style.setProperty("--pointer-x", `${(x * 52).toFixed(1)}px`);
    heroArt.style.setProperty("--pointer-y", `${(y * 38).toFixed(1)}px`);
  }, { passive: true });
  window.addEventListener("blur", () => {
    heroArt.style.setProperty("--pointer-x", "0px");
    heroArt.style.setProperty("--pointer-y", "0px");
  }, { passive: true });
}

function setFilterPanelCollapsed(collapsed) {
  filterPanel.classList.toggle("is-collapsed", collapsed);
  filterControls.hidden = collapsed;
  filterToggle.setAttribute("aria-expanded", String(!collapsed));
  filterToggle.textContent = collapsed ? "Показать" : "Свернуть";
}

const mobileFilterQuery = window.matchMedia("(max-width: 800px)");
if (mobileFilterQuery.matches) setFilterPanelCollapsed(true);
filterToggle.addEventListener("click", () => {
  setFilterPanelCollapsed(filterToggle.getAttribute("aria-expanded") === "true");
});
mobileFilterQuery.addEventListener("change", (event) => {
  setFilterPanelCollapsed(event.matches);
});

function updateBackToTop() {
  backToTop.hidden = window.scrollY < 480;
}
window.addEventListener("scroll", updateBackToTop, { passive: true });
backToTop.addEventListener("click", () => {
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  window.scrollTo({ top: 0, behavior });
});
updateBackToTop();

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Дата не указана";
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", year: "numeric" }).format(date).replace(" г.", "");
}

function formatUpdated(value) {
  if (!value) return "нет данных";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "нет данных";
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}

function makeNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fillSources() {
  elements.source.replaceChildren(new Option("Все источники", "all"));
  const sourceNames = new Map();
  state.sources.forEach((source) => {
    if (!source?.id || !source?.name) return;
    const count = Number(source.article_count) || 0;
    const stateLabel = count
      ? ` — ${count}`
      : source.status === "unavailable"
        ? " — временно недоступен"
        : " — нет свежих материалов";
    sourceNames.set(source.id, { ...source, count });
    elements.source.add(new Option(`${source.name}${stateLabel}`, source.id));
  });

  elements.sources.replaceChildren();
  const uniqueSources = [...new Map([...sourceNames.values()].map((source) => [source.name, source])).values()];
  if (!uniqueSources.length) {
    elements.sources.append(makeNode("span", "source-placeholder", "Источники появятся после первого обновления"));
    return;
  }
  uniqueSources.forEach((source) => {
    const suffix = source.count
      ? ` · ${source.count}`
      : source.status === "unavailable"
        ? " · временно недоступен"
        : " · нет свежих";
    const node = makeNode("span", `source-name${source.count ? "" : " source-name-idle"}`, `${source.name}${suffix}`);
    if (source.error) node.title = source.error;
    elements.sources.append(node);
  });
}

function filteredArticles() {
  const source = elements.source.value;
  const topic = elements.topic.value;
  const days = elements.date.value === "all" ? null : Number(elements.date.value);
  const cutoff = days ? Date.now() - days * 24 * 60 * 60 * 1000 : null;
  const query = state.query.trim().toLocaleLowerCase("ru");

  return state.articles.filter((article) => {
    if (source !== "all" && article.source_id !== source) return false;
    if (topic !== "all" && article.topic !== topic) return false;
    if (state.language !== "all" && article.language !== state.language) return false;
    const published = new Date(article.published_at).getTime();
    if (cutoff && (!Number.isFinite(published) || published < cutoff)) return false;
    if (query && !`${article.title || ""} ${article.summary || ""} ${article.source || ""}`.toLocaleLowerCase("ru").includes(query)) return false;
    return true;
  }).sort((a, b) => new Date(b.published_at) - new Date(a.published_at));
}

function renderArticle(article, index) {
  const card = makeNode("article", "news-card");
  card.style.animationDelay = `${Math.min(index, 7) * 35}ms`;
  const content = makeNode("div", "card-content");
  const topline = makeNode("div", "card-topline");
  topline.append(makeNode("span", "topic-chip", TOPICS[article.topic] || "Международка"));
  topline.append(makeNode("span", "language-chip", (article.language || "ru").toUpperCase()));
  content.append(topline);

  const href = safeUrl(article.url);
  const title = makeNode("a", "news-title", article.title || "Материал без заголовка");
  if (href) {
    title.href = href;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
  } else {
    title.removeAttribute("href");
    title.setAttribute("aria-disabled", "true");
  }
  content.append(title);

  const summary = makeNode("p", "news-summary", article.summary || "Краткое описание не передано источником.");
  content.append(summary);
  if (href) {
    const original = makeNode("a", "original-link");
    original.href = href;
    original.target = "_blank";
    original.rel = "noopener noreferrer";
    original.append(makeNode("span", "", "↗"), document.createTextNode("Читать оригинал"));
    content.append(original);
  }

  const source = makeNode("div", "card-source");
  source.append(makeNode("strong", "", article.source || "Источник"));
  const time = makeNode("time", "", formatDate(article.published_at));
  time.dateTime = article.published_at || "";
  source.append(time);
  card.append(content, source);
  return card;
}

function render() {
  if (!state.loaded) return;
  const articles = filteredArticles();
  elements.feed.replaceChildren();
  elements.feed.setAttribute("aria-busy", "false");
  elements.results.textContent = `${articles.length} ${pluralize(articles.length, "материал", "материала", "материалов")}`;
  elements.exportCount.textContent = String(articles.length);
  elements.export.disabled = articles.length === 0;
  elements.empty.hidden = articles.length > 0;
  elements.feed.hidden = articles.length === 0;

  if (!articles.length) {
    elements.emptyMessage.textContent = state.articles.length
      ? "По этому запросу ничего не нашлось. Измените условия или очистите фильтры."
      : "Лента наполнится после первого успешного сбора материалов из открытых источников.";
    return;
  }
  articles.forEach((article, index) => elements.feed.append(renderArticle(article, index)));
}

function pluralize(count, one, few, many) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function resetFilters() {
  elements.search.value = "";
  elements.source.value = "all";
  elements.topic.value = "all";
  elements.date.value = "all";
  state.query = "";
  state.language = "all";
  document.querySelectorAll(".segment").forEach((button) => {
    const active = button.dataset.language === "all";
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  render();
}

function downloadCsv() {
  const articles = filteredArticles();
  if (!articles.length) return;
  const rows = [["Заголовок", "Дата", "Источник", "Тема", "Язык", "Описание", "Ссылка"]];
  articles.forEach((article) => rows.push([
    article.title || "",
    article.published_at || "",
    article.source || "",
    TOPICS[article.topic] || "Международка",
    article.language || "",
    article.summary || "",
    article.url || "",
  ]));
  const csv = "\uFEFF" + rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(";")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `atom-monitor-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

async function loadFeed() {
  try {
    const response = await fetch("data/news.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Ошибка загрузки: ${response.status}`);
    const payload = await response.json();
    state.articles = Array.isArray(payload.articles) ? payload.articles : [];
    state.sources = Array.isArray(payload.sources) ? payload.sources : [];
    fillSources();
    elements.total.textContent = new Intl.NumberFormat("ru-RU").format(state.articles.length);
    const activeSources = state.sources.filter((source) => Number(source.article_count) > 0).length;
    elements.sourceCount.textContent = new Intl.NumberFormat("ru-RU").format(activeSources);
    elements.updated.textContent = formatUpdated(payload.generated_at);
    state.loaded = true;
    render();
  } catch (error) {
    state.sources = [];
    fillSources();
    elements.total.textContent = "0";
    elements.sourceCount.textContent = "0";
    elements.updated.textContent = "нет данных";
    state.loaded = true;
    elements.emptyMessage.textContent = "Не удалось загрузить файл ленты. Запустите сайт через локальный веб-сервер или проверьте публикацию.";
    render();
    console.error("Не удалось загрузить ленту новостей:", error);
  }
}

elements.search.addEventListener("input", (event) => { state.query = event.target.value; render(); });
[elements.source, elements.topic, elements.date].forEach((select) => select.addEventListener("change", render));
document.querySelectorAll(".segment").forEach((button) => button.addEventListener("click", () => {
  state.language = button.dataset.language;
  document.querySelectorAll(".segment").forEach((item) => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  render();
}));
document.querySelector("#reset-filters").addEventListener("click", resetFilters);
document.querySelector("#empty-reset").addEventListener("click", resetFilters);
elements.export.addEventListener("click", downloadCsv);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.activeElement === elements.search) {
    elements.search.value = "";
    state.query = "";
    render();
  }
});

loadFeed();
