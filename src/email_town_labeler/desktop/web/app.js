(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    sidebar: $("sidebar"),
    sidebarToggle: $("sidebarToggle"),
    sidebarClose: $("sidebarClose"),
    refresh: $("refreshButton"),
    addAccount: $("addAccountButton"),
    accountDialog: $("accountDialog"),
    accountForm: $("accountForm"),
    provider: $("providerSelect"),
    existingFields: $("existingFields"),
    imapFields: $("imapFields"),
    microsoftHelp: $("microsoftHelp"),
    clientIdFields: $("clientIdFields"),
    gmailFields: $("gmailFields"),
    googleStatus: $("googleStatus"),
    googleClientImport: $("googleClientImport"),
    googleClientFile: $("googleClientFile"),
    imapEmail: $("imapEmail"),
    imapUsername: $("imapUsername"),
    imapPresetHelp: $("imapPresetHelp"),
    imapPresetText: $("imapPresetText"),
    imapPresetLink: $("imapPresetLink"),
    imapServerDetails: $("imapServerDetails"),
    useMicrosoft: $("useMicrosoftButton"),
    accountError: $("accountFormError"),
    submitAccount: $("submitAccountButton"),
    accountFilters: $("accountFilters"),
    categoryFilters: $("categoryFilters"),
    search: $("searchInput"),
    clearFilters: $("clearFiltersButton"),
    listTitle: $("listTitle"),
    listStatus: $("listStatus"),
    listError: $("listError"),
    listErrorText: $("listErrorText"),
    retryMail: $("retryMailButton"),
    demoBanner: $("demoBanner"),
    jobStatus: $("jobStatus"),
    loadMoreBar: $("loadMoreBar"),
    loadedSummary: $("loadedSummary"),
    loadMore: $("loadMoreButton"),
    messageList: $("messageList"),
    selectionToolbar: $("selectionToolbar"),
    selectAll: $("selectAll"),
    selectionCount: $("selectionCount"),
    clearSelection: $("clearSelectionButton"),
    previewPane: $("previewPane"),
    previewEmpty: $("previewEmpty"),
    previewContent: $("previewContent"),
    previewClose: $("previewClose"),
    previewCategoryButton: $("previewCategoryButton"),
    originalLink: $("originalLink"),
    previewSource: $("previewSource"),
    previewCategory: $("previewCategory"),
    previewUnread: $("previewUnread"),
    previewSubject: $("previewSubject"),
    previewByline: $("previewByline"),
    previewDate: $("previewDate"),
    previewBody: $("previewBody"),
    previewError: $("previewError"),
    previewErrorText: $("previewErrorText"),
    retryPreview: $("retryPreviewButton"),
    categoryDialog: $("categoryDialog"),
    categoryForm: $("categoryForm"),
    categorySelect: $("categorySelect"),
    toast: $("toast"),
    syncIndicator: $("syncIndicator"),
    syncText: $("syncText"),
    footerStatus: $("footerStatus"),
    restart: $("restartButton"),
  };
  const state = {
    accounts: [],
    messages: [],
    categories: [],
    jobs: [],
    outbox: [],
    refreshing: false,
    stale: false,
    listError: "",
    listRetryFocus: false,
    previewError: "",
    previewErrorId: null,
    previewRequest: 0,
    previewLoading: false,
    previewRetryFocus: false,
    modelError: "",
    modelStatus: null,
    lastRefresh: null,
    demo: false,
    filters: {
      type: "all",
      account: "all",
      category: "all",
      search: "",
    },
    selected: new Set(),
    activeId: null,
    syncing: false,
    initialized: false,
    authLost: false,
    sessionToken: "",
    watching: false,
    etag: "",
    seq: 0,
    toastTimer: null,
    snapshot: "",
    setup: null,
  };
  const text = (value) => String(value ?? "");
  const formatDate = (epoch) => {
    const date = new Date(Number(epoch) || 0);
    return Number.isNaN(date.getTime())
      ? "—"
      : new Intl.DateTimeFormat("en-US", {
          month: "numeric",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }).format(date);
  };
  const localMidnight = (offset = 0) => {
    const date = new Date();
    date.setHours(0, 0, 0, 0);
    date.setDate(date.getDate() + offset);
    return date.getTime();
  };
  const accountById = (id) =>
    state.accounts.find((account) => account.id === id);
  const categoryById = (id) =>
    state.categories.find((category) => category.id === id);

  const sourceName = (source) =>
    ({
      mailbox: "Provider label",
      model: "Local automatic",
      manual: "Local manual",
      pending: "Awaiting classification",
      none: "Not classified (sent mail)",
    })[source] || "Local category";
  function usableFocus(el = document.activeElement) {
    return Boolean(
      el &&
        el !== document.body &&
        el !== document.documentElement &&
        el.isConnected &&
        !el.closest("[hidden], [inert]") &&
        el.getClientRects().length,
    );
  }
  function saveFocus() {
    const el = document.activeElement;
    const row = el?.closest?.(".message-row[data-message-id]");
    const rows = row
      ? [...els.messageList.querySelectorAll(".message-row[data-message-id]")]
      : [];
    const position = row
      ? { rowId: row.dataset.messageId, rowIndex: rows.indexOf(row), element: el }
      : { element: el };
    for (const key of ["focusKey", "accountFilter", "categoryFilter"]) {
      if (el?.dataset[key]) return { key, value: el.dataset[key], ...position };
    }
    if (row) return { key: "rowControl", value: el.dataset.rowAction || "", ...position };
    return null;
  }
  function restoreFocus(saved) {
    if (!saved) return;
    if (saved.element && usableFocus(saved.element)) {
      if (document.activeElement !== saved.element) {
        saved.element.focus({ preventScroll: true });
        const row = saved.element.closest(".message-row");
        if (row) {
          const rowBox = row.getBoundingClientRect();
          const listBox = els.messageList.getBoundingClientRect();
          if (rowBox.top < listBox.top) els.messageList.scrollTop += rowBox.top - listBox.top;
          else if (rowBox.bottom > listBox.bottom)
            els.messageList.scrollTop += rowBox.bottom - listBox.bottom;
        }
      }
      return;
    }
    if (saved.key === "rowControl") {
      const row = els.messageList.querySelector(
        `.message-row[data-message-id="${CSS.escape(saved.rowId)}"]`,
      );
      const control = row?.querySelector(`[data-row-action="${CSS.escape(saved.value)}"]`);
      if (control) {
        control.focus({ preventScroll: true });
        return;
      }
    } else {
      const el = [
        ...document.querySelectorAll(
          "[data-focus-key], [data-account-filter], [data-category-filter]",
        ),
      ].find((el) => el.dataset[saved.key] === saved.value);
      if (el) {
        el.focus({ preventScroll: true });
        return;
      }
    }
    if (!saved.rowId) return;
    const rows = [...els.messageList.querySelectorAll(".message-row[data-message-id]")];
    const row = rows[Math.min(Math.max(0, saved.rowIndex), rows.length - 1)];
    const target = row?.querySelector(
      saved.value === `select-${saved.rowId}` ? ".row-check" : ".row-open",
    );
    (target || els.messageList).focus({ preventScroll: true });
  }
  function ensureListFocus(index = 0) {
    if (usableFocus()) return;
    const rows = [...els.messageList.querySelectorAll(".message-row[data-message-id]")];
    const row = rows[Math.min(Math.max(0, index), rows.length - 1)];
    (row?.querySelector(".row-open") || els.messageList).focus({ preventScroll: true });
  }

  function setSync(kind, label) {
    els.syncIndicator.classList.toggle("is-error", kind === "error");
    els.syncIndicator.classList.toggle("is-loading", kind === "loading");
    els.syncText.textContent = label;
  }
  function renderSyncStatus() {
    const accountError = state.accounts.some((account) => account.status === "error");
    setSync(
      accountError ? "error" : "ok",
      accountError ? "Some accounts have errors" : state.refreshing ? "Syncing" : "Synced",
    );
  }
  function renderListError() {
    els.listError.hidden = !state.listError || state.authLost;
    els.listErrorText.textContent = state.listError;
    els.retryMail.disabled = state.syncing;
  }
  function clearListError() {
    if (!state.listError && !state.listRetryFocus) return;
    const retryFocused = document.activeElement === els.retryMail;
    const restoreFocus =
      (state.listRetryFocus || retryFocused) && (!usableFocus() || retryFocused);
    state.listError = "";
    state.listRetryFocus = false;
    renderListError();
    if (restoreFocus) els.messageList.focus({ preventScroll: true });
  }
  function renderPreviewError() {
    const visible = Boolean(
      state.previewError && state.previewErrorId === state.activeId && !state.authLost,
    );
    els.previewError.hidden = !visible;
    els.previewErrorText.textContent = visible ? state.previewError : "";
    els.retryPreview.disabled = state.previewLoading;
  }
  function clearPreviewError(request) {
    if (request !== state.previewRequest) return;
    const retryFocused = document.activeElement === els.retryPreview;
    const restoreFocus = state.previewRetryFocus || retryFocused;
    state.previewError = "";
    state.previewErrorId = null;
    state.previewLoading = false;
    state.previewRetryFocus = false;
    renderPreviewError();
    if (restoreFocus && (!usableFocus() || retryFocused))
      els.previewBody.focus({ preventScroll: true });
  }
  function showToast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, 4200);
  }
  // A short-lived toast with an Undo button. `undo` resolves to the text shown afterwards.
  function showActionToast(message, undo, ms = 5000) {
    els.toast.replaceChildren();
    const label = document.createElement("span");
    label.textContent = `${message} · `;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-button";
    button.textContent = "Undo";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        els.toast.textContent = await undo();
      } catch (error) {
        els.toast.textContent = error.message;
      }
      clearTimeout(state.toastTimer);
      state.toastTimer = setTimeout(() => {
        els.toast.hidden = true;
      }, 3000);
    });
    els.toast.append(label, button);
    els.toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, ms);
  }
  function showUndoToast(token) {
    els.toast.replaceChildren();
    const label = document.createElement("span");
    label.textContent = "Saved · ";
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "text-button";
    undo.textContent = "Undo";
    undo.addEventListener("click", async () => {
      const reopenedId = state.activeId;
      try {
        await api("/api/undo", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        els.toast.textContent = "Change undone";
        await loadState();
        if (reopenedId && filteredMessages().some((message) => message.id === reopenedId))
          els.messageList
            .querySelector(`.message-row[data-message-id="${CSS.escape(reopenedId)}"] .row-open`)
            ?.focus({ preventScroll: true });
      } catch (error) {
        els.toast.textContent = error.message;
      }
    });
    els.toast.append(label, undo);
    els.toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, 6000);
  }

  async function api(path, options = {}) {
    const isSession = path === "/api/session";
    const headers = {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      "X-Email-Town": "local",
      ...(options.headers || {}),
    };
    if (!isSession && state.sessionToken)
      headers.Authorization = `Bearer ${state.sessionToken}`;
    const response = await fetch(path, {
      credentials: "omit",
      ...options,
      headers,
    });
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error("unauthorized");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }
  // What the user did, for desktop.log: event names, 8-character message-key prefixes, counts and
  // command words only (the server drops anything else). Sent in batches; never subjects or addresses.
  const flagHolds = new Map(); // message id -> { unread, seq }: see applyState
  function holdUnread(id, unread, seq = null) {
    if (unread === null) flagHolds.delete(id);
    else flagHolds.set(id, { unread, seq });
    const message = state.messages.find((m) => m.id === id);
    if (message && unread !== null) message.unread = unread;
  }
  const uiQueue = [];
  let uiTimer = null;
  const k8 = (id) => String(id || "").slice(0, 8) || "-";
  function uiLog(type, data = {}) {
    uiQueue.push({ type, ms: Math.round(performance.now()), ...data });
    if (uiQueue.length > 200) uiQueue.splice(0, uiQueue.length - 200);
    if (!uiTimer) uiTimer = setTimeout(flushUiLog, 1000);
  }
  function flushUiLog() {
    clearTimeout(uiTimer);
    uiTimer = null;
    if (!uiQueue.length || !state.sessionToken) return;
    const events = uiQueue.splice(0, 50);
    api("/api/ui-log", { method: "POST", body: JSON.stringify({ events }), keepalive: true }).catch(
      () => {},
    );
    if (uiQueue.length) uiTimer = setTimeout(flushUiLog, 200);
  }
  window.addEventListener("pagehide", flushUiLog);
  function handleUnauthorized() {
    window.EmailTownImages?.clear();
    window.EmailTownHtmlView?.clear();
    state.authLost = true;
    state.sessionToken = "";
    sessionStorage.removeItem("email-town-session");
    setSync("error", "Restart required");
    els.footerStatus.textContent =
      "Session expired. Reopen Email Town from the launcher.";
    els.restart.hidden = false;
    state.refreshing = false;
    state.listError = "";
    state.listRetryFocus = false;
    state.previewError = "";
    state.previewErrorId = null;
    state.previewRequest += 1;
    state.previewLoading = false;
    state.previewRetryFocus = false;
    state.activeId = null;
    els.previewPane.classList.remove("is-open");
    els.previewContent.hidden = true;
    els.previewEmpty.hidden = false;
    renderList();
    renderListError();
    renderPreviewError();
  }

  function filteredMessages(omit = "") {
    const query = state.filters.search.trim().toLowerCase();
    const today = localMidnight();
    const tomorrow = localMidnight(1);
    return state.messages.filter((message) => {
      const date = Number(message.date) || 0;
      const typeOk =
        omit === "type" ||
        state.filters.type === "all" ||
        (state.filters.type === "today"
          ? date >= today && date < tomorrow
          : date < today);
      const accountOk =
        omit === "account" ||
        state.filters.account === "all" ||
        message.account_id === state.filters.account;
      const categoryOk =
        omit === "category" ||
        state.filters.category === "all" ||
        message.category === state.filters.category;
      const queryOk =
        !query ||
        `${message.subject || ""} ${message.sender || ""}`.toLowerCase().includes(query);
      return typeOk && accountOk && categoryOk && queryOk;
    });
  }
  function scopeStats(messages) {
    return {
      total: messages.length,
      unread: messages.filter((message) => message.unread).length,
    };
  }
  function syncFilterButtons() {
    document.querySelectorAll("[data-type-filter]").forEach((button) => {
      const active = button.dataset.typeFilter === state.filters.type;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  // The sidebar shows one number per entry: its unread mail in the current scope, as in Gmail and Outlook.
  // Totals, classification sources and completion live in each entry's tooltip and accessible name.
  function sidebarEntry(className, label, unread, summary) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    const name = document.createElement("span");
    name.className = "nav-label";
    name.textContent = text(label);
    const count = document.createElement("span");
    count.className = "nav-count";
    count.textContent = unread ? String(unread) : "";
    button.append(name, count);
    button.setAttribute("aria-label", summary);
    button.title = summary;
    return button;
  }
  function renderFilters() {
    const focus = saveFocus();
    els.accountFilters.replaceChildren();
    const accountScope = filteredMessages("account");
    const several = state.accounts.length > 1;
    // With one account there is nothing to choose between: "All accounts" would repeat that account.
    const entries = several ? [{ id: "all", name: "All accounts" }, ...state.accounts] : state.accounts;
    entries.forEach((account) => {
      const messages =
        account.id === "all"
          ? accountScope
          : accountScope.filter((message) => message.account_id === account.id);
      const { total, unread } = scopeStats(messages);
      let status = "";
      if (account.id !== "all")
        status = account.enabled === false ? "Paused" : account.status === "error" ? "Error" : "";
      const summary = `${text(account.name)}: ${unread} unread of ${total} loaded in current scope${status ? `, ${status}` : ""}`;
      const active = several && state.filters.account === account.id;
      const button = sidebarEntry(`account-filter${active ? " is-active" : ""}`, account.name, unread, summary);
      if (several) {
        button.dataset.accountFilter = account.id;
        button.setAttribute("aria-pressed", String(active));
      } else button.classList.add("is-static");
      if (status) {
        const badge = document.createElement("span");
        badge.className = "account-status";
        badge.textContent = status;
        button.querySelector(".nav-label").after(badge);
      }
      if (account.id === "all") {
        els.accountFilters.append(button);
        return;
      }
      const entry = document.createElement("div");
      entry.className = "account-entry";
      const manage = document.createElement("button");
      manage.type = "button";
      manage.className = "icon-button account-manage";
      manage.textContent = "⋯";
      manage.dataset.focusKey = `manage-${account.id}`;
      manage.setAttribute("aria-label", `Manage ${account.name}`);
      manage.title = `Manage ${account.name}`;
      manage.addEventListener("click", () => showAccountManagement(account.id));
      entry.append(button, manage);
      els.accountFilters.append(entry);
    });

    els.categoryFilters.replaceChildren();
    const stats = categoryStats();
    state.categories.forEach((category) => {
      const entry = stats.get(category.id) || {};
      const total = entry.total || 0;
      const unread = entry.unread || 0;
      const automatic = entry.automatic || 0;
      const corrected = (entry.corrected || 0) + (entry.correctedAway || 0);
      const awaiting = entry.awaiting || 0;
      const active = state.filters.category === category.id;
      const summary = `${text(category.name)}: ${unread} unread of ${total} loaded in current scope; ${automatic} sorted automatically, ${corrected} corrected by you${awaiting ? `, ${awaiting} awaiting classification` : ""}`;
      const button = sidebarEntry(`category-filter${active ? " is-active" : ""}${total ? "" : " is-empty"}`, category.name, unread, summary);
      // Choosing the selected category again shows every category.
      button.dataset.categoryFilter = active ? "all" : category.id;
      button.dataset.category = category.id;
      button.setAttribute("aria-pressed", String(active));
      els.categoryFilters.append(button);
    });
    syncFilterButtons();
    restoreFocus(focus);
    if (window.EmailTownMailHost?.render) window.EmailTownMailHost.render();
  }
  function categoryStats() {
    const stats = new Map();
    const bump = (id, key) => {
      const entry = stats.get(id) || {
        total: 0,
        unread: 0,
        automatic: 0,
        corrected: 0,
        correctedAway: 0,
        awaiting: 0,
      };
      entry[key] += 1;
      stats.set(id, entry);
    };
    for (const message of filteredMessages("category")) {
      const current = message.category || "unknown";
      for (const id of [current, "all"]) {
        bump(id, "total");
        if (message.unread) bump(id, "unread");
        if (["model", "mailbox"].includes(message.source)) bump(id, "automatic");
        if (message.source === "pending") bump(id, "awaiting");
      }
      if (message.auto_category && message.auto_category !== current) {
        bump(current, "corrected");
        bump(message.auto_category, "correctedAway");
        bump("all", "corrected");
      }
    }
    return stats;
  }
  function renderCounts() {
    const today = localMidnight();
    const tomorrow = localMidnight(1);
    const scoped = filteredMessages("type");
    const views = {
      all: { label: "All dates", test: () => true },
      today: {
        label: "Today",
        test: (message) => Number(message.date) >= today && Number(message.date) < tomorrow,
      },
      backlog: { label: "Earlier", test: (message) => Number(message.date) < today },
    };
    for (const [type, view] of Object.entries(views)) {
      const { total, unread } = scopeStats(scoped.filter(view.test));
      const summary = `${view.label}: ${unread} unread of ${total} loaded in current scope`;
      const button = document.querySelector(`[data-type-filter="${type}"]`);
      button.setAttribute("aria-label", summary);
      button.title = summary;
    }
  }
  // One removable chip per narrowing filter, so the way back is always next to the list.
  function renderChips() {
    const chips = [];
    const chip = (label, clear) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "filter-chip";
      button.textContent = label;
      const x = document.createElement("span");
      x.setAttribute("aria-hidden", "true");
      x.textContent = "×";
      button.append(x);
      button.setAttribute("aria-label", `Remove filter: ${label}`);
      button.addEventListener("click", () => {
        clear();
        renderScope();
      });
      chips.push(button);
    };
    if (state.filters.category !== "all")
      chip(categoryById(state.filters.category)?.name || state.filters.category, () => {
        state.filters.category = "all";
      });
    if (state.filters.account !== "all")
      chip(accountById(state.filters.account)?.name || "Account", () => {
        state.filters.account = "all";
      });
    const query = state.filters.search.trim();
    if (query)
      chip(`“${query}”`, () => {
        els.search.value = "";
        state.filters.search = "";
      });
    $("filterChips").replaceChildren(...chips);
  }
  function renderScope() {
    renderCounts();
    renderChips();
    renderFilters();
    renderList();
  }
  function renderMailboxNotice() {
    const truncated = state.accounts.filter((account) => account.truncated);
    const loaded = state.accounts.reduce(
      (sum, account) => sum + (Number(account.loaded) || 0),
      0,
    );
    els.loadMoreBar.hidden = truncated.length === 0;
    els.loadMore.disabled = state.limit >= 1000;
    els.loadedSummary.textContent = truncated.length
      ? `Loaded ${state.folder || "inbox"} messages: ${loaded}${state.limit >= 1000 ? " · 1,000 per-account limit reached" : ""}`
      : "";
  }

  const rowNodes = new Map();
  const setText = (node, value) => {
    const next = text(value);
    if (node.textContent !== next) node.textContent = next;
  };
  // "Maria Chen <maria@x>" -> { name: "Maria Chen", address: "maria@x" }; reader.js has the full parser
  // but loads after this file, and a row must render either way.
  function senderParts(value) {
    const raw = text(value).trim();
    const match = raw.match(/^\s*"?([^"<]*?)"?\s*<([^>]*)>\s*$/);
    if (match) return { name: match[1].trim(), address: match[2].trim() };
    return { name: "", address: raw };
  }
  // Outlook's row: an avatar (a checkbox when pointing at it or selecting), then two lines: the sender
  // and the time; the subject, its category and the star. Fixed height, so the list never reflows.
  function createRow(message) {
    const row = document.createElement("article");
    row.className = "message-row";
    row.dataset.messageId = message.id;
    const lead = document.createElement("div");
    lead.className = "row-lead";
    const avatar = document.createElement("span");
    avatar.className = "row-avatar";
    avatar.setAttribute("aria-hidden", "true");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "row-check";
    checkbox.dataset.focusKey = `select-${message.id}`;
    lead.append(avatar, checkbox);
    const body = document.createElement("div");
    body.className = "row-content";
    const title = document.createElement("div");
    title.className = "row-title";
    const sender = document.createElement("span");
    sender.className = "row-sender";
    const date = document.createElement("time");
    title.append(sender, date);
    const meta = document.createElement("div");
    meta.className = "row-meta";
    const subject = document.createElement("button");
    subject.type = "button";
    subject.dataset.focusKey = `open-${message.id}`;
    subject.className = "row-open row-subject";
    const category = document.createElement("span");
    category.className = "category-badge";
    const account = document.createElement("span");
    account.className = "row-account";
    const starred = document.createElement("span");
    starred.className = "row-starred-badge";
    starred.textContent = "★";
    starred.setAttribute("aria-label", "Starred");
    const unread = document.createElement("span");
    unread.className = "row-unread-label sr-only";
    unread.textContent = "Unread";
    meta.append(subject, category, account, starred, unread);
    const snippet = document.createElement("div");
    snippet.className = "row-preview sr-only";
    body.append(title, meta, snippet);
    row.append(lead, body);
    return row;
  }
  function syncRow(row, message) {
    row.classList.toggle("is-unread", Boolean(message.unread));
    row.classList.toggle("is-focused", message.id === state.activeId);
    row.classList.toggle("is-selected", state.selected.has(message.id));
    const subjectText = text(message.subject || "No subject");
    const checkbox = row.querySelector(".row-check");
    checkbox.checked = state.selected.has(message.id);
    checkbox.setAttribute("aria-label", `Select ${subjectText}`);
    const subject = row.querySelector(".row-open");
    setText(subject, subjectText);
    subject.title = subjectText;
    const who = senderParts(message.sender);
    const senderNode = row.querySelector(".row-sender");
    setText(senderNode, who.name || who.address || "Unknown sender");
    senderNode.title = text(message.sender);
    const avatar = row.querySelector(".row-avatar");
    const reader = window.EmailTownReader;
    if (reader) reader.avatar(avatar, who);
    else setText(avatar, (who.name || who.address || "?")[0].toUpperCase());
    const dateValue = new Date(Number(message.date) || 0);
    const date = row.querySelector("time");
    date.title = formatDate(message.date);
    date.dateTime = dateValue.toISOString();
    const thisYear = dateValue.getFullYear() === new Date().getFullYear();
    setText(
      date,
      new Intl.DateTimeFormat(
        "en-US",
        Number(message.date) >= localMidnight()
          ? { hour: "numeric", minute: "2-digit" }
          : thisYear
            ? { month: "short", day: "numeric" }
            : { year: "2-digit", month: "numeric", day: "numeric" },
      ).format(dateValue),
    );
    const category = row.querySelector(".category-badge");
    const categoryId = message.category || "unknown";
    category.dataset.category = categoryId;
    setText(category, categoryById(message.category)?.name || message.category || "Unclassified");
    category.title = `Classification: ${sourceName(message.source)}`;
    category.hidden = categoryId === "unknown"; // no category is not worth a chip on every row
    const account = row.querySelector(".row-account");
    setText(account, accountById(message.account_id)?.name || "Unknown account");
    account.hidden = state.accounts.length < 2;
    row.querySelector(".row-starred-badge").hidden = !message.starred;
    row.querySelector(".row-unread-label").hidden = !message.unread;
    setText(row.querySelector(".row-preview"), `Classification: ${sourceName(message.source)}`);
    window.EmailTownRowActions?.sync(row);
  }

  function renderList() {
    if (state.authLost) {
      els.selectionToolbar.hidden = true;
      $("mailSelectionToolbar").hidden = true;
      const error = document.createElement("div");
      error.className = "error-state";
      const heading = document.createElement("h3");
      heading.textContent = "Session expired";
      const help = document.createElement("p");
      help.textContent = "Reopen Email Town from the launcher to reconnect.";
      error.append(heading, help);
      els.messageList.replaceChildren(error);
      return;
    }
    window.EmailTownMailHost?.render?.();
    if (state.activeId) updatePreviewPosition();
    const focus = saveFocus();
    const messages = filteredMessages();
    const hasLoading = state.refreshing && !state.messages.length;
    const folderName =
      { inbox: "Inbox", sent: "Sent", archive: "Archive", trash: "Trash" }[
        state.folder
      ] || "Inbox";
    els.listTitle.textContent = folderName;
    const categoryName =
      state.filters.category === "all"
        ? ""
        : categoryById(state.filters.category)?.name;
    const dateName = { all: "", today: "Today", backlog: "Earlier" }[state.filters.type];
    const query = state.filters.search.trim();
    const context = [
      state.filters.account === "all"
        ? "All accounts"
        : accountById(state.filters.account)?.name,
      folderName,
      dateName,
      categoryName,
      query ? `Search: “${query}”` : "",
    ]
      .filter(Boolean)
      .join(" / ");
    $("listContext").textContent = context;
    // Nothing to clear, no ✕; no mailbox yet, nothing to sync or write from.
    const filtered =
      Boolean(query) ||
      state.filters.type !== "all" ||
      state.filters.account !== "all" ||
      state.filters.category !== "all";
    els.clearFilters.hidden = !filtered;
    const hasAccounts = state.accounts.length > 0;
    els.syncIndicator.hidden = !hasAccounts;
    $("refreshButton").hidden = !hasAccounts;
    $("composeButton").hidden = !hasAccounts;
    $("previewEmptyTitle").textContent = hasAccounts ? "Select a message" : "Your mail will show here";
    $("previewEmptyText").textContent = hasAccounts
      ? "Pick a message from the list to read it here."
      : "Add an email account to start. Your mail stays on this computer.";
    $("listContext").title = context;
    $("listContext").setAttribute("aria-label", context);
    els.messageList.setAttribute("aria-label", `${els.listTitle.textContent}: ${context}`);
    $("syncIndicator").title = state.lastRefresh
      ? `Last sync ${formatDate(state.lastRefresh)}`
      : "";
    els.listStatus.textContent = state.modelError
      ? `Classifier: ${text(state.modelError)}`
      : state.modelStatus?.state === "downloading"
        ? `Downloading the on-device classifier (${text(state.modelStatus.size)}, once). Mail is readable meanwhile.`
        : state.stale
        ? "Sync failed; showing the last loaded data."
        : "";
    renderMailboxNotice();
    els.selectionToolbar.hidden = !state.messages.length;
    els.selectionCount.textContent = state.selected.size
      ? `${state.selected.size} selected`
      : `${messages.length} messages`;
    $("clearSelectionButton").hidden = !state.selected.size;
    els.messageList.classList.toggle("has-selection", state.selected.size > 0);
    els.selectAll.indeterminate =
      messages.some((m) => state.selected.has(m.id)) &&
      !messages.every((m) => state.selected.has(m.id));
    els.selectAll.checked =
      messages.length > 0 && messages.every((m) => state.selected.has(m.id));
    if (hasLoading) {
      const loading = document.createElement("div");
      loading.className = "loading-state";
      const h = document.createElement("h3");
      h.textContent = "Reading local accounts";
      loading.append(h, document.createTextNode("Your mail list will appear shortly."));
      els.messageList.replaceChildren(loading);
      restoreFocus(focus);
      return;
    }
    if (state.listError && !state.initialized) {
      const error = document.createElement("div");
      error.className = "error-state";
      const h = document.createElement("h3");
      h.textContent = "Mail could not be loaded";
      const p = document.createElement("p");
      p.textContent = "Retry mail load to use the current session without restarting.";
      error.append(h, p);
      els.messageList.replaceChildren(error);
      restoreFocus(focus);
      return;
    }
    if (!messages.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const h = document.createElement("h3");
      const p = document.createElement("p");
      const scopedAccounts = state.accounts.filter(
        (account) => state.filters.account === "all" || account.id === state.filters.account,
      );
      const hasAccountMail = state.messages.some(
        (message) => state.filters.account === "all" || message.account_id === state.filters.account,
      );
      if (!state.accounts.length) {
        h.textContent = "Welcome to Email Town";
        p.textContent =
          "Connect Gmail, Outlook, iCloud, QQ or any IMAP mailbox. Email Town reads it directly from your provider and sorts it on this computer.";
        const add = document.createElement("button");
        add.type = "button";
        add.className = "button button-primary";
        add.id = "emptyAddAccount";
        add.textContent = "Add an email account";
        add.addEventListener("click", () => openAccountForm());
        empty.append(h, p, add);
      } else if (!hasAccountMail && scopedAccounts.some(
        (account) => account.enabled !== false && account.status === "error",
      )) {
        h.textContent = "Mail could not be loaded";
        p.textContent = "Check accounts marked Error, then refresh. This does not mean the folder is empty.";
      } else if (!hasAccountMail && scopedAccounts.length && scopedAccounts.every((account) => account.enabled === false)) {
        h.textContent = "Accounts in this view are paused";
        p.textContent = "Resume an account from its menu to load mail.";
      } else if (!state.messages.length) {
        h.textContent = `No mail in ${folderName}`;
        p.textContent = "This folder is empty for the connected accounts.";
      } else {
        h.textContent = "No messages match these filters";
        p.textContent = "Try clearing a filter or search term.";
      }
      if (!empty.childElementCount) empty.append(h, p);
      els.messageList.replaceChildren(empty);
      restoreFocus(focus);
      return;
    }
    const loadedIds = new Set(state.messages.map((message) => message.id));
    for (const [id, row] of rowNodes) {
      if (!loadedIds.has(id)) {
        row.remove();
        rowNodes.delete(id);
      }
    }
    const visibleIds = new Set(messages.map((message) => message.id));
    for (const child of [...els.messageList.children]) {
      if (!child.classList.contains("message-row") || !visibleIds.has(child.dataset.messageId))
        child.remove();
    }
    messages.forEach((message, index) => {
      let row = rowNodes.get(message.id);
      if (!row) {
        row = createRow(message);
        rowNodes.set(message.id, row);
      }
      syncRow(row, message);
      const current = els.messageList.children[index];
      if (current !== row) els.messageList.insertBefore(row, current || null);
    });
    els.selectionToolbar.hidden =
      state.selected.size === 0 && !state.messages.length;
    els.selectionCount.textContent = state.selected.size
      ? `${state.selected.size} selected`
      : `${messages.length} messages`;
    $("clearSelectionButton").hidden = !state.selected.size;
    els.selectAll.indeterminate =
      messages.some((m) => state.selected.has(m.id)) &&
      !messages.every((m) => state.selected.has(m.id));
    els.selectAll.checked =
      messages.length > 0 &&
      messages.every((message) => state.selected.has(message.id));
    restoreFocus(focus);
  }
  els.messageList.addEventListener("click", (event) => {
    const row = event.target.closest(".message-row[data-message-id]");
    if (!row || !els.messageList.contains(row)) return;
    if (event.target.closest("[data-row-action]")) return;
    if (event.target.closest(".row-check")) {
      event.stopPropagation();
      toggleSelected(row.dataset.messageId, event.target.checked);
      return;
    }
    // A shortcut (j/k) opens by clicking the row's button from script; a person's click is trusted.
    openMessage(row.dataset.messageId, {
      via: event.isTrusted ? `click:${event.target.closest("[class]")?.classList[0] || "row"}` : "key",
    });
  });

  // One body request per message, shared by the reading pane and the reply/attachment code, kept for
  // the last 150 opened or prefetched. A failed read is forgotten so the next open tries again.
  const detailCache = new Map();
  const DETAIL_CACHE = 150;
  function messageDetail(id) {
    let pending = detailCache.get(id);
    if (pending) {
      detailCache.delete(id);
    } else {
      pending = api(`/api/message?id=${encodeURIComponent(id)}`);
      pending.catch(() => detailCache.delete(id));
    }
    detailCache.set(id, pending);
    while (detailCache.size > DETAIL_CACHE)
      detailCache.delete(detailCache.keys().next().value);
    return pending;
  }
  // Like Outlook, the next messages are read before they are opened: the three below the open one and
  // the one above, fetched by the server in one batch, then kept here.
  let prefetchTimer = null;
  function prefetchAround(id) {
    clearTimeout(prefetchTimer);
    prefetchTimer = setTimeout(async () => {
      const ids = filteredMessages().map((m) => m.id);
      const at = ids.indexOf(id);
      if (at < 0 || state.activeId !== id) return;
      const near = [...ids.slice(at + 1, at + 4), ids[at - 1]].filter(
        (other) => other && !detailCache.has(other),
      );
      if (!near.length) return;
      try {
        await api("/api/prefetch", {
          method: "POST",
          body: JSON.stringify({ ids: near }),
        });
        near.forEach((other) => messageDetail(other).catch(() => {}));
      } catch (_) {
        // Best effort: the open itself still reads the message.
      }
    }, 150);
  }
  function openMessage(id, { retry = false, via = "code" } = {}) {
    const message = state.messages.find((m) => m.id === id);
    uiLog("open", {
      id: k8(id),
      via: retry ? "retry" : via,
      was: state.activeId === id ? "same" : k8(state.activeId),
      unread: Boolean(message?.unread),
      selected: state.selected.size,
      in_selection: state.selected.has(id),
    });
    state.activeId = id;
    renderList();
    els.messageList.querySelector(
      `.message-row[data-message-id="${CSS.escape(id)}"]`,
    )?.scrollIntoView({ block: "nearest" });
    els.previewPane.classList.add("is-open");
    els.previewEmpty.hidden = true;
    els.previewContent.hidden = false;
    loadPreview(id, retry);
    if (window.innerWidth < 1200) els.previewClose.focus();
  }
  function safeOriginalUrl(value) {
    try {
      const url = new URL(value);
      const allowed = [
        "mail.google.com",
        "outlook.office.com",
        "outlook.office365.com",
        "outlook.live.com",
      ];
      if (
        url.protocol !== "https:" ||
        !allowed.includes(url.hostname.toLowerCase()) ||
        url.username ||
        url.password ||
        (url.port && url.port !== "443")
      )
        return "";
      return url.href;
    } catch (_) {
      return "";
    }
  }
  async function loadPreview(id, retry = false) {
    const message = state.messages.find((item) => item.id === id);
    if (!message) return;
    const request = ++state.previewRequest;
    state.previewLoading = true;
    if (!retry) {
      state.previewError = "";
      state.previewErrorId = null;
      state.previewRetryFocus = false;
    }
    renderPreviewError();
    els.previewSource.textContent = text(
      accountById(message.account_id)?.name || "Unknown account",
    );
    els.previewSource.title = `Classification: ${sourceName(message.source)}`;
    els.previewSource.hidden = state.accounts.length < 2;
    els.previewCategory.textContent = text(
      categoryById(message.category)?.name ||
        message.category ||
        "Unclassified",
    );
    els.previewCategory.dataset.category = message.category || "unknown";
    els.previewUnread.hidden = !message.unread;
    els.previewSubject.textContent = text(message.subject || "No subject");
    const reader = window.EmailTownReader;
    if (reader) {
      // Gmail's header: avatar, bold name, grey address, the date on the right.
      const who = reader.parseAddress(message.sender || "");
      const name = document.createElement("strong");
      name.className = "sender-name";
      name.textContent = text(reader.displayName(who) || "Unknown sender");
      const address = document.createElement("span");
      address.className = "sender-email";
      address.textContent = who.name ? `<${who.address}>` : "";
      els.previewByline.replaceChildren(name, " ", address);
      reader.avatar($("previewAvatar"), who);
      els.previewDate.textContent = reader.longDate(message.date);
    } else {
      els.previewByline.textContent = text(message.sender || "Unknown sender");
      els.previewDate.textContent = formatDate(message.date);
    }
    els.previewDate.title = formatDate(message.date);
    // Only say "Reading message…" when it really is a wait: a cached or prefetched body is drawn first.
    window.EmailTownHtmlView?.clear();
    els.previewBody.classList.remove("is-formatted");
    els.previewBody.textContent = "";
    const readingNote = setTimeout(() => {
      if (
        state.activeId === id &&
        state.previewRequest === request &&
        !els.previewBody.textContent
      )
        els.previewBody.textContent = "Reading message…";
    }, 300);
    updatePreviewPosition();
    const setOriginalLink = (value) => {
      const knownUrl = safeOriginalUrl(value);
      els.originalLink.hidden = !knownUrl;
      if (knownUrl) els.originalLink.href = knownUrl;
    };
    setOriginalLink(message.url);
    try {
      const data = await messageDetail(id);
      clearTimeout(readingNote);
      if (state.activeId === id && state.previewRequest === request) {
        const body = text(data.body || "(No plain-text body)");
        const renderPlain = (target) => {
          if (window.EmailTownReader) window.EmailTownReader.renderBody(target, body);
          else target.textContent = body;
        };
        if (window.EmailTownHtmlView) window.EmailTownHtmlView.render(els.previewBody, data, renderPlain);
        else renderPlain(els.previewBody);
        setOriginalLink(data.url || message.url);
        clearPreviewError(request);
        prefetchAround(id);
      }
    } catch (error) {
      clearTimeout(readingNote);
      if (state.activeId !== id || state.previewRequest !== request) return;
      if (els.previewBody.textContent === "Reading message…")
        els.previewBody.textContent = "";
      state.previewLoading = false;
      state.previewError = `Unable to read message: ${text(error.message)}`;
      state.previewErrorId = id;
      renderPreviewError();
      if (state.previewRetryFocus) {
        const retryFocused = document.activeElement === els.retryPreview;
        state.previewRetryFocus = false;
        if (!usableFocus() || retryFocused)
          els.retryPreview.focus({ preventScroll: true });
      }
    }
  }
  // "2 of 37" and the newer/older arrows in the reading pane's toolbar, over the list as shown.
  function updatePreviewPosition() {
    const ids = filteredMessages().map((m) => m.id);
    const at = ids.indexOf(state.activeId);
    $("previewPosition").textContent =
      at >= 0 ? `${at + 1} of ${ids.length}` : "";
    $("previewNewer").disabled = at <= 0;
    $("previewOlder").disabled = at < 0 || at >= ids.length - 1;
  }
  function stepPreview(step) {
    const ids = filteredMessages().map((m) => m.id);
    const next = ids[ids.indexOf(state.activeId) + step];
    if (next) openMessage(next, { via: step < 0 ? "newer-arrow" : "older-arrow" });
  }
  function toggleSelected(id, checked) {
    uiLog(checked ? "select" : "deselect", {
      id: k8(id),
      active: state.activeId === id,
      selected_after: state.selected.size + (checked && !state.selected.has(id) ? 1 : 0) - (!checked && state.selected.has(id) ? 1 : 0),
    });
    if (checked) state.selected.add(id);
    else state.selected.delete(id);
    renderList();
  }

  async function performAction(ids, action, category, via = "toolbar") {
    uiLog("workflow-action", {
      action,
      via,
      ...(category ? { category } : {}),
      ids: ids.slice(0, 20).map(k8),
      count: ids.length,
      active: k8(state.activeId),
      includes_active: ids.includes(state.activeId),
    });
    if (!ids.length) return;
    const affected = new Set(ids);
    const focusIndex = Math.max(
      0,
      filteredMessages().findIndex((message) => affected.has(message.id)),
    );
    try {
      const result = await api("/api/action", {
        method: "POST",
        body: JSON.stringify({
          ids,
          action,
          ...(category ? { category } : {}),
        }),
      });
      ids.forEach((id) => {
        const message = state.messages.find((item) => item.id === id);
        if (message) {
          if (action === "category") {
            if (["model", "mailbox"].includes(message.source))
              message.auto_category = message.category;
            message.category = category;
            message.source = "manual";
          }
        }
      });
      state.selected.clear();
      renderScope();
      ensureListFocus(focusIndex);
      if (state.activeId) {
        const current = state.messages.find(
          (message) => message.id === state.activeId,
        );
        if (current) {
          els.previewCategory.textContent = text(
            categoryById(current.category)?.name ||
              current.category ||
              "Unclassified",
          );
          els.previewCategory.dataset.category = current.category || "unknown";
        }
      }
      if (result.undo) showUndoToast(result.undo);
      else
        showToast("Category updated locally");
    } catch (error) {
      showToast(error.message);
    }
  }
  function showCategoryDialog(ids) {
    els.categorySelect.replaceChildren();
    state.categories.forEach((category) => {
      const option = document.createElement("option");
      option.value = category.id;
      option.textContent = text(category.name);
      els.categorySelect.append(option);
    });
    els.categoryDialog.dataset.ids = JSON.stringify(ids);
    const current = state.messages.find(
      (message) => message.id === state.activeId,
    );
    if (current?.category) els.categorySelect.value = current.category;
    els.categoryDialog.showModal();
  }
  function renderJobs() {
    els.demoBanner.hidden = !state.demo;
    els.jobStatus.replaceChildren();
    const failed = state.accounts.filter(
      (account) => account.status === "error" && account.error,
    );
    const jobs = state.jobs;
    if (!failed.length && !jobs.length) {
      els.jobStatus.hidden = true;
      return;
    }
    els.jobStatus.hidden = false;
    failed.forEach((account) => {
      const item = document.createElement("div");
      item.textContent = `${text(account.name)}: ${text(account.error)}`;
      els.jobStatus.append(item);
    });
    jobs.forEach((job) => {
      const item = document.createElement("div");
      item.textContent = `${text(job.message || "Account connection")} ${job.status === "error" ? "(failed)" : job.status === "pending" || job.status === "waiting" ? "(waiting for action)" : job.status === "complete" ? "(connected)" : ""}`;
      if (job.user_code) {
        const code = document.createElement("strong");
        code.textContent = ` Code: ${text(job.user_code)}`;
        item.append(code);
      }
      try {
        const url = new URL(job.verification_url || "");
        if (
          [
            "https://microsoft.com/devicelogin",
            "https://www.microsoft.com/devicelogin",
          ].includes(url.href)
        ) {
          const link = document.createElement("a");
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "Open verification page";
          item.append(" ", link);
        }
      } catch (_) {
        /* Ignore unsafe job URLs. */
      }
      els.jobStatus.append(item);
    });
  }

  // The footer says what the background outbox is still doing: "Archiving 12 of 40 · Starring 0 of 2".
  const OUTBOX_VERBS = {
    archive: "Archiving",
    trash: "Moving to Trash",
    restore: "Restoring",
    read: "Marking read",
    unread: "Marking unread",
    star: "Starring",
    unstar: "Unstarring",
  };
  function renderOutbox() {
    if (state.authLost) return;
    const busy = state.outbox.map(
      (job) =>
        `${OUTBOX_VERBS[job.action] || "Updating"} ${Number(job.done) + Number(job.failed)} of ${Number(job.total)}`,
    );
    const status = busy.length
      ? busy.join(" · ")
      : state.refreshing
        ? "Syncing mail…"
        : "Classified on your device";
    els.footerStatus.textContent = status;
    els.footerStatus.title = status;
    els.footerStatus.classList.toggle("is-working", busy.length > 0);
  }
  function reconcileMessageState() {
    const ids = new Set(state.messages.map((message) => message.id));
    state.selected.forEach((id) => {
      if (!ids.has(id)) state.selected.delete(id);
    });
    if (state.activeId && !ids.has(state.activeId)) {
      state.activeId = null;
      state.previewRequest += 1;
      state.previewLoading = false;
      state.previewError = "";
      state.previewErrorId = null;
      state.previewRetryFocus = false;
      els.previewPane.classList.remove("is-open");
      els.previewEmpty.hidden = false;
      els.previewContent.hidden = true;
      renderPreviewError();
    }
  }

  async function loadState() {
    if (state.syncing || state.authLost || !state.sessionToken) return;
    state.syncing = true;
    renderListError();
    if (!state.initialized) {
      state.refreshing = true;
      setSync("loading", "Reading");
      renderList();
    }
    try {
      applyState(await api("/api/state"));
    } catch (error) {
      if (error.message !== "unauthorized") {
        state.stale = state.initialized;
        if (!state.initialized) state.refreshing = false;
        state.listError = error.message;
        setSync("error", "Read failed");
        els.restart.hidden = false;
        renderListError();
        renderList();
      }
    } finally {
      state.syncing = false;
      renderListError();
      if (state.listError && state.listRetryFocus) {
        const retryFocused = document.activeElement === els.retryMail;
        state.listRetryFocus = false;
        if (!usableFocus() || retryFocused)
          els.retryMail.focus({ preventScroll: true });
      }
    }
    watchState();
  }
  // Long-poll: the server answers as soon as its state differs from our etag
  // (or after ~10 s unchanged), so new mail appears without a fixed delay.
  async function watchState() {
    if (state.watching) return;
    state.watching = true;
    try {
      while (!document.hidden && !state.authLost && state.sessionToken) {
        try {
          const data = await api(
            `/api/state?wait=${encodeURIComponent(state.etag || "0".repeat(32))}`,
          );
          // A stale answer (older seq) must not turn the long-poll into a busy loop.
          if (!data.unchanged && !applyState(data))
            await new Promise((resolve) => setTimeout(resolve, 1000));
        } catch (error) {
          if (error.message === "unauthorized") break;
          await new Promise((resolve) => setTimeout(resolve, 3000));
          await loadState();
        }
      }
    } finally {
      state.watching = false;
    }
  }
  function applyState(data) {
    // Immediate reads and the long-poll overlap; never let an older answer win.
    if (state.authLost || !state.sessionToken || !data || Number(data.seq) < state.seq)
      return false;
    if (state.initialized && Number(data.seq) === state.seq) {
      const recovering = state.stale || Boolean(state.listError);
      state.stale = false;
      renderSyncStatus();
      if (recovering) renderList();
      clearListError();
      els.restart.hidden = true;
      return false;
    }
    state.seq = Number(data.seq);
    state.etag = text(data.etag);
    const next = {
      folder: data.folder || state.folder || "inbox",
      accounts: Array.isArray(data.accounts) ? data.accounts : [],
      messages: Array.isArray(data.messages) ? data.messages : [],
      categories: Array.isArray(data.categories) ? data.categories : [],
      refreshing: Boolean(data.refreshing),
      lastRefresh: data.last_refresh || null,
      jobs: Array.isArray(data.jobs) ? data.jobs : [],
      outbox: Array.isArray(data.outbox) ? data.outbox : [],
      demo: Boolean(data.demo),
      limit: Number(data.limit) || 100,
      modelError: data.model_error || "",
      modelStatus: data.model_status || null,
    };
    // A read flag the page set itself (opening a message) holds until a snapshot newer than the server's
    // acknowledgement arrives: a long-poll answer generated before the write must not undo it.
    next.messages.forEach((m) => {
      const hold = flagHolds.get(m.id);
      if (!hold) return;
      if (hold.seq !== null && Number(data.seq) > hold.seq) flagHolds.delete(m.id);
      else if (m.unread !== hold.unread) {
        uiLog("flag-held", { id: k8(m.id), seq: Number(data.seq), until: hold.seq ?? "ack" });
        m.unread = hold.unread;
      }
    });
    // The server's answer changing a flag the page already showed: what a list refresh or queued write did.
    if (state.initialized) {
      const before = new Map(state.messages.map((m) => [m.id, m]));
      const flips = [];
      next.messages.forEach((m) => {
        const old = before.get(m.id);
        if (old && old.unread !== m.unread)
          flips.push(`${k8(m.id)}:${m.unread ? "unread" : "read"}${m.id === state.activeId ? ":open" : ""}`);
      });
      if (flips.length) uiLog("state-flags", { seq: Number(data.seq), changes: flips.slice(0, 20) });
    }
    const snapshot = JSON.stringify(next);
    const changed = snapshot !== state.snapshot;
    const recovering = state.stale || Boolean(state.listError);
    Object.assign(state, next, { initialized: true, stale: false, snapshot });
    reconcileMessageState();
    renderOutbox();
    if (state.activeId) {
      const active = state.messages.find((m) => m.id === state.activeId);
      els.previewUnread.hidden = !active.unread;
      els.previewCategory.textContent =
        categoryById(active.category)?.name ||
        active.category ||
        "Unclassified";
      els.previewCategory.dataset.category = active.category || "unknown";
    }
    renderSyncStatus();
    if (changed) {
      renderScope();
      renderJobs();
    } else if (recovering) {
      renderList();
    }
    clearListError();
    els.restart.hidden = true;
    return true;
  }
  async function refresh() {
    if (state.syncing || state.authLost || !state.sessionToken) return;
    state.refreshing = true;
    setSync("loading", "Refreshing");
    renderList();
    try {
      await api("/api/refresh", { method: "POST", body: "{}" });
      showToast("Refresh started; mail will update locally");
      await loadState();
    } catch (error) {
      showToast(error.message);
      state.refreshing = false;
      renderList();
    }
  }

  // What this copy can sign in with, and suggested server settings per address domain.
  async function loadSetup(force = false) {
    if (state.setup && !force) return state.setup;
    try {
      state.setup = await api("/api/setup");
    } catch (_) {
      state.setup = { google_signin: true, microsoft_default: false, presets: [] };
    }
    return state.setup;
  }
  function presetFor(email) {
    const domain = text(email).trim().toLowerCase().split("@")[1] || "";
    return (state.setup?.presets || []).find((preset) =>
      (preset.domains || []).includes(domain),
    );
  }
  function applyPreset() {
    const email = els.imapEmail.value.trim();
    if (!email) return;
    if (!els.imapUsername.value || els.imapUsername.dataset.auto === "1") {
      els.imapUsername.value = email;
      els.imapUsername.dataset.auto = "1";
    }
    const preset = presetFor(email);
    els.useMicrosoft.hidden = preset?.use !== "microsoft";
    els.imapPresetHelp.hidden = !preset;
    els.imapPresetText.textContent = preset ? text(preset.help) : "";
    const link = text(preset?.help_url);
    els.imapPresetLink.hidden = !link.startsWith("https://");
    if (link.startsWith("https://")) els.imapPresetLink.href = link;
    if (preset && !preset.use) {
      for (const [field, key] of [
        ["imapHost", "host"],
        ["imapPort", "port"],
        ["smtpHost", "smtp_host"],
        ["smtpPort", "smtp_port"],
        ["smtpSecurity", "smtp_security"],
      ])
        $(field).value = preset[key];
      els.imapServerDetails.open = false;
    } else if (!preset) {
      els.imapServerDetails.open = true;
    }
  }
  async function importGoogleClient() {
    const file = els.googleClientFile.files?.[0];
    if (!file) return;
    els.accountError.hidden = true;
    try {
      if (file.size > 16384) throw new Error("This is not a Google OAuth client file");
      let client;
      try {
        client = JSON.parse(await file.text());
      } catch (_) {
        throw new Error("Choose the JSON file you downloaded for a Google OAuth client");
      }
      await api("/api/google-client", {
        method: "POST",
        body: JSON.stringify({ client }),
      });
      await loadSetup(true);
      showToast("Google sign-in is ready");
    } catch (error) {
      els.accountError.textContent = error.message;
      els.accountError.hidden = false;
    } finally {
      els.googleClientFile.value = "";
      setProviderFields();
    }
  }
  function setProviderFields() {
    const provider = els.provider.value;
    const setup = state.setup || {};
    els.existingFields.hidden = provider !== "gmail-existing";
    els.imapFields.hidden = provider !== "imap";
    els.gmailFields.hidden = provider !== "gmail";
    els.googleStatus.textContent = setup.google_signin
      ? setup.google_source === "own"
        ? "A browser window opens to sign in with Google, using your own Google Cloud client."
        : "A browser window opens to sign in with Google."
      : "Google sign-in isn't set up in this copy yet. Import a Google OAuth client file, or choose Other email to use a Gmail app password.";
    els.googleClientImport.hidden = Boolean(setup.google_signin);
    els.microsoftHelp.hidden = provider !== "microsoft";
    els.microsoftHelp.textContent = setup.microsoft_default
      ? "You'll get a short code to enter at microsoft.com/devicelogin, then grant access to read, send and manage mail."
      : "Microsoft sign-in needs an app registration with public-client (device code) flow enabled. Paste its Application (client) ID.";
    els.clientIdFields.hidden = provider !== "microsoft";
    if (provider === "microsoft" && !setup.microsoft_default) els.clientIdFields.open = true;
    els.existingFields.querySelectorAll("input").forEach((input) => {
      input.disabled = provider !== "gmail-existing";
    });
    els.imapFields.querySelectorAll("input").forEach((input) => {
      input.disabled = provider !== "imap";
    });
    els.clientIdFields.querySelectorAll("input").forEach((input) => {
      input.disabled = provider !== "microsoft";
    });
    els.submitAccount.textContent =
      provider === "microsoft"
        ? "Start device login"
        : provider === "imap"
          ? "Save and connect"
          : "Connect";
  }
  async function submitAccount(event) {
    event.preventDefault();
    els.accountError.hidden = true;
    const form = new FormData(els.accountForm);
    const provider = els.provider.value;
    const payload = { provider, name: text(form.get("name")).trim() };
    if (els.accountForm.dataset.reconnectId)
      payload.reconnect_id = els.accountForm.dataset.reconnectId;
    if (provider === "gmail-existing")
      payload.home = text(form.get("home")).trim();
    if (provider === "imap")
      Object.assign(payload, {
        host: text(form.get("host")).trim(),
        port: Number(form.get("port") || 993),
        username: text(form.get("username")).trim(),
        password: text(form.get("password")),
        smtp_host: text(form.get("smtp_host")).trim(),
        smtp_port: Number(form.get("smtp_port") || 587),
        smtp_security: text(form.get("smtp_security") || "starttls"),
      });
    if (provider === "microsoft")
      payload.client_id = text(form.get("client_id")).trim();
    els.submitAccount.disabled = true;
    try {
      const data = await api("/api/account", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      els.accountDialog.close();
      showToast(data.message || "Account connection started");
      await loadState();
    } catch (error) {
      els.accountError.textContent = error.message;
      els.accountError.hidden = false;
    } finally {
      const password = $("imapPassword");
      if (password) password.value = "";
      els.submitAccount.disabled = false;
    }
  }
  function bootstrapSession() {
    const token = new URLSearchParams(
      window.location.hash.replace(/^#/, ""),
    ).get("token");
    if (window.location.hash)
      history.replaceState(
        null,
        document.title,
        `${window.location.pathname}${window.location.search}`,
      );
    if (!token) {
      state.sessionToken = sessionStorage.getItem("email-town-session") || "";
      if (state.sessionToken) loadState();
      else handleUnauthorized();
      return;
    }
    api("/api/session", { method: "POST", body: JSON.stringify({ token }) })
      .then((data) => {
        if (!data.session) throw new Error("Session initialization failed");
        state.sessionToken = data.session;
        sessionStorage.setItem("email-town-session", data.session);
        loadState();
      })
      .catch((error) => {
        state.authLost = true;
        setSync("error", "Could not start local session");
        els.listStatus.textContent = `Could not start local session: ${text(error.message)}`;
        els.footerStatus.textContent = "Reopen Email Town from the launcher.";
        els.restart.hidden = false;
      });
  }

  document.addEventListener("click", (event) => {
    const close = event.target.closest("[data-close-dialog]");
    if (close) close.closest("dialog")?.close();
    const type = event.target.closest("[data-type-filter]");
    if (type) state.filters.type = type.dataset.typeFilter;
    const folder = event.target.closest("[data-mail-folder]");
    const account = event.target.closest("[data-account-filter]");
    if (account) state.filters.account = account.dataset.accountFilter;
    const category = event.target.closest("[data-category-filter]");
    if (category) state.filters.category = category.dataset.categoryFilter;
    if (type || account || category || folder) {
      renderScope();
      if (window.innerWidth < 1200) els.previewPane.classList.remove("is-open");
      if (window.innerWidth < 768) closeSidebar();
    }
  });
  // Drafts opens a dialog from the folder pane; on a phone the pane closes behind it (focus stays in the
  // dialog, so this does not go through closeSidebar, which focuses the menu button).
  $("draftsButton").addEventListener("click", () => {
    if (window.innerWidth >= 768 || !els.sidebar.classList.contains("is-open")) return;
    els.sidebar.classList.remove("is-open");
    els.sidebarToggle.setAttribute("aria-expanded", "false");
    syncMobileSidebar();
  });
  function syncMobileSidebar() {
    const open =
      window.innerWidth < 768 && els.sidebar.classList.contains("is-open");
    els.sidebar.inert = window.innerWidth < 768 && !open;
    $("sidebarBackdrop").hidden = !open;
  }
  function closeSidebar() {
    els.sidebar.classList.remove("is-open");
    els.sidebarToggle.setAttribute("aria-expanded", "false");
    syncMobileSidebar();
    els.sidebarToggle.focus();
  }
  els.sidebarToggle.addEventListener("click", () => {
    const open = els.sidebar.classList.toggle("is-open");
    els.sidebarToggle.setAttribute("aria-expanded", String(open));
    syncMobileSidebar();
    if (open) els.sidebarClose.focus();
  });
  els.sidebarClose.addEventListener("click", closeSidebar);
  $("sidebarBackdrop").addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (event) => {
    if (
      event.key !== "Tab" ||
      window.innerWidth >= 768 ||
      !els.sidebar.classList.contains("is-open") ||
      document.querySelector("dialog[open]")
    )
      return;
    const buttons = [...els.sidebar.querySelectorAll("button:not(:disabled)")];
    const first = buttons[0],
      last = buttons.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  $("previewNewer").addEventListener("click", () => stepPreview(-1));
  $("previewOlder").addEventListener("click", () => stepPreview(1));
  els.previewClose.addEventListener("click", () => {
    els.previewPane.classList.remove("is-open");
    restoreFocus({ key: "focusKey", value: `open-${state.activeId}` });
  });
  window.addEventListener("resize", syncMobileSidebar);
  syncMobileSidebar();
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && els.sidebar.classList.contains("is-open"))
      closeSidebar();
  });
  document.addEventListener("click", (event) => {
    document.querySelectorAll(".action-menu[open]").forEach((menu) => {
      if (
        !menu.contains(event.target) ||
        event.target.closest(".menu-panel button, .menu-panel a")
      ) {
        if (menu.contains(event.target)) {
          const dialog = document.querySelector("dialog[open]");
          if (dialog)
            dialog.addEventListener(
              "close",
              () => menu.querySelector("summary").focus(),
              { once: true },
            );
          else menu.querySelector("summary").focus({ preventScroll: true });
        }
        menu.open = false;
      }
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || document.querySelector("dialog[open]"))
      return;
    const menu = document.querySelector(".action-menu[open]");
    if (menu) {
      menu.open = false;
      menu.querySelector("summary").focus();
      event.stopImmediatePropagation();
    } else if (
      window.innerWidth < 1200 &&
      els.previewPane.classList.contains("is-open")
    )
      els.previewClose.click();
  });
  document.querySelectorAll(".action-menu").forEach((menu) => {
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      const panel = menu.querySelector(".menu-panel");
      const anchor = menu.querySelector("summary").getBoundingClientRect();
      const inset = 8;
      const availableBelow = window.innerHeight - anchor.bottom - inset * 2;
      const availableAbove = anchor.top - inset * 2;
      const above =
        availableBelow < Math.min(panel.scrollHeight, 240) &&
        availableAbove > availableBelow;
      const height = Math.min(
        panel.scrollHeight,
        above ? availableAbove : availableBelow,
      );
      panel.style.maxHeight = `${Math.max(48, height)}px`;
      // Menus open toward the content: right-aligned by default, from the left edge with data-align="start".
      const preferred =
        menu.dataset.align === "start"
          ? anchor.left
          : anchor.right - panel.offsetWidth;
      panel.style.left = `${Math.max(inset, Math.min(preferred, window.innerWidth - panel.offsetWidth - inset))}px`;
      panel.style.top = `${above ? anchor.top - height - inset : anchor.bottom + inset}px`;
    });
  });
  window.addEventListener("resize", () =>
    document.querySelectorAll(".action-menu[open]").forEach((menu) => {
      menu.open = false;
    }),
  );
  $("mobileRefreshButton").addEventListener("click", () => {
    refresh();
    closeSidebar();
  });
  els.refresh.addEventListener("click", refresh);
  els.retryMail.addEventListener("click", () => {
    if (state.syncing || state.authLost) return;
    state.listRetryFocus = true;
    loadState();
  });
  els.retryPreview.addEventListener("click", () => {
    const id = state.activeId;
    if (!id || state.previewLoading || state.authLost) return;
    state.previewRetryFocus = true;
    openMessage(id, { retry: true });
  });
  els.restart.addEventListener("click", () => window.location.reload());
  async function openAccountForm(account = null) {
    await loadSetup();
    els.accountForm.reset();
    delete els.imapUsername.dataset.auto;
    els.imapPresetHelp.hidden = true;
    els.useMicrosoft.hidden = true;
    // Known providers fill these in; they open for an unknown domain (applyPreset) or a reconnect.
    els.imapServerDetails.open = Boolean(account);
    els.accountError.hidden = true;
    els.accountForm.dataset.reconnectId = account?.id || "";
    els.provider.disabled = Boolean(account);
    $("accountDialogTitle").textContent = account
      ? "Reauthorize this mailbox"
      : "Add account";
    if (account) {
      $("accountName").value = account.name;
      els.provider.value = account.provider;
      const connection = account.connection || {};
      for (const [field, key] of [
        ["imapHost", "host"],
        ["imapPort", "port"],
        ["imapUsername", "username"],
        ["smtpHost", "smtp_host"],
        ["smtpPort", "smtp_port"],
        ["smtpSecurity", "smtp_security"],
        ["clientId", "client_id"],
      ]) {
        if (connection[key] !== undefined) $(field).value = connection[key];
      }
    }
    // A new account starts on the route that works in this copy: Google sign-in when it ships with one,
    // otherwise the email-address form (app password), which also covers Gmail.
    if (!account) els.provider.value = state.setup?.google_signin ? "gmail" : "imap";
    setProviderFields();
    els.accountDialog.showModal();
    if (!account && els.provider.value === "imap") els.imapEmail.focus();
    else els.provider.focus();
  }
  function showAccountManagement(id) {
    const account = accountById(id);
    if (!account) return;
    $("manageAccountDialog").dataset.accountId = id;
    $("manageAccountTitle").textContent = account.name;
    $("manageAccountStatus").textContent =
      account.error ||
      (account.enabled ? "Automatic sync is on" : "Automatic sync is paused");
    $("pauseAccountButton").textContent = account.enabled
      ? "Pause sync"
      : "Resume sync";
    $("manageAccountDialog").showModal();
  }
  $("pauseAccountButton").addEventListener("click", async () => {
    const account = accountById($("manageAccountDialog").dataset.accountId);
    try {
      await api("/api/account-action", {
        method: "POST",
        body: JSON.stringify({ id: account.id, enabled: !account.enabled }),
      });
      $("manageAccountDialog").close();
      await loadState();
    } catch (error) {
      showToast(error.message);
    }
  });
  $("reconnectAccountButton").addEventListener("click", () => {
    const account = accountById($("manageAccountDialog").dataset.accountId);
    $("manageAccountDialog").close();
    openAccountForm(account);
  });
  els.addAccount.addEventListener("click", () => openAccountForm());
  els.provider.addEventListener("change", setProviderFields);
  els.imapEmail.addEventListener("input", applyPreset);
  els.imapUsername.addEventListener("input", () => {
    delete els.imapUsername.dataset.auto;
  });
  els.googleClientFile.addEventListener("change", importGoogleClient);
  els.useMicrosoft.addEventListener("click", () => {
    els.provider.value = "microsoft";
    setProviderFields();
  });
  els.accountForm.addEventListener("submit", submitAccount);
  els.search.addEventListener("input", () => {
    state.filters.search = els.search.value;
    renderScope();
  });
  els.clearFilters.addEventListener("click", () => {
    state.filters = {
      type: "all",
      account: "all",
      category: "all",
      search: "",
    };
    els.search.value = "";
    renderScope();
  });
  els.selectAll.addEventListener("change", () => {
    if (els.selectAll.checked) {
      state.selected.clear();
      filteredMessages()
        .slice(0, 500)
        .forEach((message) => state.selected.add(message.id));
      if (filteredMessages().length > 500)
        showToast("Up to 500 messages per action; selected the first 500");
    } else {
      filteredMessages().forEach((message) =>
        state.selected.delete(message.id),
      );
    }
    renderList();
  });
  els.clearSelection.addEventListener("click", () => {
    state.selected.clear();
    renderList();
  });
  els.previewCategoryButton.addEventListener("click", () => {
    if (state.activeId) showCategoryDialog([state.activeId]);
  });
  els.categoryForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const ids = JSON.parse(els.categoryDialog.dataset.ids || "[]");
    performAction(ids, "category", els.categorySelect.value, "category-dialog");
    els.categoryDialog.close();
  });
  els.loadMore.addEventListener("click", async () => {
    els.loadMore.disabled = true;
    try {
      await api("/api/load-more", { method: "POST", body: "{}" });
      showToast("Loading more mail");
      await loadState();
    } catch (error) {
      showToast(error.message);
    } finally {
      els.loadMore.disabled = false;
    }
  });
  // The mail operations module is intentionally a thin client-side extension;
  // it receives the authenticated API and live workbench state without
  // exposing the session token or other credentials globally.
  window.EmailTownMailHost = {
    api,
    uiLog,
    k8,
    holdUnread,
    fetchBinary: async (path, { signal } = {}) => {
      const headers = { "X-Email-Town": "local" };
      if (state.sessionToken)
        headers.Authorization = `Bearer ${state.sessionToken}`;
      const response = await fetch(path, { credentials: "omit", headers, signal });
      if (response.status === 401) handleUnauthorized();
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      return response.blob();
    },
    state,
    loadState,
    showToast,
    showActionToast,
    openMessage,
    accountById,
    renderList,
    renderScope,
    ensureListFocus,
    reconcileMessageState,
    messageDetail,
    filteredMessages,
    render: null,
  };
  setProviderFields();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadState();
  });
  bootstrapSession();
})();
