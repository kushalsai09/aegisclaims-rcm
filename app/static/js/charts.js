(function () {
  const instances = new Map();
  const palette = ["#22d3ee", "#8b5cf6", "#34d399", "#fb7185", "#fbbf24", "#60a5fa"];

  function replaceChart(id, configuration) {
    if (typeof Chart === "undefined") return null;
    instances.get(id)?.destroy();
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    const chart = new Chart(canvas.getContext("2d"), configuration);
    instances.set(id, chart);
    return chart;
  }

  function baseLegend() {
    return {
      labels: {
        color: "#71717a",
        usePointStyle: true,
        pointStyle: "circle",
        padding: 18,
        font: { size: 10, weight: "600" },
      },
    };
  }

  function renderPayerDistribution(payers) {
    return replaceChart("payer-denial-chart", {
      type: "doughnut",
      data: {
        labels: payers.map((payer) => payer.payer_key),
        datasets: [{
          data: payers.map((payer) => payer.claim_count),
          backgroundColor: palette,
          borderColor: "#0c1118",
          borderWidth: 4,
          hoverOffset: 8,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "72%",
        layout: { padding: { top: 4, right: 12, bottom: 8, left: 12 } },
        plugins: {
          legend: { position: "bottom", ...baseLegend() },
          tooltip: tooltipOptions(),
        },
      },
    });
  }

  function renderRecoveryTrend(trends) {
    return replaceChart("recovery-trend-chart", {
      data: {
        labels: trends.map((item) => formatPeriod(item.period)),
        datasets: [
          {
            type: "bar",
            label: "Billed",
            data: trends.map((item) => item.billed_revenue),
            backgroundColor: "rgba(139,92,246,.34)",
            borderColor: "#8b5cf6",
            borderWidth: 1,
            borderRadius: 5,
          },
          {
            type: "line",
            label: "Recovered",
            data: trends.map((item) => item.recovered_revenue),
            borderColor: "#34d399",
            backgroundColor: "#34d399",
            pointRadius: 3,
            pointHoverRadius: 5,
            tension: .35,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 4, right: 20, bottom: 10, left: 8 } },
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { grid: { display: false }, ticks: { color: "#52525b", autoSkip: true, maxTicksLimit: 12, maxRotation: 0, padding: 6, font: { size: 9 } } },
          y: { beginAtZero: true, grid: { color: "rgba(255,255,255,.05)" }, ticks: { color: "#52525b", callback: (value) => `$${value}` } },
        },
        plugins: { legend: { position: "bottom", ...baseLegend() }, tooltip: tooltipOptions() },
      },
    });
  }

  function renderDenialHeatmap(container, trends) {
    if (!container) return;
    const payers = [...new Set(trends.map((item) => item.payer_key))];
    const codes = [...new Set(trends.map((item) => item.denial_code))];
    const counts = new Map(trends.map((item) => [`${item.payer_key}|${item.denial_code}`, item.claim_count]));
    const maximum = Math.max(1, ...counts.values());
    container.style.setProperty("--heat-columns", codes.length || 1);
    container.innerHTML = `<div class="heatmap-corner">PAYER / CODE</div>${codes.map((code) => `<div class="heatmap-heading">${escapeValue(code)}</div>`).join("")}${payers.map((payer) => `<div class="heatmap-payer">${escapeValue(payer)}</div>${codes.map((code) => { const count = counts.get(`${payer}|${code}`) || 0; const intensity = count / maximum; return `<div class="heatmap-cell" style="--heat:${intensity}" title="${escapeValue(payer)} ${escapeValue(code)}: ${count}">${count || "—"}</div>`; }).join("")}`).join("")}`;
  }

  function tooltipOptions() {
    return {
      backgroundColor: "#18181b",
      borderColor: "rgba(255,255,255,.1)",
      borderWidth: 1,
      titleColor: "#fff",
      bodyColor: "#a1a1aa",
      padding: 12,
    };
  }

  function formatPeriod(period) {
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${period}T00:00:00Z`));
  }

  function escapeValue(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
  }

  // Read-only access supports deterministic accessibility and demo tooling.
  function getChart(id) {
    return instances.get(id) || null;
  }

  window.RCMCharts = { renderPayerDistribution, renderRecoveryTrend, renderDenialHeatmap, getChart };
})();
