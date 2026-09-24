// Draws create_chart output with Chart.js (loaded from the CDN in index.html as `Chart`).
//
// Design rules: series take the validated palette in fixed order (--series-1..8 in styles.css);
// a single series has no legend (the title names it); bars are thin with a rounded data end and
// grow from the baseline; lines are 2px; gridlines are faint solid hairlines; text uses ink
// colors, never series colors; hovering shows a tooltip. The exact rows stay available in the
// query card above each chart, which is the chart's table view.

import { element } from "./dom.js";

const BAR_ROW_PX = 28; // horizontal bars: height per category
const live = new Map(); // canvas -> {instance, spec}, redrawn when the color scheme changes
// Chart.js comes from a CDN; without it charts degrade to a note instead of breaking the chat.
const chartLibrary = globalThis.Chart;
if (chartLibrary) chartLibrary.defaults.font.family = getComputedStyle(document.body).fontFamily;

/** A <figure> containing the chart. Drawn once it's in the page (Chart.js needs a size). */
export function createChartFigure(spec) {
  const canvas = element("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", describe(spec));

  const box = element("div", "chart-canvas", canvas);
  if (spec.type === "horizontal_bar") {
    box.style.height = `${Math.min(600, Math.max(220, spec.labels.length * BAR_ROW_PX + 60))}px`;
  }
  const figure = element("figure", "chart", [element("figcaption", "chart-title", spec.title), box]);
  if (!chartLibrary) {
    box.replaceChildren(element("p", "meta",
      "The chart library couldn't be loaded; the data is in the query above."));
    box.style.height = "auto";
    return figure;
  }
  requestAnimationFrame(() => draw(canvas, spec));
  return figure;
}

function draw(canvas, spec) {
  for (const [otherCanvas, { instance }] of live) {
    if (!otherCanvas.isConnected) { // its conversation was closed
      instance.destroy();
      live.delete(otherCanvas);
    }
  }
  if (!canvas.isConnected) return;
  const box = canvas.parentElement;
  const length = spec.type === "horizontal_bar" ? box.clientHeight : box.clientWidth; // category axis
  const config = chartConfig(spec, readTheme(), length);
  live.set(canvas, { instance: new chartLibrary(canvas, config), spec });
}

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  for (const [canvas, { instance, spec }] of live) {
    instance.destroy();
    live.delete(canvas);
    draw(canvas, spec);
  }
});

function chartConfig(spec, theme, axisLength) {
  const isLine = spec.type === "line";
  const horizontal = spec.type === "horizontal_bar";
  const stacked = spec.type === "stacked_bar";
  const multiple = spec.series.length > 1;
  const [categoryAxis, valueAxis] = horizontal ? ["y", "x"] : ["x", "y"];
  const format = formatter(spec.value_format);
  const compact = formatter(spec.value_format, { compact: true });
  const lastIndex = spec.series.length - 1;
  // Side-by-side bars need a fixed thickness to sit next to each other (with only a maximum,
  // Chart.js spreads them across the slot). Fit them into ~70% of each category's space.
  const groupedThickness = multiple && !stacked && !isLine
    ? Math.max(4, Math.min(24, Math.floor((axisLength * 0.7) / spec.labels.length / spec.series.length)))
    : undefined;

  const datasets = spec.series.map((series, index) => {
    const color = theme.series[index];
    const common = { label: series.name, data: series.values, backgroundColor: color };
    if (isLine) {
      return {
        ...common,
        borderColor: color,
        borderWidth: 2,
        borderJoinStyle: "round",
        borderCapStyle: "round",
        pointRadius: spec.labels.length <= 12 ? 4 : 0, // markers only when points are few
        pointHoverRadius: 5,
        pointBorderColor: theme.surface, // surface ring keeps overlapping markers legible
        pointBorderWidth: 2,
        tension: 0,
      };
    }
    return {
      ...common,
      maxBarThickness: 24,
      barThickness: groupedThickness,
      // Rounded data end, square at the baseline; in a stack only the top segment is rounded.
      borderRadius: stacked && index !== lastIndex ? 0 : 4,
      borderSkipped: "start",
      // A surface-colored edge is the 2px gap between touching bars and stacked segments.
      borderColor: theme.surface,
      borderWidth: multiple ? 2 : 0,
    };
  });

  return {
    type: isLine ? "line" : "bar",
    data: { labels: spec.labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      indexAxis: categoryAxis,
      // Lines: one tooltip for every series at the hovered x. Bars: the bar is the target.
      interaction: isLine || stacked
        ? { mode: "index", intersect: false, axis: categoryAxis }
        : { mode: "nearest", intersect: true, axis: categoryAxis },
      plugins: {
        legend: {
          display: multiple,
          position: "top",
          align: "start",
          labels: {
            color: theme.textMuted,
            usePointStyle: true,
            pointStyle: isLine ? "line" : "rectRounded",
            boxWidth: 12,
            boxHeight: 8,
            padding: 14,
          },
        },
        tooltip: {
          backgroundColor: theme.tooltip,
          titleColor: theme.textMuted,
          bodyColor: theme.text,
          borderColor: theme.border,
          borderWidth: 1,
          padding: 10,
          usePointStyle: true,
          bodyFont: { weight: "600" },
          callbacks: {
            label: (item) => {
              const value = item.parsed[valueAxis];
              const shown = value === null || value === undefined ? "—" : format(value);
              return multiple ? `${shown}  ${item.dataset.label}` : shown;
            },
          },
        },
      },
      scales: {
        [categoryAxis]: {
          stacked,
          grid: { display: false },
          border: { color: theme.axis },
          ticks: { color: theme.textMuted, maxRotation: 0, autoSkip: !horizontal },
        },
        [valueAxis]: {
          stacked,
          beginAtZero: !isLine, // bars must grow from zero; lines may zoom to their range
          grid: { color: theme.grid, lineWidth: 1 },
          border: { display: false },
          ticks: { color: theme.textMuted, callback: (value) => compact(value), maxTicksLimit: 6 },
          title: { display: Boolean(spec.y_label), text: spec.y_label ?? "", color: theme.textMuted },
        },
      },
    },
  };
}

function formatter(valueFormat, { compact = false } = {}) {
  const options = {
    number: { maximumFractionDigits: 2 },
    currency: { style: "currency", currency: "USD", maximumFractionDigits: compact ? 1 : 2 },
    percent: { style: "percent", maximumFractionDigits: 1 },
  }[valueFormat] ?? {};
  const numberFormat = new Intl.NumberFormat(undefined, {
    ...options,
    ...(compact && valueFormat !== "percent" ? { notation: "compact" } : {}),
  });
  return (value) => numberFormat.format(value);
}

function readTheme() {
  const style = getComputedStyle(document.documentElement);
  const token = (name) => style.getPropertyValue(name).trim();
  return {
    series: Array.from({ length: 8 }, (_, i) => token(`--series-${i + 1}`)),
    surface: token("--bg"),
    text: token("--text"),
    textMuted: token("--text-muted"),
    grid: token("--chart-grid"),
    axis: token("--chart-axis"),
    tooltip: token("--bg-elevated"),
    border: token("--border"),
  };
}

function describe(spec) {
  const kind = { line: "Line", bar: "Bar", horizontal_bar: "Bar", stacked_bar: "Stacked bar" }[spec.type];
  const names = spec.series.map((series) => series.name).join(", ");
  return `${kind} chart: ${spec.title}. ${spec.labels.length} categories; series: ${names}.`;
}
