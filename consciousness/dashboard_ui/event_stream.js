/**
 * Client-side pub/sub over the dashboard SSE bus stream.
 * Uses mitt (https://github.com/developit/mitt) + native EventSource.
 */
(function initTradingDeskEventBus(global) {
  const emitter = global.mitt ? global.mitt() : createFallbackEmitter();

  let source = null;
  let reconnectTimer = null;
  let lastSeq = 0;
  let streamUrl = "";
  let disconnectCount = 0;
  let streamSupported = null;

  function createFallbackEmitter() {
    const all = new Map();
    return {
      on(type, handler) {
        const handlers = all.get(type) || [];
        handlers.push(handler);
        all.set(type, handlers);
      },
      off(type, handler) {
        const handlers = all.get(type) || [];
        all.set(type, handlers.filter((fn) => fn !== handler));
      },
      emit(type, payload) {
        for (const fn of all.get(type) || []) fn(payload);
        for (const fn of all.get("*") || []) fn(payload, type);
      },
    };
  }

  function buildUrl(params) {
    const search = new URLSearchParams();
    if (params.sinceSeq != null) search.set("since_seq", String(params.sinceSeq));
    if (params.topics && params.topics.length) search.set("topics", params.topics.join(","));
    return `/api/events/stream?${search.toString()}`;
  }

  function scheduleReconnect(delayMs) {
    if (reconnectTimer) return;
    reconnectTimer = global.setTimeout(() => {
      reconnectTimer = null;
      if (streamUrl) connect(streamUrl);
    }, delayMs);
  }

  async function probeStreamSupport() {
    if (streamSupported != null) return streamSupported;
    try {
      const response = await fetch("/api/events/health", { cache: "no-store" });
      if (!response.ok) {
        streamSupported = false;
        return false;
      }
      const payload = await response.json();
      streamSupported = Boolean(payload && payload.sse);
      return streamSupported;
    } catch (_err) {
      streamSupported = false;
      return false;
    }
  }

  function connect(url) {
    streamUrl = url;
    if (source) {
      source.close();
      source = null;
    }
    source = new EventSource(url);

    source.addEventListener("bus.connected", (message) => {
      disconnectCount = 0;
      try {
        const payload = JSON.parse(message.data);
        if (payload.since_seq != null) lastSeq = Math.max(lastSeq, Number(payload.since_seq) || 0);
      } catch (_err) {
        /* ignore malformed handshake */
      }
      emitter.emit("connected", { sinceSeq: lastSeq });
    });

    source.addEventListener("bus.event", (message) => {
      let event;
      try {
        event = JSON.parse(message.data);
      } catch (_err) {
        return;
      }
      if (event.seq != null) lastSeq = Math.max(lastSeq, Number(event.seq) || 0);
      emitter.emit("bus.event", event);
      if (event.topic) emitter.emit(`topic:${event.topic}`, event);
    });

    source.addEventListener("bus.heartbeat", () => {
      emitter.emit("heartbeat", { sinceSeq: lastSeq });
    });

    source.onerror = () => {
      disconnectCount += 1;
      emitter.emit("disconnected", { sinceSeq: lastSeq, attempts: disconnectCount });
      if (source) {
        source.close();
        source = null;
      }
      const delay = disconnectCount >= 5 ? 15000 : 3000;
      scheduleReconnect(delay);
    };
  }

  async function connectWithFilters({ sinceSeq, topics } = {}) {
    lastSeq = Number(sinceSeq || 0);
    const supported = await probeStreamSupport();
    if (!supported) {
      emitter.emit("unsupported", { sinceSeq: lastSeq });
      return;
    }
    connect(buildUrl({ sinceSeq: lastSeq, topics }));
  }

  function disconnect() {
    if (reconnectTimer) {
      global.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (source) {
      source.close();
      source = null;
    }
  }

  global.TradingDeskEventBus = {
    on: emitter.on.bind(emitter),
    off: emitter.off.bind(emitter),
    connectWithFilters,
    disconnect,
    probeStreamSupport,
    get lastSeq() {
      return lastSeq;
    },
    get streamHealthy() {
      return disconnectCount === 0 && source != null;
    },
  };
})(window);
