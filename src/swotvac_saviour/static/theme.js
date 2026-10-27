// theme toggle: cycles light -> dark, persists in localStorage.
// The pre-paint script in base.html sets <html data-theme> before first paint;
// this only handles user clicks.
(function () {
  "use strict";
  var btn = document.getElementById("theme-toggle");
  if (!btn) return;
  btn.addEventListener("click", function () {
    var root = document.documentElement;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("theme", next);
    } catch (e) { /* private mode: theme won't persist */ }
  });
})();
