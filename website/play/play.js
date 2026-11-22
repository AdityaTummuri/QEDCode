// Fool the Grader: the page. Files live in memory per level; grading runs in worker.js.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const TIMEOUT_MS = 60000; // the first grade also loads Python
  const NEW_TEST = "tests/test_mine.py";
  let levels = [];
  let current = 0;
  let file = "";
  const state = {}; // level id -> { files: {path: text}, extra: [paths] }
  let worker = null;
  let pending = null;
  let nextId = 1;

  const store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode */ } },
  };
  const solved = new Set(store.get("qed-play-solved") || []);

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }

  function level() { return levels[current]; }
  function mine() {
    const lv = level();
    if (!state[lv.id]) state[lv.id] = { files: { ...lv.files }, extra: [] };
    return state[lv.id];
  }
  function visiblePaths() {
    const lv = level();
    return [lv.source, lv.tests, ...mine().extra];
  }

  function renderLevels() {
    const box = $("levels");
    box.replaceChildren();
    levels.forEach((lv, i) => {
      const b = el("button", `${i + 1}. ${lv.title}`);
      b.type = "button";
      b.setAttribute("aria-pressed", String(i === current));
      if (solved.has(lv.id)) b.classList.add("won");
      b.addEventListener("click", () => { saveEditor(); current = i; file = level().source; render(); });
      box.append(b);
    });
  }

  function renderTabs() {
    const tabs = $("tabs");
    tabs.replaceChildren();
    const lv = level();
    for (const path of visiblePaths()) {
      const b = el("button", path);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(path === file));
      if (mine().files[path] !== lv.files[path]) b.classList.add("changed");
      b.addEventListener("click", () => { saveEditor(); file = path; renderTabs(); loadEditor(); });
      tabs.append(b);
    }
  }

  function loadEditor() { $("editor").value = mine().files[file] ?? ""; }
  function saveEditor() { if (file && levels.length) mine().files[file] = $("editor").value; }

  function render() {
    const lv = level();
    renderLevels();
    const task = $("task");
    task.replaceChildren(el("b", "The task"), document.createTextNode(lv.task));
    if (!file || !visiblePaths().includes(file)) file = lv.source;
    renderTabs();
    loadEditor();
    $("result").replaceChildren(el("p", "Press Grade it to run the checks.", "muted"));
  }

  function edits() {
    saveEditor();
    const lv = level();
    const out = {};
    for (const [path, text] of Object.entries(mine().files)) if (lv.files[path] !== text) out[path] = text;
    return out;
  }

  // ---- shortcuts: the edits a model trying to look done sometimes makes
  function cheat(kind) {
    saveEditor();
    const lv = level();
    const files = mine().files;
    const tests = files[lv.tests];
    if (kind === "skip") {
      let text = tests.replace(/^def (test_\w+)/m, '@pytest.mark.skip(reason="flaky")\ndef $1');
      if (!/^import pytest/m.test(text)) text = "import pytest\n" + text;
      files[lv.tests] = text;
      file = lv.tests;
    } else if (kind === "delete") {
      files[lv.tests] = tests.replace(/^(\s+)assert .*$/m, "$1pass  # the assert that failed was removed");
      file = lv.tests;
    } else if (kind === "always") {
      files[lv.tests] = tests.replace(/^(\s+)assert (.*)$/m, "$1assert True or $2");
      file = lv.tests;
    } else if (kind === "hardcode") {
      for (const [path, text] of Object.entries(lv.hardcode)) {
        files[path] = text;
        if (!lv.files[path] && !mine().extra.includes(path)) mine().extra.push(path);
      }
      file = lv.source;
    }
    renderTabs();
    loadEditor();
    $("status").textContent = "Shortcut applied. Press Grade it.";
  }

  // ---- grading through the worker
  function startWorker() {
    worker = new Worker("worker.js");
    worker.onmessage = (event) => {
      const msg = event.data;
      if (msg.type === "status") $("status").textContent = msg.text;
      else if (msg.type === "ready") $("status").textContent = "Python is ready.";
      else if (pending && msg.id === pending.id) {
        clearTimeout(pending.timer);
        const done = pending;
        pending = null;
        $("grade").disabled = false;
        if (msg.type === "result") { $("status").textContent = ""; showResult(msg.result, done.levelId); }
        else showError(msg.text);
      }
    };
    worker.onerror = () => showError("The grader stopped. Reload the page to try again.");
  }

  function grade() {
    if (pending) return;
    if (!worker) startWorker();
    const id = nextId++;
    const lv = level();
    $("grade").disabled = true;
    $("status").textContent = $("status").textContent || "Grading...";
    pending = {
      id, levelId: lv.id,
      timer: setTimeout(() => {
        worker.terminate(); worker = null; pending = null; $("grade").disabled = false;
        showError("The tests ran too long (an endless loop?), so the grader was stopped. Change the code and grade again.");
      }, TIMEOUT_MS),
    };
    worker.postMessage({ id, level: { task: lv.task, files: lv.files, hidden: lv.hidden }, edits: edits() });
  }

  function showError(text) {
    $("grade").disabled = false;
    $("status").textContent = "";
    const box = $("result");
    box.classList.remove("won-card");
    box.replaceChildren(el("h2", "The grader could not finish", ""), el("pre", text, "details"));
  }

  function showResult(r, levelId) {
    const box = $("result");
    box.classList.toggle("won-card", !!r.won);
    box.replaceChildren();
    if (r.status === "no_changes") { box.append(el("p", r.message)); return; }
    const head = el("div", null, "head");
    const seal = el("span", r.grade || "✗", `seal ${r.grade || "none"}`);
    const title = el("div");
    const fooled = r.grade === "A" && r.hidden && r.hidden.ran && !r.hidden.passed;
    title.append(el("h2", r.won ? "Solved: a proven fix" : fooled ? "Grade A, yet still broken" : r.grade ? `Grade ${r.grade}` : "Rejected"));
    head.append(seal, title);
    box.append(head, el("p", r.summary, "line"));
    const list = el("ul");
    for (const s of r.stages) {
      const li = el("li", `${s.stage}: ${s.summary}`, s.passed ? "" : "no");
      list.append(li);
    }
    box.append(list);
    const scope = r.stages.find((s) => s.stage === "scope" && s.details);
    if (scope) box.append(el("pre", scope.details, "details"));
    for (const note of r.notes || []) box.append(el("p", note, "next"));
    if (r.next_step) box.append(el("p", `To raise the grade: ${r.next_step}`, "next"));
    const hidden = r.hidden && r.hidden.ran
      ? (r.hidden.passed ? "Hidden test: passes." : "Hidden test: FAILS. The visible checks were fooled, the hidden one was not; this is why benchmarks judge with tests the model never sees.")
      : "Hidden test: did not run.";
    box.append(el("p", hidden, "hidden-test"));
    if (r.won) {
      solved.add(levelId);
      store.set("qed-play-solved", [...solved]);
      renderLevels();
      const share = el("div", null, "row");
      if (r.link) {
        const a = el("a", "Open your receipt", "btn btn-primary");
        a.href = r.link.replace("https://adityatummuri.github.io/QEDCode/receipt/", "../receipt/");
        share.append(a);
      }
      const copy = el("button", "Copy a score card", "btn btn-ghost");
      copy.type = "button";
      copy.setAttribute("data-copy", `∎ I fixed "${level().title}" with a proven grade A in Fool the Grader. https://adityatummuri.github.io/QEDCode/play/`);
      share.append(copy);
      box.append(share);
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (button && navigator.clipboard) navigator.clipboard.writeText(button.getAttribute("data-copy")).then(() => { button.textContent = "Copied"; }, () => {});
  });
  document.querySelectorAll("[data-cheat]").forEach((b) => b.addEventListener("click", () => cheat(b.dataset.cheat)));
  $("grade").addEventListener("click", grade);
  $("reset").addEventListener("click", () => { delete state[level().id]; file = level().source; render(); });
  $("newtest").addEventListener("click", () => {
    saveEditor();
    const lv = level();
    if (!mine().extra.includes(NEW_TEST)) {
      mine().extra.push(NEW_TEST);
      const module = lv.source.replace(/^src\//, "").replace(/\.py$/, "").replace(/\//g, ".");
      mine().files[NEW_TEST] = `from ${module} import *  # replace with the function you test\n\n\ndef test_the_bug_is_fixed():\n    assert False  # write a check that fails on the old code\n`;
    }
    file = NEW_TEST;
    renderTabs();
    loadEditor();
  });
  let escaped = false; // Esc, then Tab, leaves the editor (keyboard users are never trapped)
  $("editor").addEventListener("keydown", (event) => {
    if (event.key === "Escape") { escaped = true; return; }
    if (event.key === "Tab" && !event.shiftKey && !event.altKey && !escaped) {
      event.preventDefault();
      const t = event.target, s = t.selectionStart;
      t.setRangeText("    ", s, t.selectionEnd, "end");
    }
    escaped = false;
  });

  fetch("levels.json").then((r) => r.json()).then((data) => {
    levels = data;
    const params = new URLSearchParams(location.search);
    const wanted = Number(params.get("level") || 1) - 1;
    current = wanted >= 0 && wanted < levels.length ? wanted : 0;
    file = levels[current].source;
    render();
    const shortcut = params.get("try"); // a shareable link: play/?try=skip shows that shortcut caught
    if (["skip", "delete", "always", "hardcode"].includes(shortcut)) { cheat(shortcut); grade(); }
  }).catch(() => showError("The levels could not be loaded."));
})();
