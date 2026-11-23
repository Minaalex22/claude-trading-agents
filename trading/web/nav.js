// One menu on every page: highlight the page you're on, and the dropdown it sits in.
"use strict";
(() => {
  const here = location.pathname.replace(/\/+$/, "") || "/";
  const alias = { "/markets": /^\/markets(\/|$)/, "/desks": /^\/desks(\/|$)/ };
  document.querySelectorAll(".ats-nav a").forEach(a => {
    const href = a.getAttribute("href");
    if (href === here || (alias[href] && alias[href].test(here))) {
      a.classList.add("on");
      const box = a.closest("details");
      if (box) box.classList.add("open-on");
    }
  });
  // Close an open dropdown when another opens or you tap elsewhere.
  const boxes = [...document.querySelectorAll(".ats-more")];
  boxes.forEach(d => d.addEventListener("toggle", () => { if (d.open) boxes.forEach(o => { if (o !== d) o.open = false; }); }));
  document.addEventListener("click", e => { if (!e.target.closest(".ats-more")) boxes.forEach(d => { d.open = false; }); });
})();
