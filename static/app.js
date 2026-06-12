/* Mensa Köln web app: loads the full plan once and renders day/canteen views
   client-side. URL hash routing (#canteen/date), meals served at both times
   appear in both sections, sides live in a bottom sheet, allergen exclusion
   and language are persisted. */

const STR = {
  de: {
    lunch: "🌤️ Mittagessen",
    dinner: "🌙 Abendessen",
    all: "Alle",
    vegetarian: "🥕 Vegetarisch",
    vegan: "🌱 Vegan",
    fish: "🐟 Fisch",
    meat: "🍖 Fleisch",
    throughout: "🕐 Mittags & abends",
    also_lunch: "mittags",
    also_dinner: "abends",
    sides_choice_one: "1 Beilage nach Wahl",
    sides_choice: (n) => `${n} Beilagen nach Wahl`,
    allergens: "Allergene & Zusatzstoffe",
    allergen_filter: "Allergene",
    allergen_title: "Allergene ausschließen",
    allergen_hint: "Gerichte mit angekreuzten Allergenen werden ausgeblendet. Die Auswahl wird gespeichert.",
    sides_button: (n) => `🥗 Beilagen & Extras (${n})`,
    sides_title: "Beilagen & Extras",
    no_menu: "Für diesen Tag liegt kein Speiseplan vor.",
    no_match: "Keine passenden Gerichte für diesen Filter.",
    ended: "vorbei",
    roles: { student: "Studierende", employee: "Bedienstete", guest: "Gäste" },
    source: "Quelle",
    updated: "Stand",
    load_error: "Fehler beim Laden des Speiseplans",
    rate: "Bewerten",
    ratings: (n) => `${n} ${n === 1 ? "Bewertung" : "Bewertungen"}`,
  },
  en: {
    lunch: "🌤️ Lunch",
    dinner: "🌙 Dinner",
    all: "All",
    vegetarian: "🥕 Vegetarian",
    vegan: "🌱 Vegan",
    fish: "🐟 Fish",
    meat: "🍖 Meat",
    throughout: "🕐 Lunch & dinner",
    also_lunch: "lunch",
    also_dinner: "dinner",
    sides_choice_one: "1 side of your choice",
    sides_choice: (n) => `${n} sides of your choice`,
    allergens: "Allergens & additives",
    allergen_filter: "Allergens",
    allergen_title: "Exclude allergens",
    allergen_hint: "Meals containing checked allergens are hidden. Your selection is saved.",
    sides_button: (n) => `🥗 Sides & extras (${n})`,
    sides_title: "Sides & extras",
    no_menu: "No menu available for this day.",
    no_match: "No meals match this filter.",
    ended: "ended",
    roles: { student: "Students", employee: "Employees", guest: "Guests" },
    source: "Source",
    updated: "Updated",
    load_error: "Failed to load the menu",
    rate: "Rate",
    ratings: (n) => `${n} rating${n === 1 ? "" : "s"}`,
  },
};

const DOW = {
  de: ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"],
  en: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
};

const state = {
  plan: null,
  canteen: localStorage.getItem("mensa.canteen") || "unimensa",
  date: null,
  diet: localStorage.getItem("mensa.diet") || "all",
  priceRole: localStorage.getItem("mensa.priceRole") || "student",
  lang: localStorage.getItem("mensa.lang") || "de",
  excluded: new Set(JSON.parse(localStorage.getItem("mensa.excludedAllergens") || "[]")),
  ratings: {},
  clientId: getClientId(),
};

function getClientId() {
  let id = localStorage.getItem("mensa.client");
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem("mensa.client", id);
  }
  return id;
}

const $ = (sel) => document.querySelector(sel);
const t = () => STR[state.lang];

/* ---------- init & routing ---------- */

async function init() {
  const [planRes, ratingsRes] = await Promise.all([
    fetch("api/plan"),
    fetch(`api/ratings?client=${encodeURIComponent(state.clientId)}`).catch(() => null),
  ]);
  if (!planRes.ok) throw new Error(`HTTP ${planRes.status}`);
  state.plan = await planRes.json();
  if (ratingsRes?.ok) state.ratings = await ratingsRes.json();

  readHash();
  if (!state.plan.menu[state.canteen]) state.canteen = Object.keys(state.plan.canteens)[0];
  if (!state.date || !state.plan.days.includes(state.date)) state.date = defaultDate();

  renderCanteenSelect();
  bindControls();
  renderAll();
  writeHash();

  window.addEventListener("hashchange", () => {
    readHash();
    if (!state.plan.menu[state.canteen]) state.canteen = Object.keys(state.plan.canteens)[0];
    if (!state.date || !state.plan.days.includes(state.date)) state.date = defaultDate();
    $("#canteen-select").value = state.canteen;
    renderAll();
  });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}

function readHash() {
  const parts = decodeURIComponent(location.hash.replace(/^#\/?/, "")).split("/");
  if (parts[0]) state.canteen = parts[0];
  if (parts[1] && /^\d{4}-\d{2}-\d{2}$/.test(parts[1])) state.date = parts[1];
}

function writeHash() {
  const hash = `#${encodeURIComponent(state.canteen)}/${state.date}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

function defaultDate() {
  const today = localDateStr(new Date());
  const days = state.plan.days;
  return days.find((d) => d >= today) || days[days.length - 1];
}

function localDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/* ---------- filtering ---------- */

function mealsFor(canteen, date) {
  return (state.plan.menu[canteen] || {})[date] || [];
}

function dietMatches(meal) {
  if (state.diet === "vegan") return meal.diet === "vegan";
  if (state.diet === "vegetarian") return meal.diet === "vegan" || meal.diet === "vegetarian";
  return true;
}

function allergenMatches(meal) {
  if (!state.excluded.size) return true;
  for (const a of meal.allergens || []) {
    for (const ex of state.excluded) {
      // excluding a parent code (11) also hides its sub-codes (11w, 11g)
      if (a.code === ex || (a.code.length === ex.length + 1 && a.code.startsWith(ex) && /[a-z]/i.test(a.code.slice(-1)))) {
        return false;
      }
    }
  }
  return true;
}

const visible = (meal) => dietMatches(meal) && allergenMatches(meal);

/* ---------- meal text helpers ---------- */

function mealName(meal) {
  return (state.lang === "en" && meal.name_en) || meal.name;
}

function mealComponents(meal) {
  const list = state.lang === "en" && meal.components_en?.length ? meal.components_en : meal.components;
  return (list || []).map((c) => c.name).join(" · ");
}

function servingText(serving) {
  if (!serving) return null;
  const parts = [];
  if (serving.location) parts.push(serving.location);
  if (serving.start && serving.end) parts.push(`${serving.start}–${serving.end} ${state.lang === "de" ? "Uhr" : ""}`.trim());
  return parts.join(" · ") || null;
}

function priceText(meal) {
  const value = meal.prices?.[state.priceRole] ?? meal.prices?.student;
  if (value == null) return "";
  const formatted = state.lang === "de" ? value.toFixed(2).replace(".", ",") + " €" : "€" + value.toFixed(2);
  return meal.price_unit ? `${formatted} / ${meal.price_unit}` : formatted;
}

/* ---------- controls ---------- */

function renderCanteenSelect() {
  const select = $("#canteen-select");
  select.innerHTML = "";
  const entries = Object.values(state.plan.canteens);
  entries.sort((a, b) => a.name.localeCompare(b.name, "de"));
  for (const c of entries) {
    const total = Object.values(state.plan.menu[c.id] || {}).reduce((n, m) => n + m.length, 0);
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = total ? c.name : `${c.name} ✕`;
    select.appendChild(opt);
  }
  select.value = state.canteen;
  select.addEventListener("change", () => {
    state.canteen = select.value;
    localStorage.setItem("mensa.canteen", state.canteen);
    writeHash();
    renderAll();
  });
}

function bindControls() {
  for (const btn of document.querySelectorAll("#diet-filters button")) {
    btn.classList.toggle("active", btn.dataset.diet === state.diet);
    btn.addEventListener("click", () => {
      document.querySelectorAll("#diet-filters button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.diet = btn.dataset.diet;
      localStorage.setItem("mensa.diet", state.diet);
      renderMenu();
    });
  }

  const role = $("#price-role");
  role.value = state.priceRole;
  role.addEventListener("change", () => {
    state.priceRole = role.value;
    localStorage.setItem("mensa.priceRole", state.priceRole);
    renderMenu();
  });

  $("#lang-toggle").addEventListener("click", () => {
    state.lang = state.lang === "de" ? "en" : "de";
    localStorage.setItem("mensa.lang", state.lang);
    document.documentElement.lang = state.lang;
    renderAll();
  });

  $("#allergen-button").addEventListener("click", () => openSheet("allergen"));
  $("#sides-button").addEventListener("click", () => openSheet("sides"));
  $("#sheet-backdrop").addEventListener("click", closeSheets);
  document.querySelectorAll(".sheet [data-close]").forEach((b) => b.addEventListener("click", closeSheets));
  document.addEventListener("keydown", (e) => e.key === "Escape" && closeSheets());
}

function renderControlLabels() {
  const s = t();
  for (const btn of document.querySelectorAll("#diet-filters button")) {
    btn.textContent = s[btn.dataset.diet];
  }
  const role = $("#price-role");
  role.innerHTML = "";
  for (const key of ["student", "employee", "guest"]) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = s.roles[key];
    role.appendChild(opt);
  }
  role.value = state.priceRole;
  $("#lang-toggle").textContent = state.lang === "de" ? "EN" : "DE";
  const n = state.excluded.size;
  $("#allergen-button").textContent = `⚠️ ${s.allergen_filter}${n ? ` (${n})` : ""}`;
  $("#allergen-button").classList.toggle("active", n > 0);
}

/* ---------- sheets (bottom panels) ---------- */

function openSheet(which) {
  closeSheets();
  $("#sheet-backdrop").hidden = false;
  if (which === "sides") {
    renderSidesSheet();
    $("#sides-sheet").hidden = false;
  } else {
    renderAllergenSheet();
    $("#allergen-sheet").hidden = false;
  }
  document.body.classList.add("sheet-open");
}

function closeSheets() {
  $("#sheet-backdrop").hidden = true;
  $("#sides-sheet").hidden = true;
  $("#allergen-sheet").hidden = true;
  document.body.classList.remove("sheet-open");
}

function renderSidesSheet() {
  const s = t();
  $("#sides-title").textContent = s.sides_title;
  const body = $("#sides-body");
  body.innerHTML = "";

  const sides = mealsFor(state.canteen, state.date).filter((m) => m.is_side && visible(m));
  // group by serving spot
  const groups = new Map();
  for (const side of sides) {
    const key = servingText(side.serving) || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(side);
  }
  for (const [spot, items] of groups) {
    if (spot) {
      const h = document.createElement("div");
      h.className = "line-header";
      h.innerHTML = `<h3>📍 ${esc(spot)}</h3>`;
      body.appendChild(h);
    }
    for (const side of items) body.appendChild(mealCard(side, side.serving, true));
  }
}

function renderAllergenSheet() {
  const s = t();
  $("#allergen-title").textContent = s.allergen_title;
  $("#allergen-hint").textContent = s.allergen_hint;
  const body = $("#allergen-body");
  body.innerHTML = "";

  const legend = state.plan.allergen_legend || {};
  const codes = Object.keys(legend).sort((a, b) =>
    (parseInt(a, 10) || 99) - (parseInt(b, 10) || 99) || a.localeCompare(b)
  );
  for (const code of codes) {
    const label = (state.lang === "en" && legend[code].en) || legend[code].de || code;
    const row = document.createElement("label");
    row.className = "allergen-row";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.excluded.has(code);
    cb.addEventListener("change", () => {
      cb.checked ? state.excluded.add(code) : state.excluded.delete(code);
      localStorage.setItem("mensa.excludedAllergens", JSON.stringify([...state.excluded]));
      renderControlLabels();
      renderMenu();
    });
    row.appendChild(cb);
    const span = document.createElement("span");
    span.textContent = `${code} – ${label}`;
    row.appendChild(span);
    body.appendChild(row);
  }
}

/* ---------- main rendering ---------- */

function renderAll() {
  renderControlLabels();
  renderCanteenInfo();
  renderDayNav();
  renderMenu();
  renderFooter();
}

function renderCanteenInfo() {
  const c = state.plan.canteens[state.canteen];
  const hours = (c.hours || "").split("\n").join(" · ");
  $("#canteen-info").innerHTML = `
    <span>📍 ${esc(c.address)}</span>
    <span>🕐 ${esc(hours)}</span>`;
}

function renderDayNav() {
  const nav = $("#day-nav");
  nav.innerHTML = "";
  for (const date of state.plan.days) {
    const d = new Date(date + "T12:00:00");
    const btn = document.createElement("button");
    const hasMeals = mealsFor(state.canteen, date).length > 0;
    btn.className = (date === state.date ? "active" : "") + (hasMeals ? "" : " empty");
    btn.innerHTML = `<span class="dow">${DOW[state.lang][d.getDay()]}</span><span class="date">${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.</span>`;
    btn.addEventListener("click", () => {
      state.date = date;
      writeHash();
      renderDayNav();
      renderMenu();
    });
    nav.appendChild(btn);
  }
  nav.querySelector(".active")?.scrollIntoView({ block: "nearest", inline: "center" });
}

function groupMeals(meals) {
  // mealtime -> serving spot -> meals; a meal served at both lunch and
  // dinner is placed in both sections, under its serving spot for that time
  const sections = [];
  const byTime = new Map();
  for (const meal of meals) {
    const placements = new Map();
    for (const s of meal.servings || []) {
      if (!placements.has(s.mealtime)) placements.set(s.mealtime, s);
    }
    for (const mt of meal.mealtimes || [meal.mealtime]) {
      if (!placements.has(mt)) placements.set(mt, null);
    }
    for (const [mt, serving] of placements) {
      if (!byTime.has(mt)) byTime.set(mt, new Map());
      const groups = byTime.get(mt);
      const key = serving ? `${serving.start || ""}|${serving.location || ""}|${serving.end || ""}` : "~none";
      if (!groups.has(key)) groups.set(key, { serving, meals: [] });
      groups.get(key).meals.push(meal);
    }
  }
  for (const mt of ["lunch", "dinner"]) {
    if (!byTime.has(mt)) continue;
    const groups = [...byTime.get(mt).values()].sort((a, b) => {
      const ka = a.serving ? `${a.serving.start || "98:98"}|${a.serving.location || ""}` : "99:99";
      const kb = b.serving ? `${b.serving.start || "98:98"}|${b.serving.location || ""}` : "99:99";
      return ka.localeCompare(kb);
    });
    sections.push({ mealtime: mt, groups });
  }
  return sections;
}

function renderMenu() {
  const s = t();
  const main = $("#menu");
  main.innerHTML = "";

  const all = mealsFor(state.canteen, state.date);
  const mains = all.filter((m) => !m.is_side);
  const meals = mains.filter(visible);
  const sidesCount = all.filter((m) => m.is_side && visible(m)).length;

  const bar = $("#sides-bar");
  bar.hidden = sidesCount === 0;
  $("#sides-button").textContent = s.sides_button(sidesCount);

  if (!meals.length) {
    const msg = !mains.length ? s.no_menu : s.no_match;
    main.innerHTML = `<div class="empty-state">😴 ${esc(msg)}</div>`;
    return;
  }

  const isToday = state.date === localDateStr(new Date());
  const now = new Date().toTimeString().slice(0, 5);

  for (const section of groupMeals(meals)) {
    const sectionEl = document.createElement("section");
    sectionEl.className = "mealtime-section";
    sectionEl.innerHTML = `<h2>${s[section.mealtime] || section.mealtime}</h2>`;

    for (const group of section.groups) {
      const div = document.createElement("div");
      div.className = "line-group";
      const past = isToday && group.serving?.end && group.serving.end < now;
      if (past) div.classList.add("past");

      const spot = servingText(group.serving);
      if (spot) {
        const header = document.createElement("div");
        header.className = "line-header";
        header.innerHTML = `<h3>📍 ${esc(spot)}</h3>` + (past ? `<span class="past-label">${esc(s.ended)}</span>` : "");
        div.appendChild(header);
      }

      for (const meal of group.meals) div.appendChild(mealCard(meal, group.serving));
      sectionEl.appendChild(div);
    }
    main.appendChild(sectionEl);
  }
}

function mealCard(meal, currentServing, compact = false) {
  const s = t();
  const card = document.createElement("article");
  card.className = "meal-card" + (compact ? " compact" : "");

  const components = mealComponents(meal);
  const price = priceText(meal);

  const badges = [];
  if (s[meal.diet]) badges.push(`<span class="badge ${meal.diet}">${s[meal.diet]}</span>`);
  if (meal.throughout) badges.push(`<span class="badge throughout">${s.throughout}</span>`);
  if (meal.choice_sides > 0) {
    const label = meal.choice_sides === 1 ? s.sides_choice_one : s.sides_choice(meal.choice_sides);
    badges.push(`<span class="badge sides">🥗 ${esc(label)}</span>`);
  }
  // serving spots other than the one this card is displayed under
  const curKey = currentServing ? `${currentServing.location}|${currentServing.start}` : "";
  for (const sv of meal.servings || []) {
    if (`${sv.location}|${sv.start}` === curKey) continue;
    const when = sv.mealtime === "dinner" ? s.also_dinner : s.also_lunch;
    badges.push(`<span class="badge serving">📍 ${esc(when)}: ${esc(servingText(sv) || "?")}</span>`);
  }
  for (const icon of meal.icons || []) {
    const code = icon.code.toUpperCase();
    if (["VGN", "VGT", "V", "F", "G", "R", "S", "L", "W"].includes(code)) continue; // covered by diet badge
    badges.push(`<span class="badge info">${esc(icon.label)}</span>`);
  }

  let allergens = "";
  if (meal.allergens?.length) {
    const legend = state.plan.allergen_legend || {};
    const items = meal.allergens
      .map((a) => {
        const label = (state.lang === "en" && legend[a.code]?.en) || a.label;
        return `<li>${esc(a.code)} – ${esc(label)}</li>`;
      })
      .join("");
    allergens = `<details class="meal-allergens"><summary>${esc(s.allergens)} (${meal.allergens.length})</summary><ul>${items}</ul></details>`;
  }

  card.innerHTML = `
    <div class="meal-top">
      <div>
        <div class="meal-name">${esc(mealName(meal))}</div>
        ${components ? `<p class="meal-components">${esc(components)}</p>` : ""}
        ${meal.description && !compact ? `<p class="meal-components">${esc(meal.description)}</p>` : ""}
      </div>
      ${price ? `<div class="meal-price">${esc(price)}</div>` : ""}
    </div>
    ${badges.length ? `<div class="meal-badges">${badges.join("")}</div>` : ""}
    ${allergens}`;
  if (meal.rating_key) card.appendChild(ratingRow(meal));
  return card;
}

function ratingRow(meal) {
  const s = t();
  const row = document.createElement("div");
  row.className = "meal-rating";

  const render = () => {
    row.innerHTML = "";
    const r = state.ratings[meal.rating_key] || {};
    const mine = r.mine || 0;
    for (let i = 1; i <= 5; i++) {
      const btn = document.createElement("button");
      btn.className = "star" + (i <= mine ? " filled" : "");
      btn.textContent = i <= mine ? "★" : "☆";
      btn.title = `${s.rate}: ${i}/5`;
      btn.addEventListener("click", () => submit(i));
      row.appendChild(btn);
    }
    const info = document.createElement("span");
    info.className = "rating-avg";
    info.textContent = r.count ? `Ø ${r.avg.toFixed(1).replace(".", state.lang === "de" ? "," : ".")} · ${s.ratings(r.count)}` : s.rate;
    row.appendChild(info);
  };

  const submit = async (stars) => {
    try {
      const res = await fetch("api/rate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: meal.rating_key, stars, client: state.clientId }),
      });
      if (res.ok) {
        state.ratings[meal.rating_key] = await res.json();
        // refresh every visible rating row for this meal (it can appear in
        // several sections/sheets at once)
        document
          .querySelectorAll(`.meal-rating[data-key="${CSS.escape(meal.rating_key)}"]`)
          .forEach((el) => el.dispatchEvent(new CustomEvent("rating-update")));
      }
    } catch {
      /* offline: keep current display */
    }
  };

  row.dataset.key = meal.rating_key;
  row.addEventListener("rating-update", render);
  render();
  return row;
}

function renderFooter() {
  const s = t();
  const fetched = new Date(state.plan.fetched_at);
  $("#meta-info").textContent =
    `${state.plan.organization} · ${s.source}: ${state.plan.source} · ${s.updated}: ${fetched.toLocaleString(state.lang === "de" ? "de-DE" : "en-GB")}`;
}

function esc(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

init().catch((err) => {
  $("#menu").innerHTML = `<div class="empty-state">⚠️ ${esc(STR[state.lang].load_error)}:<br>${esc(err.message)}</div>`;
});
