const el = (id) => document.getElementById(id);
const form = el("recommendForm");
const resultSection = el("resultSection");
const cards = el("cards");
const statusBox = el("statusBox");
const diagnostics = el("diagnostics");

const money = (value) => new Intl.NumberFormat("ru-RU").format(value) + " ₸";

function fillSelect(id, values) {
  const select = el(id);
  const existing = new Set([...select.options].map((o) => o.value));
  values.forEach((value) => {
    if (existing.has(value)) return;
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
}

async function loadMeta() {
  const res = await fetch("/api/meta");
  const meta = await res.json();
  fillSelect("city", meta.cities);
  fillSelect("eventFormat", meta.event_formats);
  fillSelect("category", meta.categories);
  fillSelect("language", meta.languages);
  el("eventDate").min = meta.date_min;
  el("eventDate").max = meta.date_max;
  el("eventDate").value = meta.date_min;
  el("profilesBadge").textContent = `${meta.profiles_count} профилей в датасете`;
}

function payloadFromForm() {
  const duration = el("duration").value;
  const language = el("language").value;
  const preferences = el("preferences").value.trim();
  return {
    city: el("city").value,
    event_date: el("eventDate").value,
    event_format: el("eventFormat").value,
    category: el("category").value,
    budget_kzt: Number(el("budget").value),
    duration_hours: duration ? Number(duration) : null,
    language: language || null,
    preferences: preferences || null,
  };
}

function renderCard(card) {
  const tags = [
    ...card.categories.map((x) => `<span class="tag">${x}</span>`),
    ...card.languages.map((x) => `<span class="tag">${x}</span>`),
    card.synthetic ? '<span class="tag synthetic">synthetic</span>' : "",
    card.city_imputed ? '<span class="tag imputed">city imputed</span>' : "",
    card.price_imputed ? '<span class="tag imputed">price imputed</span>' : "",
  ].join("");

  return `
    <article class="card">
      <div class="card-head">
        <div><h3>${card.name}</h3><div class="id">${card.id} · ${card.city}</div></div>
        <div class="price">от ${money(card.price_from_kzt)}</div>
      </div>
      <div class="tags">${tags}</div>
      <ol class="reasons">${card.reasons.map((r) => `<li>${r}</li>`).join("")}</ol>
      <p class="description">${card.description}</p>
      <div class="score">Детерминированный score: ${card.score.toFixed(4)}</div>
    </article>`;
}

function render(data) {
  resultSection.classList.remove("hidden");
  statusBox.className = "status";
  if (data.status === "no_match") statusBox.classList.add("no-match");
  if (data.status === "category_absent") statusBox.classList.add("absent");
  statusBox.textContent = data.message;
  cards.innerHTML = data.results.map(renderCard).join("");
  diagnostics.textContent = JSON.stringify(data.diagnostics, null, 2);
  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  el("requestState").textContent = "Подбираем…";
  try {
    const response = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadFromForm()),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(JSON.stringify(data));
    render(data);
  } catch (error) {
    statusBox.className = "status absent";
    statusBox.textContent = "Ошибка запроса: " + error.message;
    resultSection.classList.remove("hidden");
  } finally {
    el("requestState").textContent = "";
  }
});

const demos = {
  dense: {
    city: "Алматы", event_date: "2026-10-15", event_format: "корпоратив",
    category: "Ведущий", budget_kzt: 1500000, duration_hours: 6,
    language: "русский", preferences: "интеллигентный юмор импровизация современная подача"
  },
  rare: {
    city: "Алматы", event_date: "2026-10-15", event_format: "свадьба",
    category: "Флорист", budget_kzt: 500000, duration_hours: null,
    language: "русский", preferences: "авторское оформление цветы"
  },
  empty: {
    city: "Астана", event_date: "2026-12-31", event_format: "свадьба",
    category: "Ведущий", budget_kzt: 300000, duration_hours: 10,
    language: "казахский", preferences: null
  },
  absent: {
    city: "Астана", event_date: "2026-10-15", event_format: "корпоратив",
    category: "Декоратор", budget_kzt: 3000000, duration_hours: null,
    language: "русский", preferences: null
  },
};

function applyDemo(d) {
  el("city").value = d.city;
  el("eventDate").value = d.event_date;
  el("eventFormat").value = d.event_format;
  el("category").value = d.category;
  el("budget").value = d.budget_kzt;
  el("duration").value = d.duration_hours ?? "";
  el("language").value = d.language ?? "";
  el("preferences").value = d.preferences ?? "";
  form.requestSubmit();
}

document.querySelectorAll(".demo").forEach((button) => {
  button.addEventListener("click", () => applyDemo(demos[button.dataset.demo]));
});

loadMeta().catch((error) => {
  el("profilesBadge").textContent = "Не удалось загрузить метаданные";
  console.error(error);
});
