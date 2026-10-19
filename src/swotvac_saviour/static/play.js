// swotvac-saviour play page: KaTeX render, keyboard shortcuts, source popover.
(function () {
  "use strict";

  function renderMath() {
    document.querySelectorAll(".katex-inline").forEach(function (el) {
      try {
        el.innerHTML = katex.renderToString(el.textContent, {
          displayMode: false,
          throwOnError: true,
        });
      } catch (e) {
        el.classList.add("katex-fallback");
      }
    });
    // $$...$$ blocks render in full display mode (centered, large operators).
    document.querySelectorAll(".katex-display-block").forEach(function (el) {
      try {
        el.innerHTML = katex.renderToString(el.textContent, {
          displayMode: true,
          throwOnError: true,
        });
      } catch (e) {
        el.classList.add("katex-fallback");
      }
    });
  }

  function currentFocused() {
    var a = document.activeElement;
    return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT");
  }

  function press(key) {
    if (currentFocused()) return;
    var btn = document.querySelector('.actions button[data-shortcut="' + key + '"]');
    if (btn) btn.click();
  }

  document.addEventListener("keydown", function (e) {
    switch (e.key) {
      case "1":
        press("1"); break;
      case "2":
        press("2"); break;
      case "q":
      case "Q":
        press("q"); break;
      case " ":
        press("1"); break;
      default:
        return;
    }
    e.preventDefault(); // stop the page scrolling, even before any keyup
  });
  document.addEventListener("keyup", function (e) {
    if (e.key === " ") { e.preventDefault(); }
  });

  // reveal source page popover
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".source-badge button[data-source-url]");
    if (!btn) return;
    var img = btn.parentElement.querySelector("img.source-popover");
    if (!img) return;
    var expanded = btn.getAttribute("aria-expanded") === "true";
    if (expanded) {
      img.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      return;
    }
    if (!img.getAttribute("src")) {
      img.setAttribute("src", btn.getAttribute("data-source-url"));
    }
    img.hidden = false;
    btn.setAttribute("aria-expanded", "true");
  });

  renderMath();
  // after any htmx swap (next question), re-render and refocus
  document.addEventListener("htmx:afterSwap", function () {
    renderMath();
    var btn = document.querySelector('.actions button[data-shortcut="1"]');
    if (btn) btn.focus();
    var meta = document.querySelector(".play-meta");
    if (meta) meta.setAttribute("aria-live", "polite");
  });
})();