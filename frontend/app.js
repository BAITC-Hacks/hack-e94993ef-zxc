const el = (id) => document.getElementById(id);
const form = el("recommendForm");
const resultSection = el("resultSection");
const emptyState = el("emptyState");
const cards = el("cards");
const statusBox = el("statusBox");
const diagnostics = el("diagnostics");
const submitButton = el("submitButton");
const planBSection = el("planBSection");
const planBOptions = el("planBOptions");

let latestPlanB = [];

const money = (value) => new Intl.NumberFormat("ru-RU").format(value) + " ₸";

function candidateWord(count) {
  const last = count % 10;
  const lastTwo = count % 100;
  if (last === 1 && lastTwo !== 11) return "вариант";
  if ([2, 3, 4].includes(last) && ![12, 13, 14].includes(lastTwo)) return "варианта";
  return "вариантов";
}

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
  el("profilesBadge").textContent = `${meta.profiles_count} специалистов доступны`;
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

function renderCard(card, index) {
  const tags = [
    ...card.categories.map((x) => `<span class="tag">${x}</span>`),
    ...card.languages.map((x) => `<span class="tag">${x}</span>`),
    card.synthetic ? '<span class="tag synthetic">синтетический профиль</span>' : "",
    card.city_imputed ? '<span class="tag imputed">город дополнен</span>' : "",
    card.price_imputed ? '<span class="tag imputed">цена дополнена</span>' : "",
  ].join("");

  return `
    <article class="card">
      <div class="card-top">
        <div>
          <div class="rank">Вариант ${index + 1}</div>
          <h3>${card.name}</h3>
          <div class="card-meta">${card.city} · ${card.id}</div>
        </div>
        <div class="price">
          <small>Стоимость от</small>
          <strong>${money(card.price_from_kzt)}</strong>
        </div>
      </div>

      <div class="tags">${tags}</div>

      <div class="match-box">
        <div class="match-title">Почему подходит</div>
        <ul class="reasons">${card.reasons.map((r) => `<li>${r}</li>`).join("")}</ul>
      </div>

      <p class="description">${card.description}</p>
    </article>`;
}

function planBIcon(kind) {
  const icons = {
    date: "↔",
    budget: "+",
    language: "文",
    duration: "◷",
    city: "⌖",
    combined: "↗",
  };
  return icons[kind] || "↗";
}

function renderPlanB(options = []) {
  latestPlanB = options;
  if (!options.length) {
    planBSection.classList.add("hidden");
    planBOptions.innerHTML = "";
    return;
  }

  planBSection.classList.remove("hidden");
  planBOptions.innerHTML = options.map((option, index) => `
    <article class="plan-b-option">
      <div class="plan-b-option-icon" aria-hidden="true">${planBIcon(option.kind)}</div>
      <div class="plan-b-option-copy">
        <div class="plan-b-option-top">
          <h4>${option.title}</h4>
          <span class="gain">+${option.gain}</span>
        </div>
        <p>${option.description}</p>
        <div class="plan-b-impact">
          После изменения: <strong>${option.candidate_count} ${candidateWord(option.candidate_count)}</strong>
        </div>
      </div>
      <button type="button" class="apply-plan-b" data-plan-b-index="${index}">Применить</button>
    </article>
  `).join("");
}

function applyPlanB(option) {
  const patch = option.patch || {};
  if (Object.prototype.hasOwnProperty.call(patch, "city")) el("city").value = patch.city ?? "";
  if (Object.prototype.hasOwnProperty.call(patch, "event_date")) el("eventDate").value = patch.event_date ?? "";
  if (Object.prototype.hasOwnProperty.call(patch, "budget_kzt")) el("budget").value = patch.budget_kzt ?? "";
  if (Object.prototype.hasOwnProperty.call(patch, "language")) el("language").value = patch.language ?? "";
  if (Object.prototype.hasOwnProperty.call(patch, "duration_hours")) el("duration").value = patch.duration_hours ?? "";
  form.requestSubmit();
}

planBOptions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-plan-b-index]");
  if (!button) return;
  const option = latestPlanB[Number(button.dataset.planBIndex)];
  if (option) applyPlanB(option);
});

function render(data) {
  emptyState.classList.add("hidden");
  resultSection.classList.remove("hidden");
  statusBox.className = "status";

  if (data.status === "no_match") statusBox.classList.add("no-match");
  if (data.status === "category_absent") statusBox.classList.add("absent");

  statusBox.textContent = data.message;
  cards.innerHTML = data.results.map(renderCard).join("");
  renderPlanB(data.plan_b || []);
  diagnostics.textContent = JSON.stringify(data.diagnostics, null, 2);

  if (window.innerWidth < 1040) {
    resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function setLoading(isLoading) {
  submitButton.disabled = isLoading;
  submitButton.querySelector("span").textContent = isLoading ? "Ищем подходящих…" : "Найти варианты";
  el("requestState").textContent = isLoading ? "Проверяем параметры и доступность подрядчиков" : "";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setLoading(true);

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
    emptyState.classList.add("hidden");
    resultSection.classList.remove("hidden");
    cards.innerHTML = "";
    renderPlanB([]);
    statusBox.className = "status absent";
    statusBox.textContent = "Не удалось выполнить поиск. Проверьте параметры и попробуйте ещё раз.";
    diagnostics.textContent = String(error.message || error);
  } finally {
    setLoading(false);
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
  el("profilesBadge").textContent = "Сервис временно недоступен";
  console.error(error);
});
