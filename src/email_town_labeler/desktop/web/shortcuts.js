// Keyboard shortcuts: Gmail's single keys and Apple Mail's ⌘ combinations.
// Every shortcut clicks the same button a pointer would, so disabled controls, account
// permissions and confirmation steps apply unchanged. Single keys never fire while typing,
// while an input method is composing (Chinese, Japanese…), or under an open dialog.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const host = () => window.EmailTownMailHost;
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const MOD = MAC ? "⌘" : "Ctrl";
  let pendingGo = 0; // time of a "g" press: the next key picks a folder

  // [keys shown, description] per group; the help dialog is built from this table.
  const HELP = [
    [
      "Navigate",
      [
        ["j / ↓", "Next (older) message"],
        ["k / ↑", "Previous (newer) message"],
        ["Esc", "Back to the list, close a menu or dialog"],
        [`/ or ${MOD} F`, "Search"],
        ["g then i / s / a / t", "Inbox, Sent, Archive, Trash"],
      ],
    ],
    [
      "Act on the open message, or on the selection",
      [
        ["x", "Select or deselect the open message"],
        ["e", "Archive"],
        ["# or Delete", "Move to Trash"],
        ["s", "Star or unstar"],
        ["Shift U / Shift I", "Mark unread / read"],
        ["d", "Mark done or reopen"],
      ],
    ],
    [
      "Write",
      [
        [`c or ${MOD} N`, "New message"],
        [`r or ${MOD} R`, "Reply"],
        [`a or ${MOD} Shift R`, "Reply all"],
        [`f or ${MOD} Shift F`, "Forward"],
        [`${MOD} Enter`, "Send (in a message you are writing)"],
      ],
    ],
    ["Help", [["?", "Show these shortcuts"]]],
  ];

  // Text entry only: a focused checkbox (a row just selected with the mouse) or button must not
  // switch the shortcuts off.
  const NOT_TEXT = ["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"];
  function typing(target) {
    const field = target?.closest?.(
      "input, textarea, select, [contenteditable=''], [contenteditable='true']",
    );
    if (!field) return false;
    return !(field.tagName === "INPUT" && NOT_TEXT.includes(field.type));
  }
  const visible = (el) =>
    el && !el.disabled && !el.hidden && el.getClientRects().length > 0;
  // Enabled and not in a hidden region. Layout is not consulted, so a button in a closed "More"
  // menu counts whatever an engine does with closed <details> content.
  const usable = (el) => el && !el.disabled && !el.closest("[hidden]");
  function press(selector) {
    const button = [...document.querySelectorAll(selector)].find(usable);
    if (!button) return false;
    button.click();
    return true;
  }
  const selection = () => host()?.state.selected?.size || 0;
  // Gmail's rule: with messages selected an action applies to them, else to the open message.
  function act(action) {
    if (selection()) return press(`[data-mail-action="${action}"]`);
    return press(`[data-single-action="${action}"]`);
  }
  function rows() {
    return [...document.querySelectorAll(".message-row[data-message-id]")];
  }
  function move(step) {
    const list = rows();
    if (!list.length) return false;
    const active = host()?.state.activeId;
    const index = list.findIndex((row) => row.dataset.messageId === active);
    const next =
      list[
        index < 0 ? 0 : Math.min(list.length - 1, Math.max(0, index + step))
      ];
    const open = next.querySelector(".row-open");
    open?.click();
    open?.focus({ preventScroll: true });
    next.scrollIntoView({ block: "nearest" });
    return true;
  }
  function toggleOpenSelected() {
    const active = host()?.state.activeId;
    const box = active
      ? document.querySelector(
          `.message-row[data-message-id="${CSS.escape(active)}"] .row-check`,
        )
      : null;
    if (!usable(box)) return false;
    box.click();
    return true;
  }
  function compose(mode) {
    if (!mode) return press("#composeButton");
    // Reply buttons exist once the open message's details have loaded.
    if (press(`[data-compose-mode="${mode}"]`)) return true;
    if (host()?.state.activeId) host().showToast("The message is still loading");
    return false;
  }
  function focusSearch() {
    const search = $("searchInput");
    if (!visible(search)) {
      $("sidebarToggle")?.click(); // narrow layouts keep search in the menu
    }
    search?.focus();
    search?.select();
    return true;
  }
  function goFolder(key) {
    const folder = { i: "inbox", s: "sent", a: "archive", t: "trash" }[key];
    return folder ? press(`[data-mail-folder="${folder}"]`) : false;
  }

  function showHelp() {
    const dialog = $("shortcutsDialog");
    const body = $("shortcutsList");
    if (!dialog || !body) return false;
    if (!body.childElementCount) {
      for (const [group, items] of HELP) {
        const section = document.createElement("section");
        const title = document.createElement("h3");
        title.textContent = group;
        const list = document.createElement("dl");
        for (const [keys, description] of items) {
          const dt = document.createElement("dt");
          keys.split(" ").forEach((part, i) => {
            if (i) dt.append(" ");
            // "/" separates keys ("j / ↓"); as the first token it is the slash key itself.
            if (["or", "then"].includes(part) || (part === "/" && i > 0)) dt.append(part);
            else {
              const kbd = document.createElement("kbd");
              kbd.textContent = part;
              dt.append(kbd);
            }
          });
          const dd = document.createElement("dd");
          dd.textContent = description;
          list.append(dt, dd);
        }
        section.append(title, list);
        body.append(section);
      }
    }
    if (!dialog.open) dialog.showModal();
    return true;
  }

  function onKey(event) {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229)
      return;
    const mod = MAC ? event.metaKey : event.ctrlKey;
    const key = event.key;
    const lower = key.length === 1 ? key.toLowerCase() : key;
    const dialog = document.querySelector("dialog[open]");

    // ⌘/Ctrl combinations (Apple Mail): available everywhere except other dialogs.
    if (mod && !event.altKey) {
      if (key === "Enter" && dialog?.id === "composeDialog") {
        event.preventDefault();
        $("composeForm")?.requestSubmit($("sendButton"));
        return;
      }
      if (dialog) return;
      let handled = false;
      if (lower === "n" && !event.shiftKey) handled = compose();
      else if (lower === "r")
        handled = compose(event.shiftKey ? "reply-all" : "reply");
      else if (lower === "f" && event.shiftKey) handled = compose("forward");
      else if (lower === "f" && !event.shiftKey) handled = focusSearch();
      if (handled) event.preventDefault();
      return; // ⌘C, ⌘V, ⌘A, ⌘Z… stay with the system
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (dialog || typing(event.target)) return;

    if (pendingGo && Date.now() - pendingGo < 1500) {
      pendingGo = 0;
      if (goFolder(lower)) event.preventDefault();
      return;
    }
    pendingGo = 0;
    let handled = false;
    if (key === "?") handled = showHelp();
    else if (lower === "j" || key === "ArrowDown") handled = move(1);
    else if (lower === "k" || key === "ArrowUp") handled = move(-1);
    else if (key === "/") handled = focusSearch();
    else if (lower === "g" && !event.shiftKey) {
      pendingGo = Date.now();
      handled = true;
    } else if (lower === "x") handled = toggleOpenSelected();
    else if (lower === "e") handled = act("archive");
    else if (key === "#" || key === "Delete" || key === "Backspace")
      handled = act("trash");
    else if (lower === "s" && !event.shiftKey)
      handled = act("star") || act("unstar");
    else if (key === "U") handled = act("unread");
    else if (key === "I") handled = act("read");
    else if (lower === "d" && !event.shiftKey)
      handled = !selection() && press("#previewDoneButton");
    else if (lower === "c" && !event.shiftKey) handled = compose();
    else if (lower === "r" && !event.shiftKey) handled = compose("reply");
    else if (lower === "a" && !event.shiftKey) handled = compose("reply-all");
    else if (lower === "f" && !event.shiftKey) handled = compose("forward");
    if (handled) event.preventDefault();
  }

  document.addEventListener("keydown", onKey);
  $("shortcutsButton")?.addEventListener("click", showHelp);
  window.EmailTownShortcuts = { HELP, showHelp };
})();
