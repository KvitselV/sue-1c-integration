/* Chart helpers for СУЭ UI */
const SUE_COLORS = {
  green: "#1f6a4f",
  amber: "#c47a2c",
  blue: "#2f5d8c",
  gray: "#6b7c6e",
  softGreen: "rgba(31,106,79,.18)",
  softAmber: "rgba(196,122,44,.18)",
  softBlue: "rgba(47,93,140,.18)",
};

Chart.defaults.font.family = '"Segoe UI", Manrope, system-ui, sans-serif';
Chart.defaults.color = "#5d6a60";
Chart.defaults.responsive = true;
Chart.defaults.maintainAspectRatio = false;
Chart.defaults.resizeDelay = 0;
Chart.defaults.layout.padding = { top: 4, right: 6, bottom: 2, left: 2 };
Chart.defaults.plugins.legend.labels.boxWidth = 10;
Chart.defaults.plugins.legend.labels.padding = 10;
Chart.defaults.plugins.legend.labels.usePointStyle = true;

function sueMoney(v) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(v);
}

function truncateTick(maxLen) {
  return function tickLabel(value) {
    const label = String(this.getLabelForValue(value) ?? "");
    return label.length > maxLen ? `${label.slice(0, maxLen - 1)}…` : label;
  };
}

function makeBar(ctx, labels, datasets, options = {}) {
  const { percent = false, plugins, scales, ...rest } = options;
  return new Chart(ctx, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom" }, ...plugins },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            autoSkip: true,
            maxRotation: 40,
            minRotation: 0,
            maxTicksLimit: 8,
            callback: truncateTick(12),
          },
        },
        y: {
          beginAtZero: true,
          ticks: { callback: (v) => (percent ? `${v}%` : sueMoney(v)), maxTicksLimit: 6 },
          grid: { color: "rgba(0,0,0,.05)" },
        },
        ...scales,
      },
      ...rest,
    },
  });
}

function makeLine(ctx, labels, datasets, options = {}) {
  const { plugins, scales, ...rest } = options;
  return new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, ...plugins },
      scales: {
        x: {
          grid: { display: false },
          ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8, callback: truncateTick(10) },
        },
        y: {
          beginAtZero: false,
          ticks: { callback: (v) => sueMoney(v), maxTicksLimit: 6 },
          grid: { color: "rgba(0,0,0,.05)" },
        },
        ...scales,
      },
      ...rest,
    },
  });
}

function makeDoughnut(ctx, labels, values, colors) {
  return new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderWidth: 0,
        hoverOffset: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 10, padding: 8, font: { size: 11 } },
        },
      },
    },
  });
}

function parseJsonScript(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  return JSON.parse(el.textContent);
}

const CAL_WEEK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const CAL_MONTHS = [
  "январь", "февраль", "март", "апрель", "май", "июнь",
  "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
];

function parseISODate(value) {
  if (!value) return null;
  const parts = value.split("-").map(Number);
  if (parts.length !== 3 || parts.some((n) => Number.isNaN(n))) return null;
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function toISODate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatRuDate(value) {
  const date = parseISODate(value);
  if (!date) return "дд.мм.гггг";
  return date.toLocaleDateString("ru-RU");
}

function initDatePickers() {
  document.querySelectorAll("input.sue-date").forEach(enhanceDateInput);
}

function enhanceDateInput(input) {
  if (input.dataset.sueCal === "1") return;
  input.dataset.sueCal = "1";

  const wrap = document.createElement("div");
  wrap.className = "sue-cal";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  input.classList.add("sue-cal-native");
  input.setAttribute("tabindex", "-1");

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "sue-cal-trigger";
  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.setAttribute("aria-label", input.previousElementSibling?.textContent?.trim() || "Дата");
  trigger.innerHTML = `<span class="sue-cal-text"></span><svg class="icon" aria-hidden="true"><use href="#i-cal"></use></svg>`;
  wrap.appendChild(trigger);
  const text = trigger.querySelector(".sue-cal-text");

  const sync = () => {
    text.textContent = formatRuDate(input.value);
    trigger.classList.toggle("empty", !input.value);
  };
  sync();
  input.addEventListener("change", sync);

  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    toggleCalendar(input, trigger);
  });
}

let calendarState = null;

function calendarRoot() {
  let pop = document.getElementById("sue-cal-pop");
  if (pop) return pop;
  pop = document.createElement("div");
  pop.id = "sue-cal-pop";
  pop.className = "sue-cal-pop";
  pop.hidden = true;
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", "Календарь");
  document.body.appendChild(pop);
  document.addEventListener("pointerdown", (event) => {
    if (!calendarState) return;
    if (pop.contains(event.target) || calendarState.trigger.contains(event.target)) return;
    closeCalendar();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeCalendar();
  });
  window.addEventListener("resize", closeCalendar);
  return pop;
}

function toggleCalendar(input, trigger) {
  if (calendarState && calendarState.input === input) {
    closeCalendar();
    return;
  }
  openCalendar(input, trigger);
}

function openCalendar(input, trigger) {
  const selected = parseISODate(input.value);
  const basis = selected || new Date();
  calendarState = {
    input,
    trigger,
    year: basis.getFullYear(),
    month: basis.getMonth(),
  };
  renderCalendar();
}

function closeCalendar() {
  const pop = document.getElementById("sue-cal-pop");
  if (pop) pop.hidden = true;
  calendarState = null;
}

function renderCalendar() {
  if (!calendarState) return;
  const pop = calendarRoot();
  const { input, trigger, year, month } = calendarState;
  const selected = input.value;
  const today = toISODate(new Date());
  const first = new Date(year, month, 1);
  const startOffset = (first.getDay() + 6) % 7;
  const gridStart = new Date(year, month, 1 - startOffset);

  const days = [];
  for (let i = 0; i < 42; i += 1) {
    const date = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
    days.push(date);
  }

  pop.innerHTML = `
    <div class="sue-cal-head">
      <button type="button" class="sue-cal-nav" data-step="-1" aria-label="Предыдущий месяц">‹</button>
      <p class="sue-cal-title">${CAL_MONTHS[month]} ${year}</p>
      <button type="button" class="sue-cal-nav" data-step="1" aria-label="Следующий месяц">›</button>
    </div>
    <div class="sue-cal-week">${CAL_WEEK.map((d) => `<span>${d}</span>`).join("")}</div>
    <div class="sue-cal-grid">
      ${days.map((date) => {
        const iso = toISODate(date);
        const classes = ["sue-cal-day"];
        if (date.getMonth() !== month) classes.push("out");
        if (iso === selected) classes.push("picked");
        if (iso === today) classes.push("today");
        return `<button type="button" class="${classes.join(" ")}" data-date="${iso}">${date.getDate()}</button>`;
      }).join("")}
    </div>
    <div class="sue-cal-foot">
      <button type="button" class="sue-cal-today" data-today>Сегодня</button>
      ${input.required ? "" : '<button type="button" class="sue-cal-clear" data-clear>Очистить</button>'}
    </div>
  `;
  pop.hidden = false;
  placeCalendar(pop, trigger);

  pop.querySelectorAll("[data-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = new Date(calendarState.year, calendarState.month + Number(btn.dataset.step), 1);
      calendarState.year = next.getFullYear();
      calendarState.month = next.getMonth();
      renderCalendar();
    });
  });
  pop.querySelectorAll("[data-date]").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.date;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      closeCalendar();
    });
  });
  pop.querySelector("[data-today]")?.addEventListener("click", () => {
    input.value = today;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    closeCalendar();
  });
  pop.querySelector("[data-clear]")?.addEventListener("click", () => {
    input.value = "";
    input.dispatchEvent(new Event("change", { bubbles: true }));
    closeCalendar();
  });
}

function placeCalendar(pop, trigger) {
  const box = trigger.getBoundingClientRect();
  const width = pop.offsetWidth || 280;
  const left = Math.min(box.left, window.innerWidth - width - 12);
  pop.style.left = `${Math.max(12, left)}px`;
  pop.style.top = `${box.bottom + 8}px`;
}

function initFilePickers() {
  document.querySelectorAll("input.sue-file").forEach(enhanceFileInput);
}

function enhanceFileInput(input) {
  if (input.dataset.sueFile === "1") return;
  input.dataset.sueFile = "1";

  const wrap = document.createElement("div");
  wrap.className = "sue-file";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  input.classList.add("sue-file-native");
  input.setAttribute("tabindex", "-1");

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "sue-file-trigger";
  trigger.setAttribute("aria-label", "Выбрать файл");
  trigger.innerHTML = `<span class="sue-file-text"></span><svg class="icon" aria-hidden="true"><use href="#i-file"></use></svg>`;
  wrap.appendChild(trigger);
  const text = trigger.querySelector(".sue-file-text");

  const sync = () => {
    const file = input.files && input.files[0];
    text.textContent = file ? file.name : "Файл не выбран";
    trigger.classList.toggle("empty", !file);
  };
  sync();
  input.addEventListener("change", sync);
  trigger.addEventListener("click", () => input.click());
}

document.addEventListener("DOMContentLoaded", () => {
  initDatePickers();
  initFilePickers();
});

window.SUE = {
  colors: SUE_COLORS,
  makeBar,
  makeLine,
  makeDoughnut,
  parseJsonScript,
  sueMoney,
  initDatePickers,
  initFilePickers,
};
