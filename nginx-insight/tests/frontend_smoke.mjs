// Dependency-free interaction smoke test; this does not replace browser layout QA.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const root = new URL("../", import.meta.url);
const html = fs.readFileSync(
  new URL("insight/templates/index.html", root),
  "utf8",
);
class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this._text = "";
    this.listeners = {};
    this.classes = new Set();
    this.classList = {
      add: (c) => this.classes.add(c),
      remove: (c) => this.classes.delete(c),
      toggle: (c, force) =>
        force ? this.classes.add(c) : this.classes.delete(c),
    };
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }
  set className(value) {
    this.classes = new Set(value.split(" "));
  }
  append(...children) {
    for (const child of children) {
      if (child.parentNode)
        child.parentNode.children = child.parentNode.children.filter(
          (item) => item !== child,
        );
      child.parentNode = this;
      this.children.push(child);
    }
  }
  insertBefore(child, reference) {
    child.parentNode = this;
    this.children.splice(this.children.indexOf(reference), 0, child);
  }
  get options() {
    return this.children;
  }
  get scrollHeight() {
    return this.children.length * 44;
  }
  contains(target) {
    return (
      target === this || this.children.some((child) => child.contains(target))
    );
  }
  getBoundingClientRect() {
    return { top: 100, bottom: 140, left: 20, right: 220 };
  }
  scrollIntoView() {}
  focus() {
    this.focused = true;
  }
  dispatchEvent(event) {
    this["on" + event.type]?.(event);
    this.listeners[event.type]?.(event);
    return true;
  }
  replaceChildren(...children) {
    this._text = "";
    this.children = [...children];
  }
  setAttribute(key, value) {
    this.attributes[key] = value;
  }
  removeAttribute(key) {
    delete this.attributes[key];
  }
  addEventListener(key, fn) {
    this.listeners[key] = fn;
  }
  querySelector(selector) {
    return this.children.find((c) => c.tag === selector) || null;
  }
  querySelectorAll(selector) {
    return this.children.filter((c) => c.tag === selector);
  }
  showModal() {
    this.open = true;
  }
  close() {
    this.open = false;
  }
  click() {
    this.onclick?.();
  }
}
const nodes = new Map();
for (const match of html.matchAll(/<([\w-]+)[^>]*\bid="([^"]+)"[^>]*>/g)) {
  assert(!nodes.has(match[2]), "Duplicate HTML id " + match[2]);
  nodes.set(match[2], new Element(match[1]));
}
for (const match of html.matchAll(
  /<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select\s*>/g,
)) {
  const select = nodes.get(match[1]),
    label = new Element("label");
  label.append(select);
  for (const option of match[2].matchAll(
    /<option\b[^>]*value="([^"]*)"[^>]*>([^<]*)<\/option>/g,
  )) {
    const item = new Element("option");
    item.value = option[1];
    item.textContent = option[2];
    select.append(item);
  }
}
const get = (id) => {
  assert(nodes.has(id), "Missing HTML id " + id);
  return nodes.get(id);
};
get("hours").value = "24";
get("log-kind").value = "error";
get("auth-button").append(new Element("span"));
const live = new Element(),
  tabs = new Element("nav");
const panels = ["overview", "logs", "locations", "traffic"].map((panel) => {
  const button = new Element("button");
  button.dataset.panel = panel;
  tabs.append(button);
  return button;
});
for (const region of ["all", "domestic", "overseas"]) {
  const button = new Element("button");
  button.dataset.region = region;
  get("ranking-tabs").append(button);
}
const charts = [];
const echarts = {
  init: (element) => {
    const chart = {
      options: [],
      setOption(value) {
        this.options.push(value);
      },
      resize() {},
      on() {},
      dispatchAction() {},
      getDataURL() {
        return "data:image/png;base64,test";
      },
    };
    charts.push(chart);
    return chart;
  },
  registerMap() {},
  graphic: { LinearGradient: class {} },
};
let authenticated = false;
const requests = [];
const stats = {
  total: 20,
  unique_ips: 4,
  success_rate: 95,
  average_ms: 17,
  traffic_bytes: 1024,
  failures: 1,
  growth: 100,
  updated_at: "2026-11-11T10:00:00",
  sites: ["one.test"],
  site_stats: [
    { site: "one.test", requests: 20, average_ms: 17, server_errors: 1 },
  ],
  regions: { domestic: 15, overseas: 5, unknown: 0 },
  provinces: [
    { name: "广东省", value: 15 },
    { name: "境外 / England", value: 5 },
  ],
  trend: [{ time: "2026-11-11T09:00:00", value: 20 }],
  statuses: [
    { status: 200, value: 19 },
    { status: 503, value: 1 },
  ],
};
const overview = {
  states: { pending: 1, retry: 1, resolved: 4 },
  due: 2,
  malformed: 0,
  cursors: [{ path: "/var/log/nginx/access.log", offset: 2048, generation: 0 }],
  settings: { geo_qps: "1", log_time: "日志原始时间", whitelist: "" },
};
const context = {
  console,
  Event: class {
    constructor(type) {
      this.type = type;
    }
  },
  Node: Element,
  Option: class extends Element {
    constructor(text, value) {
      super("option");
      this.textContent = text;
      this.value = value;
    }
  },
  URLSearchParams,
  AbortController,
  JSON,
  Intl,
  Date,
  Number,
  String,
  Math,
  Set,
  Error,
  Promise,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  requestAnimationFrame: (fn) => fn(),
  location: { search: "" },
  history: { replaceState() {} },
  echarts,
  document: {
    listeners: [],
    addEventListener(type, fn) {
      this.listeners.push([type, fn]);
    },
    getElementById: get,
    createElement: (tag) => new Element(tag),
    createElementNS: (ns, tag) => {
      const e = new Element(tag);
      e.namespaceURI = ns;
      return e;
    },
    createTextNode: (text) => {
      const e = new Element("text");
      e.textContent = text;
      return e;
    },
    querySelector: (selector) =>
      selector === ".live-indicator"
        ? live
        : selector === ".workspace-tabs"
          ? tabs
          : null,
    querySelectorAll: (selector) =>
      selector === ".workspace-tabs button" ? panels : [],
    hidden: false,
  },
  fetch: async (path, options = {}) => {
    assert.ok(
      !path.startsWith("/"),
      "Requests must preserve the deployment subpath",
    );
    path = "/" + path;
    requests.push([path, options]);
    let data;
    const route = path.split("?")[0];
    if (route === "/api/auth")
      data = {
        authenticated,
        configured: true,
        csrf_token: "test-csrf",
        username: authenticated ? "admin" : null,
      };
    else if (route === "/api/auth/login") {
      authenticated = true;
      data = { authenticated: true, username: "admin", csrf_token: "new-csrf" };
    } else if (route === "/api/auth/logout") {
      authenticated = false;
      data = { authenticated: false };
    } else if (route === "/api/stats") data = stats;
    else if (route === "/static/china.json")
      data = {
        features: [
          {
            properties: { name: "广东省" },
            geometry: { type: "Polygon", coordinates: [] },
          },
        ],
      };
    else if (route === "/api/admin/overview") data = overview;
    else if (route === "/api/admin/logs")
      data = {
        page: 1,
        page_size: 20,
        total: 1,
        items: [
          {
            time: "2026-11-11T09:00:00",
            site: "one.test",
            ip: "1.2.3.4",
            kind: "error",
            level: "error",
            raw: "<script>private detail</script>",
            request_time: null,
          },
        ],
      };
    else if (route === "/api/admin/locations")
      data = {
        page: 1,
        page_size: 20,
        total: 1,
        items: [
          { ip: "1.2.3.4", state: "retry", attempts: 1, province: "未知" },
        ],
      };
    else if (route === "/api/admin/traffic")
      data = { slow_count: 0, paths: [], ips: [] };
    else if (route === "/api/admin/locations/retry") data = { affected: 1 };
    else throw Error("Unexpected URL " + path);
    return { ok: true, status: 200, json: async () => data };
  },
};
context.window = {
  innerHeight: 720,
  echarts,
  matchMedia: () => ({ matches: true }),
  addEventListener() {},
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(new URL("insight/static/app.js", root), "utf8"),
  context,
);
const flush = async () => {
  for (let i = 0; i < 25; i++) await Promise.resolve();
};
await flush();
assert.equal(get("total").textContent, "20");
assert.equal(get("site-rows").children.length, 1);
assert.equal(get("ranking").children.length, 2);
assert.equal(
  requests.filter(([url]) => url.startsWith("/api/admin/")).length,
  0,
  "Anonymous startup fetched private data",
);
const siteWidget = vm.runInContext('selectWidgets.get("site")', context);
const timeWidget = vm.runInContext('selectWidgets.get("hours")', context);
assert.equal(
  get("site").hidden,
  true,
  "Native mobile select must not open its OS popup",
);
siteWidget.trigger.onclick();
assert.equal(siteWidget.menu.hidden, false);
assert.equal(siteWidget.trigger.attributes["aria-expanded"], "true");
siteWidget.trigger.onkeydown({ key: "End", preventDefault() {} });
siteWidget.trigger.onkeydown({ key: "Enter", preventDefault() {} });
await flush();
assert.equal(get("site").value, "one.test");
assert.equal(siteWidget.menu.hidden, true);
assert(
  requests.some(
    ([url]) => url.startsWith("/api/stats?") && url.includes("site=one.test"),
  ),
);
timeWidget.trigger.onclick();
assert.equal(timeWidget.menu.hidden, false);
timeWidget.trigger.onkeydown({ key: "Escape" });
assert.equal(timeWidget.menu.hidden, true);
siteWidget.trigger.getBoundingClientRect = () => ({ top: 650, bottom: 690 });
siteWidget.trigger.onclick();
assert(siteWidget.trigger.parentNode.classes.has("opens-up"));
for (const [type, fn] of context.document.listeners)
  if (type === "pointerdown") fn({ target: new Element() });
assert.equal(
  siteWidget.menu.hidden,
  true,
  "Outside touch must close the popup",
);
const unsortedStatuses = [
  { status: 503, value: 1 },
  { status: 404, value: 4 },
  { status: 200, value: 19 },
  { status: 301, value: 4 },
];
vm.runInContext(
  `renderStats(${JSON.stringify({ ...stats, statuses: unsortedStatuses })})`,
  context,
);
const sortedPie = charts[1].options.at(-1);
assert.deepEqual(
  Array.from(sortedPie.series[0].data, (item) => item.name),
  ["200", "301", "404", "503"],
);
assert.deepEqual(Array.from(sortedPie.legend.data), [
  "200",
  "301",
  "404",
  "503",
]);
const originalColor = sortedPie.series[0].data.find(
  (item) => item.name === "200",
).itemStyle.color;
vm.runInContext(
  `renderStats(${JSON.stringify({
    ...stats,
    statuses: [
      { status: 404, value: 25 },
      { status: 200, value: 1 },
    ],
  })})`,
  context,
);
assert.equal(
  charts[1].options.at(-1).series[0].data.find((item) => item.name === "200")
    .itemStyle.color,
  originalColor,
);
get("map-reset").onclick();
const mapChart = charts[2];
assert.equal(mapChart.options[0].series[0].layoutSize, "88%");
assert.deepEqual(
  mapChart.options.at(-1).series[0].layoutCenter,
  mapChart.options[0].series[0].layoutCenter,
);
assert.equal(mapChart.options.at(-1).series[0].zoom, 1);
get("admin-nav").onclick();
assert.equal(get("login-dialog").open, true);
get("username").value = "admin";
get("password").value = "password";
await get("login-form").onsubmit({ preventDefault() {} });
await flush();
assert.equal(get("admin-view").hidden, false);
assert.equal(get("admin-metrics").children.length, 4);
assert.equal(get("site").disabled, false);
assert.equal(get("hours").disabled, false);
get("site").value = "one.test";
get("hours").value = "168";
for (const button of panels) {
  tabs.listeners.click({ target: { closest: () => button } });
  await flush();
  assert.equal(get("admin-" + button.dataset.panel).hidden, false);
}
assert(
  requests.some(
    ([url]) =>
      url.startsWith("/api/admin/logs?") &&
      url.includes("site=one.test") &&
      url.includes("hours=168"),
  ),
);
assert.equal(get("site").disabled, false);
assert.equal(get("hours").disabled, false);
assert(
  get("log-results").textContent.includes("<script>private detail</script>"),
  "Raw log should be rendered as text",
);
get("retry-all").onclick();
assert.equal(get("confirm-dialog").open, true);
await get("confirm-retry").onclick();
await flush();
assert(
  requests.some(
    ([url, options]) =>
      url.endsWith("/retry") && options.headers["X-CSRF-Token"] === "new-csrf",
  ),
);
await get("auth-button").onclick();
await flush();
assert.equal(get("admin-view").hidden, true);
assert.equal(get("log-results").children.length, 0);
assert.equal(get("location-rows").children.length, 0);
console.log(
  "Frontend smoke passed: anchored select menus, keyboard selection/Escape, outside touch, upward placement, filter requests, public startup, login/admin tabs, retry and logout.",
);

// Verify the actual GeoJSON projection with ECharts' SVG renderer at desktop
// and mobile sizes; mocked charts cannot detect clipped geographic bounds.
const { createRequire } = await import("node:module");
const require = createRequire(import.meta.url);
const realECharts = require("../insight/static/echarts.min.js");
const china = JSON.parse(
  fs.readFileSync(new URL("insight/static/china.json", root), "utf8"),
);
realECharts.registerMap("china-bounds-test", china);
const defaultView = JSON.parse(
  vm.runInContext("JSON.stringify(defaultMapView())", context),
);
for (const [width, height] of [
  [900, 510],
  [650, 450],
  [420, 390],
  [290, 350],
]) {
  const chart = realECharts.init(null, null, {
    renderer: "svg",
    ssr: true,
    width,
    height,
  });
  try {
    chart.setOption({
      animation: false,
      series: [{ type: "map", map: "china-bounds-test", ...defaultView }],
    });
    const coordinateSystem = chart
      .getModel()
      .getSeriesByIndex(0).coordinateSystem;
    const check = (coordinates) => {
      if (typeof coordinates[0] === "number") {
        const [x, y] = coordinateSystem.dataToPoint(coordinates);
        assert(
          x >= 8 && x <= width - 8 && y >= 8 && y <= height - 8,
          `Map clipped at ${width}x${height}: ${x},${y}`,
        );
      } else coordinates.forEach(check);
    };
    china.features
      .filter((feature) => feature.geometry)
      .forEach((feature) => check(feature.geometry.coordinates));
  } finally {
    chart.dispose();
  }
}
console.log("Actual ECharts map bounds passed at four desktop/mobile sizes.");

assert.equal(vm.runInContext('localTime("2026-11-13T06:49:44")', context), "2026-11-13 06:49:44");
assert.equal(vm.runInContext('compactTime("2026-11-13T06:49:44")', context), "11-13 06:49");
