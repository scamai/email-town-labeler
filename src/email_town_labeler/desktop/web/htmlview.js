// The formatted view of an HTML message, as Gmail shows it. The server sanitizes the sender's HTML and
// serves it as its own document (no scripts, no forms, no network except images; see htmlview.py); this
// file embeds it in a sandboxed iframe sized to its content. External images stay hidden until asked for,
// and links (target=_blank, allow-popups) open in the system browser.
//
// The sandbox omits allow-scripts, and WebKit then also refuses listeners this page attaches inside the
// frame. So nothing here listens in the frame: the frame never keeps keyboard focus (the shortcuts stay
// with the app), copying reads the frame's selection from this page, and the height is measured when the
// frame has loaded (after its images) and when the window resizes.
(() => {
  "use strict";
  const host = window.EmailTownMailHost;
  if (!host) return;
  const plain = new Set(); // messages switched to plain text in this session
  let generation = 0;
  let active = null; // { frame, current } of the formatted view on screen

  // Whether the reading pane shows this detail formatted: decided from the detail itself, so the images
  // panel (images.js) and the body agree whichever renders first.
  const formatted = (detail) => Boolean(detail?.has_html) && !plain.has(detail.id);

  function clear() {
    generation += 1;
    active = null;
  }

  async function viewUrl(id, remote) {
    const data = await host.api(
      `/api/message-view?id=${encodeURIComponent(id)}&remote=${remote ? 1 : 0}`,
    );
    if (typeof data.url !== "string" || !data.url.startsWith("/view/")) throw new Error("No view");
    return data.url;
  }

  // Same origin (allow-same-origin, never allow-scripts), so this page can read the frame's layout.
  function fit() {
    if (!active?.current()) return;
    const root = active.frame.contentDocument?.documentElement;
    if (root) active.frame.style.height = `${Math.ceil(root.scrollHeight)}px`;
  }
  const frameSelection = () => {
    try {
      return active?.frame.contentWindow?.getSelection()?.toString() || "";
    } catch (_) {
      return "";
    }
  };

  window.addEventListener("resize", () => requestAnimationFrame(fit));
  // Clicking into the message (to select text, or on a link) moves focus into the frame, where keys
  // would never reach the shortcuts. Hand focus back; the click itself and the selection are unaffected.
  window.addEventListener("blur", () =>
    setTimeout(() => {
      if (active && document.activeElement === active.frame)
        document.getElementById("previewBody")?.focus({ preventScroll: true });
    }, 0),
  );
  document.addEventListener("copy", (event) => {
    const own = document.getSelection();
    if (own && !own.isCollapsed) return;
    const text = frameSelection();
    if (!text) return;
    event.clipboardData.setData("text/plain", text);
    event.preventDefault();
  });

  function load(frame, url, current) {
    frame.onload = () => {
      if (!current()) return;
      active = { frame, current };
      fit();
    };
    frame.src = url;
  }

  function toolbar(detail, { label, onToggle, onImages }) {
    const bar = document.createElement("div");
    bar.className = "format-bar";
    const count = Number(detail.external_images) || 0;
    if (onImages && count) {
      const note = document.createElement("span");
      note.className = "format-note";
      note.textContent = `${count} external image${count === 1 ? "" : "s"} hidden`;
      note.title = "Loading them can tell the sender you opened this message";
      const load = document.createElement("button");
      load.type = "button";
      load.className = "text-button";
      load.textContent = "Show images";
      load.onclick = () => onImages(load, note);
      bar.append(note, load);
    }
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "text-button format-toggle";
    toggle.textContent = label;
    toggle.onclick = onToggle;
    bar.append(toggle);
    return bar;
  }

  // Draw the body of `detail` into `target`. `renderPlain(target)` draws the plain-text body.
  function render(target, detail, renderPlain) {
    clear();
    const version = generation;
    const current = () => version === generation && host.state.activeId === detail.id;
    target.classList.remove("is-formatted");
    if (!detail.has_html) {
      renderPlain(target);
      return;
    }
    if (!formatted(detail)) {
      renderPlain(target);
      target.prepend(
        toolbar(detail, {
          label: "Show formatted",
          onToggle: () => {
            plain.delete(detail.id);
            render(target, detail, renderPlain);
          },
        }),
      );
      window.EmailTownImages?.render(detail);
      return;
    }
    window.EmailTownImages?.clear();
    target.replaceChildren();
    target.classList.add("is-formatted");
    const frame = document.createElement("iframe");
    frame.className = "email-frame";
    frame.title = "Message content";
    frame.setAttribute("sandbox", "allow-same-origin allow-popups allow-popups-to-escape-sandbox");
    frame.referrerPolicy = "no-referrer";
    target.append(
      toolbar(detail, {
        label: "Plain text",
        onToggle: () => {
          plain.add(detail.id);
          render(target, detail, renderPlain);
        },
        onImages: async (button, note) => {
          button.disabled = true;
          try {
            const url = await viewUrl(detail.id, true);
            if (!current()) return;
            load(frame, url, current);
            note.textContent = "External images shown for this message";
            button.remove();
          } catch (error) {
            button.disabled = false;
            host.showToast?.(`Images could not be loaded: ${error.message}`);
          }
        },
      }),
      frame,
    );
    viewUrl(detail.id, false)
      .then((url) => {
        if (current()) load(frame, url, current);
      })
      .catch(() => {
        // The formatted version is unavailable: fall back to the plain text the detail already has.
        if (!current()) return;
        plain.add(detail.id);
        render(target, detail, renderPlain);
      });
  }

  window.EmailTownHtmlView = { render, clear, formatted };
  window.addEventListener("pagehide", clear);
})();
