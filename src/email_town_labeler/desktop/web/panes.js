// Draggable pane borders, as in Outlook: the folder pane and the message list can each be widened or
// narrowed by dragging the border on their right (or focusing it and pressing ←/→); double-click restores
// the default. The workbench keeps the widths (prefs in workflow.sqlite): the app serves the page on a new
// port every launch, so the page's localStorage starts empty each time and only caches them for a reload.
(() => {
  "use strict";
  const shell = document.getElementById("appShell");
  const workspace = document.getElementById("workspace");
  if (!shell || !workspace) return;
  const KEY = "email-town.panes";
  const DEFAULTS = { sidebar: 232, list: 420 };
  const MIN = { sidebar: 180, list: 320 };
  const READING_MIN = 360; // the reading pane keeps at least this much
  const STEP = 16;
  let widths = { ...DEFAULTS };
  let adopted = false; // the workbench's widths are taken once per page, unless a border was moved first
  let touched = false;
  let saveTimer = null;

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
      for (const name of Object.keys(DEFAULTS))
        if (Number.isFinite(saved[name])) widths[name] = saved[name];
    } catch (_) {
      widths = { ...DEFAULTS };
    }
  }
  function save() {
    touched = true;
    try {
      localStorage.setItem(KEY, JSON.stringify(widths));
    } catch (_) {
      // private window or blocked storage: the workbench copy below still keeps them
    }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(send, 300); // arrow keys repeat: one save once they stop
  }
  // keepalive: a save started as the window closes (or right after a drag) still reaches the workbench.
  function send() {
    clearTimeout(saveTimer);
    saveTimer = null;
    window.EmailTownMailHost?.api("/api/panes", {
      method: "POST",
      keepalive: true,
      body: JSON.stringify({ widths }),
    }).catch(() => {}); // a lost save only means the default width after the next launch
  }
  function adopt(saved) {
    if (adopted || touched || !saved) return;
    adopted = true;
    let changed = false;
    for (const name of Object.keys(DEFAULTS))
      if (Number.isFinite(saved[name]) && saved[name] !== widths[name]) {
        widths[name] = saved[name];
        changed = true;
      }
    if (changed) apply();
  }
  function max(name) {
    const total = workspace.clientWidth;
    if (name === "sidebar") return Math.min(360, total - widths.list - READING_MIN);
    return total - widths.sidebar - READING_MIN;
  }
  const clamp = (name, value) => Math.round(Math.max(MIN[name], Math.min(max(name), value)));
  function apply() {
    // Clamp against the current window, but keep the saved preference for a wider one.
    const sidebar = window.innerWidth < 1200 ? Math.min(widths.sidebar, 280) : clamp("sidebar", widths.sidebar);
    shell.style.setProperty("--sidebar-w", `${Math.max(MIN.sidebar, sidebar)}px`);
    shell.style.setProperty("--list-w", `${Math.max(MIN.list, clamp("list", widths.list))}px`);
    document.querySelectorAll(".splitter").forEach((handle) => {
      const name = handle.dataset.split;
      handle.setAttribute("aria-valuenow", String(widths[name]));
      handle.setAttribute("aria-valuemin", String(MIN[name]));
      handle.setAttribute("aria-valuemax", String(Math.max(MIN[name], max(name))));
    });
  }
  function set(name, value) {
    widths[name] = clamp(name, value);
    apply();
    save();
  }

  document.querySelectorAll(".splitter").forEach((handle) => {
    const name = handle.dataset.split;
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const startX = event.clientX;
      const start = widths[name];
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("is-dragging");
      document.body.classList.add("is-resizing");
      const move = (e) => {
        widths[name] = clamp(name, start + e.clientX - startX);
        apply();
      };
      const end = () => {
        handle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing");
        handle.removeEventListener("pointermove", move);
        save();
        send(); // a drag ends once: save now, not after the debounce
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", end, { once: true });
      handle.addEventListener("pointercancel", end, { once: true });
    });
    handle.addEventListener("dblclick", () => set(name, DEFAULTS[name]));
    handle.addEventListener("keydown", (event) => {
      const delta = { ArrowLeft: -STEP, ArrowRight: STEP }[event.key];
      if (!delta) return;
      event.preventDefault();
      event.stopPropagation(); // not a list shortcut
      set(name, widths[name] + delta);
    });
  });
  window.addEventListener("resize", apply);
  window.addEventListener("pagehide", () => {
    if (saveTimer) send();
  });
  window.EmailTownPanes = { adopt };
  load();
  apply();
})();
