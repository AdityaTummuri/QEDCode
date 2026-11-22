// QEDCode Receipt Viewer. A receipt is untrusted data: every value goes in with textContent,
// never as HTML. The receipt comes from the URL fragment (#r=<gzip+base64url>), a dropped file,
// or ?sample. Nothing is sent anywhere.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STAGES = ["apply", "parse", "scope", "risk", "lint", "fbpa", "targeted", "full"];
  const NAMES = {
    apply: "apply: the edits land exactly", parse: "parse: valid syntax",
    scope: "scope: no protected or weakened tests", risk: "risk: no risky code without approval",
    lint: "lint: no new serious errors", fbpa: "a test failed before and passes after",
    targeted: "related tests", full: "full suite: no regressions",
  };
  let schema = null;

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }

  function fail(message) {
    $("view").hidden = true;
    const box = $("problem");
    box.replaceChildren(el("h2", "This receipt cannot be shown"), el("p", message));
    box.hidden = false;
  }

  function b64urlToBytes(text) {
    const clean = text.replace(/-/g, "+").replace(/_/g, "/");
    const padded = clean + "=".repeat((4 - (clean.length % 4)) % 4);
    const raw = atob(padded);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  async function gunzip(bytes) {
    if (!("DecompressionStream" in window)) throw new Error("This browser cannot decompress links; open the receipt file instead.");
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return await new Response(stream).text();
  }

  async function sha256(text) {
    if (!(window.crypto && crypto.subtle)) return null;
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  // A small JSON Schema check for the shapes pydantic writes: type, required, properties,
  // items, $ref, anyOf, enum, const. Returns the first problem, or "".
  function check(value, node, path) {
    if (!node) return "";
    if (node.$ref) return check(value, schema.$defs[node.$ref.split("/").pop()], path);
    if (node.anyOf) {
      return node.anyOf.some((alt) => check(value, alt, path) === "") ? "" : `${path} has the wrong shape`;
    }
    if ("const" in node && value !== node.const) return `${path} must be ${JSON.stringify(node.const)}`;
    if (node.enum && !node.enum.includes(value)) return `${path} must be one of ${node.enum.join(", ")}`;
    const t = node.type;
    const kinds = { string: "string", integer: "number", number: "number", boolean: "boolean" };
    if (t === "object" || node.properties) {
      if (typeof value !== "object" || value === null || Array.isArray(value)) return `${path} must be an object`;
      for (const key of node.required || []) if (!(key in value)) return `${path}.${key} is missing`;
      for (const [key, sub] of Object.entries(node.properties || {})) {
        if (key in value) { const p = check(value[key], sub, `${path}.${key}`); if (p) return p; }
      }
      return "";
    }
    if (t === "array") {
      if (!Array.isArray(value)) return `${path} must be a list`;
      for (let i = 0; i < value.length; i++) { const p = check(value[i], node.items, `${path}[${i}]`); if (p) return p; }
      return "";
    }
    if (t === "null") return value === null ? "" : `${path} must be null`;
    if (kinds[t] && typeof value !== kinds[t]) return `${path} must be a ${t}`;
    if (t === "integer" && !Number.isInteger(value)) return `${path} must be a whole number`;
    return "";
  }

  function testCounts(checks) {
    for (const stage of ["full", "targeted"]) {
      for (let i = checks.length - 1; i >= 0; i--) {
        const c = checks[i];
        if (c.stage === stage && c.tests && c.tests.length) {
          const passed = c.tests.filter((t) => t.outcome === "passed").length;
          const total = c.tests.filter((t) => ["passed", "failed", "error"].includes(t.outcome)).length;
          return [passed, total];
        }
      }
    }
    return [0, 0];
  }

  function renderDiff(patch) {
    const box = $("diff");
    box.replaceChildren();
    for (const line of patch.split("\n")) {
      let cls = "";
      if (line.startsWith("diff --git")) cls = "file";
      else if (line.startsWith("@@")) cls = "hunk";
      else if (line.startsWith("+") && !line.startsWith("+++")) cls = "add";
      else if (line.startsWith("-") && !line.startsWith("---")) cls = "del";
      box.append(el("span", line || " ", cls));
    }
  }

  async function show(receipt, source) {
    $("problem").hidden = true;
    const problem = schema ? check(receipt, schema, "receipt") : "";
    if (problem) return fail(`It does not match the receipt schema: ${problem}.`);
    const status = $("status");
    status.replaceChildren();
    status.append(el("span", `from ${source}`, "pill"));
    status.append(el("span", schema ? "schema: valid" : "schema: not checked", schema ? "pill ok" : "pill"));
    const digest = await sha256(receipt.patch);
    if (digest === null) status.append(el("span", "checksum: needs https", "pill"));
    else if (digest === receipt.patch_sha256) status.append(el("span", "patch checksum: matches", "pill ok"));
    else status.append(el("span", "patch checksum: MISMATCH (the patch was edited)", "pill bad"));

    const seal = $("seal");
    seal.textContent = receipt.grade;
    seal.className = `seal ${receipt.grade}`;
    $("task").textContent = receipt.task.request;
    const [passed, total] = testCounts(receipt.checks);
    const parts = [`Grade ${receipt.grade}`];
    if (receipt.fbpa_tests.length) parts.push("repro test failed before, passes now");
    parts.push(total ? `${passed}/${total} tests pass` : "no tests ran");
    if (receipt.mutation && receipt.mutation.total) parts.push(`proof strength ${receipt.mutation.killed}/${receipt.mutation.total}`);
    $("summary").textContent = parts.join(" · ");

    const steps = $("steps");
    steps.replaceChildren();
    for (const stage of STAGES) {
      const ran = receipt.checks.filter((c) => c.stage === stage);
      const last = ran[ran.length - 1];
      const state = !last ? "skip" : last.passed ? "ok" : "no";
      const li = el("li", null, state);
      li.append(el("span", state === "ok" ? "✓" : state === "no" ? "✗" : "–", "ic"));
      const body = el("span", NAMES[stage]);
      body.append(el("small", last ? last.summary : "did not run"));
      li.append(body);
      steps.append(li);
    }
    const fbpa = $("fbpa");
    fbpa.replaceChildren(...(receipt.fbpa_tests.length ? receipt.fbpa_tests.map((k) => el("li", k)) : [el("li", "None: nothing proved the exact problem.")]));
    const m = receipt.mutation;
    $("strength-title").hidden = $("strength").hidden = !(m && m.total);
    if (m && m.total) {
      $("strength").textContent = `The new tests caught ${m.killed} of ${m.total} small breakages of the changed lines.` + (m.survivors.length ? ` Survived: ${m.survivors.join("; ")}.` : "");
    }
    $("gaps").replaceChildren(...(receipt.unverified.length ? receipt.unverified.map((g) => el("li", g)) : [el("li", "Nothing listed.")]));
    const meta = $("meta");
    meta.replaceChildren();
    const rows = [
      ["project", receipt.project_name], ["made", `${receipt.created_at} by QEDCode ${receipt.qed_version}`],
      ["base commit", receipt.base_commit], ["model", receipt.model_info && receipt.model_info.model ? receipt.model_info.model : "none (qed check)"],
      ["sealed test box", receipt.sandbox ? "yes" : "no"], ["patch sha256", receipt.patch_sha256],
    ];
    for (const [k, v] of rows) { meta.append(el("dt", k), el("dd", v)); }
    renderDiff(receipt.patch);
    $("intro").hidden = true;
    $("view").hidden = false;
    document.title = `Grade ${receipt.grade}: ${receipt.task.request.slice(0, 60)} · Receipt Viewer`;
  }

  async function parseAndShow(text, source) {
    let receipt;
    try { receipt = JSON.parse(text); } catch (_) { return fail("It is not JSON. Pick a file from .qed/receipts/."); }
    await show(receipt, source);
  }

  async function fromFragment() {
    const hash = location.hash;
    if (!hash.startsWith("#r=")) return false;
    try {
      const text = await gunzip(b64urlToBytes(hash.slice(3).split("&")[0]));
      await parseAndShow(text, "the link (nothing was downloaded)");
    } catch (error) {
      fail("The link is damaged: copy the whole link, including everything after '#r='. " + (error && error.message ? `(${error.message})` : ""));
    }
    return true;
  }

  async function start() {
    try {
      const response = await fetch("receipt.schema.json", { cache: "force-cache" });
      if (response.ok) schema = await response.json();
    } catch (_) { schema = null; }
    if (await fromFragment()) return;
    if (new URLSearchParams(location.search).has("sample")) {
      const response = await fetch("sample.json");
      if (response.ok) return parseAndShow(await response.text(), "the sample (a qed try run)");
      return fail("The sample receipt is missing from this copy of the site.");
    }
  }

  const drop = $("drop");
  const read = (file) => file && file.text().then((text) => parseAndShow(text, file.name));
  $("file").addEventListener("change", (event) => read(event.target.files[0]));
  drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (event) => { event.preventDefault(); drop.classList.remove("over"); read(event.dataTransfer.files[0]); });
  window.addEventListener("hashchange", fromFragment);
  start();
})();
