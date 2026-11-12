// Selection as in desktop mail clients and Gmail:
//   Shift-click (row or checkbox)  selects the range from the last clicked message
//   ⌘/Ctrl-click a row             adds or removes that one message, without opening it
//   the ▾ menu by "Select all"     All / None / Read / Unread / Starred / Unstarred (replaces the selection)
// Keyboard extensions (Shift+j/k, * then a/n/r/u/s/t, Esc) are in shortcuts.js and call the API below.
// Ranges follow the list as shown (filters and search applied) and stop at the 500-message action limit.
(() => {
  "use strict";

  const LIMIT = 500;
  const host = () => window.EmailTownMailHost;
  const list = $("messageList");
  let anchor = null; // the message a Shift range starts from: the last one clicked or toggled

  function $(id) {
    return document.getElementById(id);
  }
  const shown = () => host()?.filteredMessages?.() || [];
  const selected = () => host().state.selected;

  function commit() {
    if (selected().size > LIMIT) {
      const keep = shown()
        .map((m) => m.id)
        .filter((id) => selected().has(id))
        .slice(0, LIMIT);
      selected().clear();
      keep.forEach((id) => selected().add(id));
      host().showToast(`Up to ${LIMIT} messages per action; selected the first ${LIMIT}`);
    }
    host().renderList();
  }
  function range(fromId, toId, on) {
    const ids = shown().map((m) => m.id);
    let a = ids.indexOf(fromId);
    const b = ids.indexOf(toId);
    if (b < 0) return false;
    if (a < 0) a = b;
    const [lo, hi] = a < b ? [a, b] : [b, a];
    ids.slice(lo, hi + 1).forEach((id) => (on ? selected().add(id) : selected().delete(id)));
    anchor = toId;
    commit();
    return true;
  }
  function toggle(id, on = !selected().has(id)) {
    if (on) selected().add(id);
    else selected().delete(id);
    anchor = id;
    commit();
  }
  const WHERE = {
    all: () => true,
    none: () => false,
    read: (m) => !m.unread,
    unread: (m) => m.unread,
    starred: (m) => m.starred,
    unstarred: (m) => !m.starred,
  };
  function selectWhere(kind) {
    const test = WHERE[kind];
    if (!test) return false;
    selected().clear();
    shown()
      .filter(test)
      .forEach((m) => selected().add(m.id));
    anchor = null;
    commit();
    return true;
  }
  // Shift+j / Shift+k: select the open message and the next one in that direction, then open it.
  function extend(step) {
    const ids = shown().map((m) => m.id);
    if (!ids.length) return false;
    const active = host().state.activeId;
    const index = ids.indexOf(active);
    const next = ids[index < 0 ? 0 : Math.min(ids.length - 1, Math.max(0, index + step))];
    if (active && index >= 0) selected().add(active);
    selected().add(next);
    anchor = next;
    commit();
    host().openMessage(next, { via: step < 0 ? "shift+k" : "shift+j" });
    return true;
  }
  function clear() {
    if (!selected().size) return false;
    selected().clear();
    anchor = null;
    commit();
    return true;
  }

  // Capture phase: runs before the row's own handlers, so a modified click selects instead of opening.
  list?.addEventListener(
    "mousedown",
    (event) => {
      if (event.shiftKey && event.target.closest(".message-row")) event.preventDefault(); // no text selection
    },
    true,
  );
  list?.addEventListener(
    "click",
    (event) => {
      const row = event.target.closest(".message-row[data-message-id]");
      if (!row) return;
      const id = row.dataset.messageId;
      const box = event.target.closest(".row-check");
      if (event.shiftKey && anchor && anchor !== id) {
        event.preventDefault(); // the checkbox state comes from the range, not the browser's toggle
        event.stopPropagation();
        range(anchor, id, box ? !selected().has(id) : true);
        return;
      }
      if (!box && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        event.stopPropagation();
        toggle(id);
        return;
      }
      anchor = id; // a plain click on a checkbox or a row starts the next range
    },
    true,
  );
  document.querySelectorAll("[data-select]").forEach((button) =>
    button.addEventListener("click", () => selectWhere(button.dataset.select)),
  );

  window.EmailTownSelection = { range, toggle, selectWhere, extend, clear };
})();
