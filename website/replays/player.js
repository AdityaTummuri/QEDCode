// A tiny player for asciinema v2 recordings (.cast): plays the terminal output in a <pre>,
// with the colours QEDCode prints. No dependency. Text goes in with textContent only.
(function () {
  "use strict";
  const COLOURS = { 31: "t-bad", 32: "t-ok", 33: "t-warn", 36: "t-info", 91: "t-bad", 92: "t-ok", 95: "t-info", 96: "t-info" };

  function parse(text) {
    const lines = text.split("\n").filter(Boolean);
    const header = JSON.parse(lines[0]);
    const events = lines.slice(1).map((l) => JSON.parse(l)).filter((e) => e[1] === "o");
    return { header, events };
  }

  // Splits terminal output into [text, classes] pieces; only colour codes are kept.
  function pieces(data, state) {
    const out = [];
    const re = /\x1b\[([0-9;?]*)([A-Za-z])/g;
    let last = 0, m;
    while ((m = re.exec(data))) {
      if (m.index > last) out.push([data.slice(last, m.index), state.cls.join(" ")]);
      last = re.lastIndex;
      if (m[2] !== "m") continue;
      for (const code of (m[1] || "0").split(";").map(Number)) {
        if (code === 0) state.cls = [];
        else if (code === 1) state.cls.push("t-bold");
        else if (code === 2) state.cls.push("t-dim");
        else if (COLOURS[code]) state.cls.push(COLOURS[code]);
      }
    }
    if (last < data.length) out.push([data.slice(last), state.cls.join(" ")]);
    return out;
  }

  function play(box) {
    const screen = box.querySelector("pre");
    const button = box.querySelector("button");
    const speedSel = box.querySelector("select");
    let timer = null;
    let cast = null;
    const start = () => {
      clearTimeout(timer);
      screen.replaceChildren();
      const state = { cls: [] };
      let i = 0;
      const speed = Number(speedSel.value) || 4;
      const step = () => {
        if (i >= cast.events.length) { button.textContent = "Replay"; return; }
        const [t, , data] = cast.events[i++];
        for (const [text, cls] of pieces(data.replace(/\r\n/g, "\n").replace(/\r/g, ""), state)) {
          const span = document.createElement("span");
          span.textContent = text;
          if (cls) span.className = cls;
          screen.append(span);
        }
        screen.scrollTop = screen.scrollHeight;
        const next = i < cast.events.length ? cast.events[i][0] : t;
        const wait = Math.min(next - t, 1.2) / speed; // long pauses are cut to 1.2 s
        timer = setTimeout(step, wait * 1000);
      };
      button.textContent = "Restart";
      step();
    };
    button.addEventListener("click", () => {
      if (cast) return start();
      button.disabled = true;
      fetch(box.dataset.cast).then((r) => r.text()).then((text) => { cast = parse(text); button.disabled = false; start(); })
        .catch(() => { screen.textContent = "The recording could not be loaded."; button.disabled = false; });
    });
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) speedSel.value = "100";
  }

  document.querySelectorAll("[data-cast]").forEach(play);
})();
