// Day / night look, applied before the page paints so it never flashes the other one.
// "light" / "dark" = chosen with the header's switch (remembered in this browser); nothing = follow the system.
"use strict";
(() => {
  let theme = "";
  try {
    theme = localStorage.getItem("edmultitool.theme") || "";
  } catch {
    // storage blocked: follow the system
  }
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
})();
