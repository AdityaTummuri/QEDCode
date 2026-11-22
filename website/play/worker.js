// Runs QEDCode's grader in Pyodide, off the page's main thread (an endless loop in a player's
// code can then be stopped by ending this worker). Everything is fetched from this site and the
// Pyodide CDN once; no code or result is sent anywhere.
/* global importScripts, loadPyodide */
importScripts("https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js");

let booting = null;

async function boot() {
  postMessage({ type: "status", text: "Loading Python in your browser (about 15 MB, only the first time)..." });
  const pyodide = await loadPyodide();
  postMessage({ type: "status", text: "Loading pytest and pydantic..." });
  await pyodide.loadPackage(["pytest", "pydantic"]);
  postMessage({ type: "status", text: "Loading QEDCode's grading rules..." });
  const manifest = await (await fetch("py/manifest.json")).json();
  const files = manifest.map((rel) => ["py/" + rel, "/lib/" + rel]).concat([["grader.py", "/lib/grader.py"]]);
  for (const [url, target] of files) {
    const text = await (await fetch(url)).text();
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, text);
  }
  pyodide.runPython("import sys\nsys.path.insert(0, '/lib')\nimport grader");
  postMessage({ type: "ready" });
  return pyodide;
}

onmessage = async (event) => {
  const { id, level, edits } = event.data;
  try {
    booting = booting || boot();
    const pyodide = await booting;
    const run = pyodide.globals.get("grader").main_json;
    const out = run(JSON.stringify({ level, edits }));
    postMessage({ type: "result", id, result: JSON.parse(out) });
  } catch (error) {
    postMessage({ type: "error", id, text: String(error && error.message ? error.message : error).slice(-1500) });
  }
};
