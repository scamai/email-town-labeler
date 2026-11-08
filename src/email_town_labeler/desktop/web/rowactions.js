// Row controls as in Gmail: a star toggle on every row, and Archive / Trash / Mark read-unread on
// hover or keyboard focus (pointer devices only; touch keeps the star and the preview's buttons).
// Rows are re-rendered by app.js; a MutationObserver decorates each new row. Every control runs the
// toolbar's own mailAction, so account permissions and Trash's confirmation still apply.
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
  function button(className, label, action, id, iconName, filled) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = className;
    b.title = label;
    b.setAttribute("aria-label", label);
    b.dataset.rowAction = action;
    b.append(icon(iconName, filled));
    b.addEventListener("click", (event) => {
      event.stopPropagation(); // a control, not "open this message"
      host().mailAction(action, [id]);
    });
    return b;
  }

  function decorate(row) {
    if (row.dataset.decorated) return;
    const id = row.dataset.messageId;
    const message = host()?.state.messages.find((m) => m.id === id);
    if (!message) return;
    row.dataset.decorated = "1";
    const canWrite = Boolean(host().accountById?.(message.account_id)?.capabilities?.write);
    if (!canWrite) return; // read-only or paused account: nothing here could act
    const star = button(
      "row-star",
      message.starred ? "Unstar (S)" : "Star (S)",
      message.starred ? "unstar" : "star",
      id,
      "star",
      message.starred,
    );
    star.classList.toggle("is-starred", Boolean(message.starred));
    star.setAttribute("aria-pressed", String(Boolean(message.starred)));
    row.querySelector(".row-check")?.after(star);
    const actions = document.createElement("span");
    actions.className = "row-actions";
    const trashed = host().state.folder === "trash";
    actions.append(
      trashed
        ? button("row-action", "Restore to Inbox", "restore", id, "archive")
        : button("row-action", "Archive (E)", "archive", id, "archive"),
      ...(trashed ? [] : [button("row-action", "Move to Trash (#)", "trash", id, "trash")]),
      message.unread
        ? button("row-action", "Mark as read (Shift+I)", "read", id, "read")
        : button("row-action", "Mark as unread (Shift+U)", "unread", id, "unread"),
    );
    row.querySelector(".row-title")?.append(actions);
  }

  function decorateAll() {
    list?.querySelectorAll(".message-row[data-message-id]").forEach(decorate);
  }
  if (list) new MutationObserver(decorateAll).observe(list, { childList: true });
  decorateAll();
})();
