/**
 * Equity sparkline — Chart.js line chart fed by portfolio snapshot + live bus events.
 */
(function initEquitySparkline(global) {
  let chart = null;
  let series = [];

  function getChart() {
    return global.Chart;
  }

  function canvas() {
    return document.getElementById("portfolio-equity-sparkline");
  }

  function trendColor(values) {
    if (!values.length) return "#6b8cae";
    const first = values[0];
    const last = values[values.length - 1];
    if (last > first) return "#3dd68c";
    if (last < first) return "#f07178";
    return "#6b8cae";
  }

  function normalizePoints(points) {
    return (points || [])
      .map((row) => ({
        ts: Number(row.ts || 0),
        equity: Number(row.equity),
      }))
      .filter((row) => !Number.isNaN(row.equity) && row.equity > 0)
      .sort((a, b) => a.ts - b.ts);
  }

  function destroy() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
  }

  function renderMeta(metaNode, values, changePct) {
    if (!metaNode) return;
    if (!values.length) {
      metaNode.textContent = "Equity history will appear after trades or broker sync.";
      return;
    }
    const latest = values[values.length - 1];
    const changeText =
      changePct == null || Number.isNaN(changePct)
        ? ""
        : ` (${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%)`;
    metaNode.textContent = `Latest ${latest.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}${changeText} · ${values.length} point${values.length === 1 ? "" : "s"}`;
  }

  function mount(points, meta) {
    const Chart = getChart();
    const node = canvas();
    if (!Chart || !node) return;

    series = normalizePoints(points);
    const values = series.map((row) => row.equity);
    const labels = series.map((row) =>
      row.ts ? new Date(row.ts * 1000).toLocaleTimeString() : ""
    );
    const stroke = trendColor(values);
    const changePct =
      values.length >= 2 && values[0] > 0 ? ((values[values.length - 1] - values[0]) / values[0]) * 100 : null;

    destroy();

    const ctx = node.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 0, node.height || 80);
    gradient.addColorStop(0, `${stroke}55`);
    gradient.addColorStop(1, `${stroke}08`);

    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            data: values,
            borderColor: stroke,
            backgroundColor: gradient,
            borderWidth: 2,
            pointRadius: values.length <= 2 ? 2 : 0,
            pointHoverRadius: 3,
            fill: true,
            tension: 0.35,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(context) {
                const value = context.parsed.y;
                return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
              },
            },
          },
        },
        scales: {
          x: { display: false },
          y: { display: false },
        },
      },
    });

    const metaNode = document.getElementById("portfolio-equity-sparkline-meta");
    renderMeta(metaNode, values, changePct);
    if (meta && metaNode && meta.message) {
      metaNode.textContent = `${metaNode.textContent} · ${meta.message}`;
    }
  }

  function appendLivePoint(payload) {
    const equity = Number(payload?.equity);
    if (Number.isNaN(equity) || equity <= 0) return;
    const ts = Number(payload?.ts || Date.now() / 1000);
    const next = normalizePoints([...series, { ts, equity, live: true }]);
    if (next.length > 120) next.splice(0, next.length - 120);
    mount(next);
  }

  global.EquitySparkline = {
    destroy,
    mount,
    appendLivePoint,
  };
})(window);
