/* Charts of the report page (templates/hisobot/staff/report.html), Chart.js 4.
   Data comes from #chart-data (json_script); colours from the design tokens of app.css: --chart-1 Туширилган,
   --chart-2 Кириб келган, --chart-3 Қолган (dashed). In the comparison each bar takes the --mark (Туширилган) and
   --soft (Кириб келган) of its МТУ, read from an element with class mtu-N (data.compare.sorts). The tables on the
   page carry every number: without Chart.js this file does nothing and the chart boxes keep their fallback text. */
(() => {
  "use strict";
  const source = document.getElementById("chart-data");
  if (typeof window.Chart === "undefined" || !source) return;

  const { Chart } = window;
  const data = JSON.parse(source.textContent);
  const names = data.names;
  const locale = document.documentElement.lang || undefined;
  const charts = [];
  // A4 less the margins and a tile's padding; on paper the comparison is at most one sheet tall (see beforeprint).
  const PAPER = { width: 640, height: 860 };
  let onPaper = false;

  const token = (name, element = document.documentElement) => getComputedStyle(element).getPropertyValue(name).trim();
  const lastIndex = (values) => values.reduce((last, value, i) => (value === null ? last : i), -1);
  // No value in any series (nothing entered in the range, or no bars): no bare axes, the box keeps its fallback text.
  const blank = (...series) => series.flat().every((value) => value === null);

  // --mark / --soft of each МТУ, as an element with that class computes them.
  function mtuColours(box, sorts) {
    const probe = document.createElement("i");
    probe.hidden = true;
    box.append(probe);
    const colours = sorts.map((sort) => {
      probe.className = `mtu-${sort}`;
      return { mark: token("--mark", probe), soft: token("--soft", probe) };
    });
    probe.remove();
    return colours;
  }

  function canvasIn(id) {
    const box = document.getElementById(id);
    if (!box) return null;
    const canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    // The tile title names the chart; the tables are its readable twin.
    const title = box.closest(".tile").querySelector(".t-title").textContent;
    canvas.setAttribute("aria-label", `${title}. Рақамлар жадвалларда.`);
    box.replaceChildren(canvas);
    return { box, canvas };
  }

  // The value at the end of each line (its latest day) and of each bar, as the «Ҳозир» chart prints them;
  // none on a bar too thin for the text (many stations on paper): the tables carry the numbers.
  const endLabels = {
    id: "endLabels",
    afterDatasetsDraw(chart) {
      const bar = chart.config.type === "bar";
      const labels = [];
      chart.data.datasets.forEach((dataset, i) => {
        const last = lastIndex(dataset.data);
        chart.getDatasetMeta(i).data.forEach((element, j) => {
          const value = dataset.data[j];
          if (value === null || (!bar && j !== last) || (bar && element.height < 9)) return;
          labels.push({ text: value.toLocaleString(locale), x: element.x + (bar ? 6 : 11), y: element.y, colour: dataset.labelColour });
        });
      });
      if (!bar) {
        // Two lines ending close together: push their labels apart.
        labels.sort((a, b) => a.y - b.y);
        labels.forEach((label, k) => {
          if (k) label.y = Math.max(label.y, labels[k - 1].y + 15);
        });
      }
      const { ctx } = chart;
      ctx.save();
      ctx.font = `700 12.5px ${Chart.defaults.font.family}`;
      ctx.textBaseline = "middle";
      for (const label of labels) {
        ctx.fillStyle = label.colour;
        ctx.fillText(label.text, label.x, label.y);
      }
      ctx.restore();
    },
  };

  function draw() {
    const colours = ["--chart-1", "--chart-2", "--chart-3"].map((name) => token(name));
    const [ink, ink2, ink3, surface, grid] = ["--ink", "--ink-2", "--ink-3", "--surface", "--chart-grid"].map((name) => token(name));
    Chart.defaults.color = token("--chart-text");
    Chart.defaults.borderColor = grid;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Object.assign(Chart.defaults.plugins.tooltip, {
      backgroundColor: token("--night"),
      titleColor: "#fff",
      bodyColor: token("--night-ink-2"),
      footerColor: token("--night-ink-2"),
      titleFont: { weight: "650", size: 13 },
      bodyFont: { weight: "500", size: 13 },
      footerFont: { weight: "500", size: 12 },
      padding: 12,
      cornerRadius: 14,
      boxPadding: 5,
      usePointStyle: true,
    });

    const base = { responsive: true, maintainAspectRatio: false, animation: false, locale };
    const valueAxis = {
      beginAtZero: true,
      grid: { color: grid },
      border: { display: false },
      ticks: { precision: 0, padding: 8 },
    };
    const quiet = { legend: { display: false } }; // the HTML legend in the tile head names the series

    const daily = data.daily;
    const line = blank(daily.unloaded, daily.arrived, daily.remaining) ? null : canvasIn("chart-daily");
    if (line) {
      const series = (key, colour, labelColour, extra = {}) => ({
        label: names[key],
        data: daily[key],
        labelColour,
        borderColor: colour,
        backgroundColor: colour,
        borderWidth: 2.5,
        borderJoinStyle: "round",
        borderCapStyle: "round",
        // A dot only on the latest day and on a day alone between gaps (no line would show it).
        pointRadius(context) {
          const values = context.dataset.data;
          const i = context.dataIndex;
          if (values[i] === null) return 0;
          if (i === lastIndex(values)) return 5;
          return (values[i - 1] ?? null) === null && (values[i + 1] ?? null) === null ? 3.5 : 0;
        },
        pointHoverRadius: 5,
        pointHitRadius: 12,
        pointBorderColor: surface,
        pointBorderWidth: 2.5,
        pointStyle: "circle",
        ...extra,
      });
      charts.push(
        new Chart(line.canvas, {
          type: "line",
          data: {
            labels: daily.labels,
            datasets: [
              series("unloaded", colours[0], ink),
              series("arrived", colours[1], ink2),
              series("remaining", colours[2], ink3, { borderDash: [6, 5], borderCapStyle: "butt" }),
            ],
          },
          plugins: [endLabels],
          options: {
            ...base,
            layout: { padding: { top: 8, right: 44 } },
            interaction: { mode: "index", intersect: false },
            plugins: {
              ...quiet,
              tooltip: {
                filter: (item) => item.raw !== null,
                callbacks: {
                  footer(items) {
                    const missing = items.length ? daily.missing[items[0].dataIndex] : null;
                    return missing ? `${names.missing}: ${missing}` : "";
                  },
                },
              },
            },
            scales: {
              x: {
                grid: { display: false },
                border: { color: token("--line-2") },
                ticks: {
                  maxRotation: 0,
                  autoSkipPadding: 16,
                  padding: 6,
                  callback(value) {
                    return this.getLabelForValue(value).slice(0, 5); // dd.mm; the tooltip shows the full date
                  },
                },
              },
              y: valueAxis,
            },
          },
        }),
      );
    }

    const compare = data.compare;
    const bars = blank(compare.unloaded, compare.arrived) ? null : canvasIn("chart-compare");
    if (bars) {
      const tints = mtuColours(bars.box, compare.sorts);
      // Horizontal bars, so long station names stay readable: the box grows with the number of rows.
      const needed = compare.labels.length * 34 + 40;
      if (needed > bars.box.clientHeight) bars.box.style.height = `${needed}px`;
      const series = (key, extra) => ({
        label: names[key],
        data: compare[key],
        labelColour: ink2,
        borderRadius: 6,
        borderSkipped: "start",
        maxBarThickness: 18,
        categoryPercentage: 0.74,
        barPercentage: 0.92,
        ...extra,
      });
      charts.push(
        new Chart(bars.canvas, {
          type: "bar",
          data: {
            labels: compare.labels,
            datasets: [
              series("unloaded", { backgroundColor: tints.map((tint) => tint.mark) }),
              series("arrived", {
                backgroundColor: tints.map((tint) => tint.soft),
                borderColor: tints.map((tint) => tint.mark),
                borderWidth: 1.5,
              }),
            ],
          },
          plugins: [endLabels],
          options: {
            ...base,
            indexAxis: "y",
            layout: { padding: { right: 40 } },
            interaction: { mode: "index", axis: "y", intersect: false },
            plugins: quiet,
            scales: {
              x: valueAxis,
              y: {
                grid: { display: false },
                border: { color: token("--line-2") },
                ticks: {
                  autoSkip: false, // a bar without its name says nothing
                  color: ink,
                  // On paper a row is PAPER.height / rows tall (about 12px for 68 stations): a size that fits it.
                  font: ({ chart }) => ({
                    weight: "600",
                    size: onPaper ? Math.max(7, Math.min(13, Math.floor(PAPER.height / chart.data.labels.length) - 3)) : 13,
                  }),
                  // Chart.js gives this axis at most half the width and cuts longer labels at the left edge:
                  // wrap a long name after its hyphens and spaces. The tooltip keeps the whole name. On paper
                  // (640px wide) the whole name fits on one line, and two would overprint the next row.
                  callback(value) {
                    if (onPaper) return this.getLabelForValue(value);
                    const lines = [];
                    for (const part of this.getLabelForValue(value).split(/(?<=[-\s])/)) {
                      if (lines.length && (lines[lines.length - 1] + part).length <= 18) lines[lines.length - 1] += part;
                      else lines.push(part);
                    }
                    return lines;
                  },
                },
              },
            },
          },
        }),
      );
    }
  }

  // Canvas text is drawn once: wait for Onest, or the labels stay in the fallback font.
  document.fonts.ready.then(draw);
  // A canvas keeps the pixels it was drawn with, and at beforeprint the page still has its screen layout: redraw
  // each chart at the paper's width and at most a page tall, so a long station comparison is not pushed whole to
  // the next sheet. Back to the screen size and labels at afterprint. update() applies the labels even when the
  // size stays the same (resize() alone would not redraw then).
  window.addEventListener("beforeprint", () => {
    onPaper = true;
    charts.forEach((chart) => {
      chart.update("none");
      chart.resize(PAPER.width, Math.min(chart.height, PAPER.height));
    });
  });
  window.addEventListener("afterprint", () => {
    onPaper = false;
    charts.forEach((chart) => {
      chart.update("none");
      chart.resize();
    });
  });
})();
