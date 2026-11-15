// DOM-level interaction checks without a browser or network listener.
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";
const source = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
async function setup(failDelete = false) {
  const nodes = new Map();
  function node(selector) {
    if (!nodes.has(selector))
      nodes.set(selector, {
        innerHTML: "",
        textContent: "",
        hidden: false,
        disabled: false,
        value: "recent",
        listeners: {},
        classList: { toggle() {} },
        addEventListener(name, handler) {
          this.listeners[name] = handler;
        },
        showModal() {
          this.open = true;
        },
        close() {
          this.open = false;
          this.listeners.close?.();
        },
      });
    return nodes.get(selector);
  }
  const calls = [];
  let rows = [];
  const context = vm.createContext({
    console,
    setTimeout() {
      return 0;
    },
    clearTimeout() {},
    navigator: { clipboard: { writeText: async () => {} } },
    document: {
      querySelector: node,
      querySelectorAll: () => [],
      addEventListener() {},
    },
    fetch: async (path, options = {}) => {
      calls.push({ path, ...options });
      if (options.method === "DELETE") {
        if (failDelete)
          return {
            ok: false,
            json: async () => ({ error: "文件已变化，请重新扫描" }),
          };
        rows = [];
        return { ok: true, json: async () => ({ ok: true }) };
      }
      const value =
        path === "/api/status"
          ? { sources: [], warnings: [], last_scan: null }
          : rows;
      return { ok: true, json: async () => value };
    },
  });
  vm.runInContext(source, context);
  await new Promise((resolve) => setImmediate(resolve));
  const row = {
    id: "test-session",
    revision: "1:2",
    title: "待清理会话",
    path: "/local/sessions/test.jsonl",
    cwd: "/local",
    updated: 0,
    provider: "codex",
    messages: [],
  };
  rows = [row];
  vm.runInContext(
    `state.rows = [${JSON.stringify(row)}]; state.detail = state.rows[0]; state.selected = state.detail.id; state.project = state.detail.cwd;`,
    context,
  );
  const open = () =>
    node("#detail").onclick({
      target: {
        closest: (selector) =>
          selector === "[data-action]"
            ? { dataset: { action: "delete" } }
            : null,
      },
    });
  return { node, calls, context, open };
}
test("opening and cancelling confirmation never sends DELETE", async () => {
  const app = await setup();
  await app.open();
  assert.equal(app.node("#delete-dialog").open, true);
  assert.equal(
    app.node("#delete-path").textContent,
    "/local/sessions/test.jsonl",
  );
  assert.equal(app.calls.filter((c) => c.method === "DELETE").length, 0);
  app.node("#cancel-delete").onclick();
  await app.node("#confirm-delete").onclick();
  assert.equal(app.calls.filter((c) => c.method === "DELETE").length, 0);
});
test("confirmed deletion sends revision and clears selection, project, and rows", async () => {
  const app = await setup();
  await app.open();
  await app.node("#confirm-delete").onclick();
  const sent = app.calls.find((c) => c.method === "DELETE");
  assert.equal(sent.path, "/api/sessions/test-session");
  assert.deepEqual(JSON.parse(sent.body), {
    confirm: "test-session",
    revision: "1:2",
  });
  assert.equal(sent.headers["X-ASH"], "1");
  assert.equal(vm.runInContext("state.selected", app.context), null);
  assert.equal(vm.runInContext("state.rows.length", app.context), 0);
  assert.equal(vm.runInContext("state.project", app.context), "");
  assert.equal(app.node("#delete-dialog").open, false);
  assert.match(app.node("#detail").innerHTML, /会话已删除/);
});
test("deletion failure keeps dialog and session, and re-enables retry", async () => {
  const app = await setup(true);
  await app.open();
  await app.node("#confirm-delete").onclick();
  assert.equal(app.node("#delete-dialog").open, true);
  assert.match(app.node("#delete-error").textContent, /文件已变化/);
  assert.equal(app.node("#delete-error").hidden, false);
  assert.equal(app.node("#confirm-delete").disabled, false);
  assert.equal(vm.runInContext("state.rows.length", app.context), 1);
  assert.equal(vm.runInContext("state.selected", app.context), "test-session");
});

test("session actions and provider messages use the correct local icon states", async () => {
  const app = await setup();
  vm.runInContext("renderDetail()", app.context);
  let markup = app.node("#detail").innerHTML;
  assert.match(markup, /icon--star"/);
  assert.match(markup, /icon--archive"/);
  assert.match(markup, /icon--trash"/);
  assert.match(markup, /aria-label="永久删除会话文件"/);
  vm.runInContext(
    `state.detail.favorite = true; state.detail.archived = true;
    state.detail.provider = 'claude';
    state.detail.messages = [{role:'assistant',text:'<script>test</script>'}];
    renderDetail();`,
    app.context,
  );
  markup = app.node("#detail").innerHTML;
  assert.match(markup, /icon--star-filled"/);
  assert.match(markup, /icon--unarchive"/);
  assert.match(markup, /icon--claude"/);
  assert.match(markup, /aria-label="取消归档"/);
  assert.match(markup, /&lt;script&gt;test&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /[★☆▣⇩⌫✳]/);
});
