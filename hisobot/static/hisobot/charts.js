/* Charts of the report page (templates/hisobot/staff/report.html), Chart.js 4.
   Data comes from #chart-data (json_script); colours from the --chart-* tokens of app.css, read again when
   the colour scheme changes. The tables on the page carry every number: without Chart.js this file does
   nothing and the chart boxes keep their fallback text. */
(() => {
  "use strict";
  const source = document.getElementById("chart-data");
  if (typeof window.Chart === "undefined" || !source) return;

  const { Chart } = window;
  const data = JSON.parse(source.textContent);
  const names = data.names;
  const locale = document.documentElement.lang || undefined;
  let charts = [];

  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function canvasIn(id) {
    const box = document.getElementById(id);
    if (!box) return null;
    const canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    // The card title names the chart; the tables are its readable twin.
    const title = box.closest(".card").querySelector(".card-title").textContent;
    canvas.setAttribute("aria-label", `${title}. Рақамлар жадвалларда.`);
    box.replaceChildren(canvas);
    return { box, canvas };
  }

  function draw() {
    charts.forEach((chart) => chart.destroy());
    charts = [];
    const colours = ["--chart-1", "--chart-2", "--chart-3"].map(token);
    const grid = token("--chart-grid");
    Chart.defaults.color = token("--chart-text");
    Chart.defaults.borderColor = grid;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

    const base = { responsive: true, maintainAspectRatio: false, animation: false, locale };
    const valueAxis = { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { precision: 0 } };
    const quiet = { legend: { display: false } }; // the HTML legend under each chart names the series

    const daily = data.daily;
    const line = canvasIn("chart-daily");
    if (line) {
      const dots = daily.labels.length <= 14;
      const series = (key, colour) => ({
        label: names[key],
        data: daily[key],
        borderColor: colour,
        backgroundColor: colour,
        borderWidth: 2,
        borderJoinStyle: "round",
        borderCapStyle: "round",
        pointRadius: dots ? 4 : 0,
        pointHoverRadius: 5,
        pointHitRadius: 12,
        pointBorderColor: token("--surface"),
        pointBorderWidth: 2,
      });
      charts.push(
        new Chart(line.canvas, {
          type: "line",
          data: {
            labels: daily.labels,
            datasets: [
              series("unloaded", colours[0]),
              series("arrived", colours[1]),
              series("remaining", colours[2]),
            ],
          },
          options: {
            ...base,
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
                ticks: {
                  maxRotation: 0,
                  autoSkipPadding: 12,
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
    const bars = canvasIn("chart-compare");
    if (bars) {
      // Horizontal bars, so long station names stay readable: the box grows with the number of rows.
      const needed = compare.labels.length * 48 + 48;
      if (needed > bars.box.clientHeight) bars.box.style.height = `${needed}px`;
      const series = (key, colour) => ({
        label: names[key],
        data: compare[key],
        backgroundColor: colour,
        borderRadius: 4,
        borderSkipped: "start",
        maxBarThickness: 24,
        categoryPercentage: 0.7,
        barPercentage: 0.9,
      });
      charts.push(
        new Chart(bars.canvas, {
          type: "bar",
          data: {
            labels: compare.labels,
            datasets: [series("unloaded", colours[0]), series("arrived", colours[1])],
          },
          options: {
            ...base,
            indexAxis: "y",
            interaction: { mode: "index", axis: "y", intersect: false },
            plugins: quiet,
            scales: { x: valueAxis, y: { grid: { display: false }, border: { color: grid } } },
          },
        }),
      );
    }
  }

  draw();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
})();
