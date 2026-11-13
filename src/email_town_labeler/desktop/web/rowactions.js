// Row controls as in Gmail: a star toggle at the end of every writable row's subject line, and Archive / Trash / Mark read-unread
// on hover or keyboard focus. Keyed rows retain their controls; labels and actions follow current state.
(() => {
  "use strict";

  const host = () => window.EmailTownMailHost;
  const list = document.getElementById("messageList");
  const SVG = "http://www.w3.org/2000/svg";
  const ICONS = {
    archive: "M4 8h16v11H4z M3 4h18v4H3z M10 12h4",
    trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v6 M14 11v6",
    read: "M4 10l8-6 8 6v10H4z M4 10l8 6 8-6",
    unread: "M3 6h18v12H3z M3 7l9 7 9-7",
    star: "M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z",
  };

  function icon(name, filled = false) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", ICONS[name]);
    path.setAttribute("fill", filled ? "currentColor" : "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.8");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-linecap", "round");
    svg.append(path);
    return svg;
  }
  function button(className, role, label, action, iconName, filled = false) {
    const control = document.createElement("button");
    control.type = "button";
    control.className = className;
    control.dataset.role = role;
    control.title = label;
    control.setAttribute("aria-label", label);
    control.dataset.rowAction = action;
    control.append(icon(iconName, filled));
    return control;
  }
  function syncButton(control, label, action, iconName, filled = false) {
    control.title = label;
    control.setAttribute("aria-label", label);
    control.dataset.rowAction = action;
    const path = control.querySelector("path");
    path.setAttribute("d", ICONS[iconName]);
    path.setAttribute("fill", filled ? "currentColor" : "none");
  }

  function sync(row) {
    const id = row.dataset.messageId;
    const message = host()?.state.messages.find((item) => item.id === id);
    if (!message) return;
    const canWrite = Boolean(host().accountById?.(message.account_id)?.capabilities?.write);
    if (!canWrite) {
      row.querySelector(".row-star")?.remove();
      row.querySelector(".row-actions")?.remove();
      return;
    }
    let star = row.querySelector(".row-star");
    if (!star) {
      star = button("row-star", "star", "Star (S)", "star", "star");
      row.querySelector(".row-meta")?.append(star);
    }
    syncButton(
      star,
      message.starred ? "Unstar (S)" : "Star (S)",
      message.starred ? "unstar" : "star",
      "star",
      Boolean(message.starred),
    );
    star.classList.toggle("is-starred", Boolean(message.starred));
    star.setAttribute("aria-pressed", String(Boolean(message.starred)));

    let actions = row.querySelector(".row-actions");
    if (!actions) {
      actions = document.createElement("span");
      actions.className = "row-actions";
      actions.append(
        button("row-action", "archive", "Archive (E)", "archive", "archive"),
        button("row-action", "restore", "Restore to Inbox", "restore", "archive"),
        button("row-action", "trash", "Move to Trash (#)", "trash", "trash"),
        button("row-action", "read-state", "Mark as read (Shift+I)", "read", "read"),
      );
      row.querySelector(".row-title")?.append(actions);
    }
    const trashed = host().state.folder === "trash";
    actions.querySelector('[data-role="archive"]').hidden = trashed;
    actions.querySelector('[data-role="restore"]').hidden = !trashed;
    actions.querySelector('[data-role="trash"]').hidden = trashed;
    syncButton(
      actions.querySelector('[data-role="read-state"]'),
      message.unread ? "Mark as read (Shift+I)" : "Mark as unread (Shift+U)",
      message.unread ? "read" : "unread",
      message.unread ? "read" : "unread",
    );
  }

  function syncAll() {
    list?.querySelectorAll(".message-row[data-message-id]").forEach(sync);
  }
  list?.addEventListener("click", (event) => {
    const control = event.target.closest("[data-row-action]");
    const row = control?.closest(".message-row[data-message-id]");
    if (!control || !row || !list.contains(row)) return;
    event.stopPropagation();
    host().mailAction(control.dataset.rowAction, [row.dataset.messageId], "row-button");
  });
  if (list) new MutationObserver(syncAll).observe(list, { childList: true });
  window.EmailTownRowActions = { sync };
  syncAll();
})();
