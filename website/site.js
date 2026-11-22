// QEDCode site helpers: copy buttons and the terminal replay. No dependencies, no tracking.
(function () {
  "use strict";
  const QED = (window.QED = {});

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const text = button.getAttribute("data-copy");
    const done = () => {
      const old = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = old), 1400);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, () => {});
    } else {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy"); done(); } catch (_) { /* ignore */ }
      area.remove();
    }
  });

  function lineNode(text, cls) {
    const span = document.createElement("span");
    if (text.startsWith("GRADE_A ")) {
      const seal = document.createElement("span");
      seal.className = "t-a";
      seal.textContent = "A";
      span.append(seal, " " + text.slice(8));
    } else {
      span.textContent = text;
      if (cls) span.className = cls;
    }
    return span;
  }

  // Types the recorded lines once; with reduced motion everything appears at once.
  QED.replay = function (target, lines) {
    if (!target) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) {
      lines.forEach(([text, cls]) => target.append(lineNode(text, cls), "\n"));
      return;
    }
    let index = 0;
    const next = () => {
      if (index >= lines.length) return;
      const [text, cls] = lines[index++];
      target.append(lineNode(text, cls), "\n");
      const pause = text.startsWith("$") ? 700 : text.startsWith("Try") ? 520 : 240;
      setTimeout(next, pause);
    };
    setTimeout(next, 500);
  };
})();
