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
    demoBanner: $("demoBanner"),
    jobStatus: $("jobStatus"),
    loadMoreBar: $("loadMoreBar"),
    loadedSummary: $("loadedSummary"),
    loadMore: $("loadMoreButton"),
    messageList: $("messageList"),
    selectionToolbar: $("selectionToolbar"),
    selectAll: $("selectAll"),
    selectionCount: $("selectionCount"),
    markDone: $("markDoneButton"),
    reopen: $("reopenButton"),
    clearSelection: $("clearSelectionButton"),
    previewPane: $("previewPane"),
    previewEmpty: $("previewEmpty"),
    previewContent: $("previewContent"),
    previewClose: $("previewClose"),
    previewDone: $("previewDoneButton"),
    previewCategoryButton: $("previewCategoryButton"),
    originalLink: $("originalLink"),
    previewSource: $("previewSource"),
    previewCategory: $("previewCategory"),
    previewUnread: $("previewUnread"),
    previewSubject: $("previewSubject"),
    previewByline: $("previewByline"),
    previewDate: $("previewDate"),
    previewBody: $("previewBody"),
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
    modelError: "",
    modelStatus: null,
    lastRefresh: null,
    demo: false,
    filters: {
      type: "all",
      done: "all",
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
  function saveFocus() {
    const el = document.activeElement;
    for (const key of ["focusKey", "accountFilter", "categoryFilter"]) {
      if (el?.dataset[key]) return { key, value: el.dataset[key] };
    }
    return null;
  }
  function restoreFocus(saved) {
    if (!saved) return;
    const el = [
      ...document.querySelectorAll(
        "[data-focus-key], [data-account-filter], [data-category-filter]",
      ),
    ].find((el) => el.dataset[saved.key] === saved.value);
    el?.focus({ preventScroll: true });
  }

  function setSync(kind, label) {
    els.syncIndicator.classList.toggle("is-error", kind === "error");
    els.syncIndicator.classList.toggle("is-loading", kind === "loading");
    els.syncText.textContent = label;
  }
  function showToast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, 4200);
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
      try {
        await api("/api/undo", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        els.toast.textContent = "Change undone";
        await loadState();
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
  function handleUnauthorized() {
    window.EmailTownImages?.clear();
    state.authLost = true;
    state.sessionToken = "";
    sessionStorage.removeItem("email-town-session");
    setSync("error", "Restart required");
    els.footerStatus.textContent =
      "Session expired. Reopen Email Town from the launcher.";
    els.restart.hidden = false;
    state.refreshing = false;
    state.activeId = null;
    els.previewPane.classList.remove("is-open");
    els.previewContent.hidden = true;
    els.previewEmpty.hidden = false;
    renderList();
  }

  function filteredMessages() {
    const query = state.filters.search.trim().toLowerCase();
    const today = localMidnight();
    const tomorrow = localMidnight(1);
    return state.messages.filter((message) => {
      const date = Number(message.date) || 0;
      const typeOk =
        state.filters.type === "all" ||
        (state.filters.type === "today"
          ? date >= today && date < tomorrow
          : date < today && !message.done);
      const doneOk =
        state.filters.done === "all" ||
        (state.filters.done === "done" ? message.done : !message.done);
      const accountOk =
        state.filters.account === "all" ||
        message.account_id === state.filters.account;
      const categoryOk =
        state.filters.category === "all" ||
        message.category === state.filters.category;
      const queryOk =
        !query ||
        `${message.subject || ""} ${message.sender || ""}`
          .toLowerCase()
          .includes(query);
      return typeOk && doneOk && accountOk && categoryOk && queryOk;
    });
  }
  function syncFilterButtons() {
    document.querySelectorAll("[data-type-filter]").forEach((button) => {
      const active = button.dataset.typeFilter === state.filters.type;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll("[data-done-filter]").forEach((button) => {
      const active = button.dataset.doneFilter === state.filters.done;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function renderFilters() {
    const focus = saveFocus();
    els.accountFilters.replaceChildren();
    [{ id: "all", name: "All accounts" }, ...state.accounts].forEach(
      (account) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `account-filter${state.filters.account === account.id ? " is-active" : ""}`;
        button.dataset.accountFilter = account.id;
        button.setAttribute(
          "aria-pressed",
          String(state.filters.account === account.id),
        );
        const name = document.createElement("span");
        name.textContent = text(account.name);
        button.append(name);
        if (account.id !== "all") {
          const status = document.createElement("span");
          status.className = "count";
          status.textContent =
            account.enabled === false
              ? "Paused"
              : account.status === "error"
                ? "Error"
                : "";
          button.append(status);
        }
        if (account.id === "all") els.accountFilters.append(button);
        else {
          const entry = document.createElement("div");
          entry.className = "account-entry";
          const manage = document.createElement("button");
          manage.type = "button";
          manage.className = "icon-button";
          manage.textContent = "⋯";
          manage.dataset.focusKey = `manage-${account.id}`;
          manage.setAttribute("aria-label", `Manage ${account.name}`);
          manage.addEventListener("click", () =>
            showAccountManagement(account.id),
          );
          entry.append(button, manage);
          els.accountFilters.append(entry);
        }
      },
    );
    $("accountCount").textContent = state.accounts.length
      ? String(state.accounts.length)
      : "—";
    els.categoryFilters.replaceChildren();
    const stats = categoryStats();
    [{ id: "all", name: "All categories" }, ...state.categories].forEach(
      (category) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `category-filter${state.filters.category === category.id ? " is-active" : ""}`;
        button.dataset.categoryFilter = category.id;
        button.setAttribute(
          "aria-pressed",
          String(state.filters.category === category.id),
        );
        // Per category, over the loaded messages: how many, how many done, how many the classifier
        // put here that you moved elsewhere ("wrong"; mail you have not checked is not counted).
        const { total, done, wrong } = stats.get(category.id) || { total: 0, done: 0, wrong: 0 };
        const label = document.createElement("span");
        label.className = "category-label";
        const name = document.createElement("span");
        name.textContent = text(category.name);
        label.append(name);
        if (total || wrong) {
          const line = document.createElement("span");
          line.className = "category-stats";
          line.textContent = `${done} done`;
          if (wrong || category.id === "all") {
            const bad = document.createElement("span");
            bad.className = wrong ? "category-wrong" : "";
            bad.textContent = ` · ${wrong} wrong`;
            line.append(bad);
          }
          label.append(line);
        }
        const count = document.createElement("span");
        count.className = "count";
        count.textContent = String(total);
        button.append(label, count);
        const summary = `${text(category.name)}: ${total} loaded, ${done} done, ${wrong} wrong`;
        button.setAttribute("aria-label", summary);
        button.title = `${summary}. Wrong: classified here, then moved by you to another category.`;
        els.categoryFilters.append(button);
      },
    );
    $("categoryCount").textContent = state.categories.length
      ? String(state.categories.length)
      : "—";
    syncFilterButtons();
    restoreFocus(focus);
    if (window.EmailTownMailHost?.render) window.EmailTownMailHost.render();
  }
  // {category id → {total, done, wrong}} over the loaded messages, plus "all" for the totals.
  function categoryStats() {
    const stats = new Map();
    const bump = (id, key) => {
      const entry = stats.get(id) || { total: 0, done: 0, wrong: 0 };
      entry[key] += 1;
      stats.set(id, entry);
    };
    for (const message of state.messages) {
      for (const id of [message.category || "unknown", "all"]) {
        bump(id, "total");
        if (message.done) bump(id, "done");
      }
      if (message.auto_category && message.auto_category !== message.category)
        for (const id of [message.auto_category, "all"]) bump(id, "wrong");
    }
    return stats;
  }
  function renderCounts() {
    const today = localMidnight();
    const tomorrow = localMidnight(1);
    $("countAll").textContent = state.messages.length || "0";
    $("countToday").textContent = state.messages.filter(
      (message) =>
        Number(message.date) >= today && Number(message.date) < tomorrow,
    ).length;
    $("countBacklog").textContent = state.messages.filter(
      (message) => Number(message.date) < today && !message.done,
    ).length;
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
    els.listTitle.textContent =
      state.filters.type === "today"
        ? "Today"
        : state.filters.type === "backlog"
          ? "Earlier"
          : {
              inbox: "Inbox",
              sent: "Sent",
              archive: "Archive",
              trash: "Trash",
            }[state.folder] || "Inbox";
    const folderName =
      { inbox: "Inbox", sent: "Sent", archive: "Archive", trash: "Trash" }[
        state.folder
      ] || "Inbox";
    const categoryName =
      state.filters.category === "all"
        ? ""
        : categoryById(state.filters.category)?.name;
    if (categoryName) els.listTitle.textContent = categoryName;
    $("listContext").textContent = [
      state.filters.account === "all"
        ? "All accounts"
        : accountById(state.filters.account)?.name,
      categoryName || state.filters.type !== "all" ? folderName : "",
    ]
      .filter(Boolean)
      .join(" / ");
    $("syncIndicator").title = state.lastRefresh
      ? `Last sync ${formatDate(state.lastRefresh)}`
      : "";
    els.listStatus.textContent = state.modelError
      ? `Classifier: ${text(state.modelError)}`
      : state.modelStatus?.state === "downloading"
        ? `Downloading the on-device classifier (${text(state.modelStatus.size)}, once). Mail is readable meanwhile.`
        : state.stale
        ? "Sync failed; showing the last loaded data."
        : state.refreshing && state.messages.length
          ? "Syncing latest changes…"
          : "";
    renderMailboxNotice();
    els.selectionToolbar.hidden = !state.messages.length;
    els.selectionCount.textContent = state.selected.size
      ? `${state.selected.size} selected`
      : `${messages.length} messages`;
    $("clearSelectionButton").hidden = !state.selected.size;
    els.selectAll.indeterminate =
      messages.some((m) => state.selected.has(m.id)) &&
      !messages.every((m) => state.selected.has(m.id));
    els.markDone.disabled = els.reopen.disabled = state.selected.size === 0;
    els.selectAll.checked =
      messages.length > 0 && messages.every((m) => state.selected.has(m.id));
    els.messageList.replaceChildren();
    if (hasLoading) {
      const loading = document.createElement("div");
      loading.className = "loading-state";
      const h = document.createElement("h3");
      h.textContent = "Reading local accounts";
      loading.append(
        h,
        document.createTextNode("Your mail list will appear shortly."),
      );
      els.messageList.append(loading);
      return;
    }
    if (!messages.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const h = document.createElement("h3");
      h.textContent = state.messages.length
        ? "No messages match these filters"
        : "No mail yet";
      const p = document.createElement("p");
      p.textContent = state.messages.length
        ? "Try clearing a filter or search term."
        : "Add an account to load mail into this workbench.";
      empty.append(h, p);
      els.messageList.append(empty);
      return;
    }
    messages.forEach((message) => {
      const row = document.createElement("article");
      row.className = `message-row${message.unread ? " is-unread" : ""}${message.id === state.activeId ? " is-focused" : ""}${state.selected.has(message.id) ? " is-selected" : ""}`;
      row.dataset.messageId = message.id;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "row-check";
      checkbox.dataset.focusKey = `select-${message.id}`;
      checkbox.checked = state.selected.has(message.id);
      checkbox.setAttribute(
        "aria-label",
        `Select ${text(message.subject || "No subject")}`,
      );
      checkbox.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleSelected(message.id, checkbox.checked);
      });
      const body = document.createElement("div");
      body.className = "row-content";
      const title = document.createElement("div");
      title.className = "row-title";
      const subject = document.createElement("button");
      subject.type = "button";
      subject.dataset.focusKey = `open-${message.id}`;
      subject.className = `row-open row-subject${message.done ? " row-status" : ""}`;
      subject.textContent = text(message.subject || "No subject");
      const sender = document.createElement("span");
      sender.className = "row-sender";
      sender.textContent = text(message.sender || "Unknown sender");

      const snippet = document.createElement("div");
      snippet.className = "row-preview sr-only";
      snippet.textContent = `Classification: ${sourceName(message.source)}`;
      const account = document.createElement("div");
      account.className = "row-account";
      account.textContent = text(
        accountById(message.account_id)?.name || "Unknown account",
      );

      const meta = document.createElement("div");
      meta.className = "row-meta";
      const date = document.createElement("time");
      date.title = formatDate(message.date);
      date.textContent = new Intl.DateTimeFormat(
        "en-US",
        Number(message.date) >= localMidnight()
          ? { hour: "numeric", minute: "2-digit" }
          : { month: "short", day: "numeric" },
      ).format(new Date(Number(message.date) || 0));
      title.append(sender, date);
      const category = document.createElement("span");
      category.className = "category-badge";
      category.dataset.category = message.category || "unknown";
      category.textContent = text(
        categoryById(message.category)?.name ||
          message.category ||
          "Unclassified",
      );
      category.title = `Classification: ${sourceName(message.source)}`;
      meta.append(category, account);
      if (message.starred) {
        const star = document.createElement("span");
        star.className = "unread-badge";
        star.textContent = "★ Starred";
        meta.append(star);
      }
      if (message.unread) {
        // Unread reads as weight and a dot, as in Gmail; the word stays for screen readers.
        const unread = document.createElement("span");
        unread.className = "sr-only";
        unread.textContent = "Unread";
        meta.append(unread);
      }
      body.append(title, subject, meta, snippet);
      row.append(checkbox, body);
      row.addEventListener("click", () => openMessage(message.id));
      els.messageList.append(row);
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

  function openMessage(id) {
    state.activeId = id;
    renderList();
    els.previewPane.classList.add("is-open");
    els.previewEmpty.hidden = true;
    els.previewContent.hidden = false;
    loadPreview(id);
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
  async function loadPreview(id) {
    const message = state.messages.find((item) => item.id === id);
    if (!message) return;
    els.previewSource.textContent = text(
      accountById(message.account_id)?.name || "Unknown account",
    );
    els.previewSource.title = `Classification: ${sourceName(message.source)}`;
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
    els.previewBody.textContent = "Reading message…";
    updatePreviewPosition();
    els.previewDone.textContent = message.done ? "Reopen" : "Mark done";
    const setOriginalLink = (value) => {
      const knownUrl = safeOriginalUrl(value);
      els.originalLink.hidden = !knownUrl;
      if (knownUrl) els.originalLink.href = knownUrl;
    };
    setOriginalLink(message.url);
    try {
      const data = await api(`/api/message?id=${encodeURIComponent(id)}`);
      if (state.activeId === id) {
        const body = text(data.body || "(No plain-text body)");
        if (window.EmailTownReader) window.EmailTownReader.renderBody(els.previewBody, body);
        else els.previewBody.textContent = body;
        setOriginalLink(data.url || message.url);
      }
    } catch (error) {
      if (state.activeId === id)
        els.previewBody.textContent = `Unable to read message: ${text(error.message)}`;
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
    if (next) openMessage(next);
  }
  function toggleSelected(id, checked) {
    if (checked) state.selected.add(id);
    else state.selected.delete(id);
    renderList();
  }

  async function performAction(ids, action, category) {
    if (!ids.length) return;
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
          if (action === "done") message.done = true;
          if (action === "reopen") message.done = false;
          if (action === "category") {
            // As the next snapshot will say: keep what classification chose, for the "wrong" count.
            if (["model", "mailbox"].includes(message.source))
              message.auto_category = message.category;
            message.category = category;
            message.source = "manual";
          }
        }
      });
      state.selected.clear();
      renderFilters(); // the sidebar's per-category done / wrong counts
      renderCounts();
      renderList();
      if (state.activeId) {
        const current = state.messages.find(
          (message) => message.id === state.activeId,
        );
        if (current) {
          els.previewDone.textContent = current.done ? "Reopen" : "Mark done";
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
        showToast(
          action === "done"
            ? "Marked done locally"
            : action === "reopen"
              ? "Reopened"
              : "Category updated locally",
        );
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
    els.footerStatus.textContent = busy.length
      ? busy.join(" · ")
      : "Classified on your device";
    els.footerStatus.classList.toggle("is-working", busy.length > 0);
  }
  function reconcileMessageState() {
    const ids = new Set(state.messages.map((message) => message.id));
    state.selected.forEach((id) => {
      if (!ids.has(id)) state.selected.delete(id);
    });
    if (state.activeId && !ids.has(state.activeId)) {
      state.activeId = null;
      els.previewPane.classList.remove("is-open");
      els.previewEmpty.hidden = false;
      els.previewContent.hidden = true;
    }
  }

  async function loadState() {
    if (state.syncing || state.authLost || !state.sessionToken) return;
    state.syncing = true;
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
        setSync("error", "Read failed");
        els.listStatus.textContent = error.message;
        els.restart.hidden = false;
        renderList();
      }
    } finally {
      state.syncing = false;
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
    if (!data || Number(data.seq) <= state.seq) return false;
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
    const snapshot = JSON.stringify(next);
    const changed = snapshot !== state.snapshot;
    Object.assign(state, next, { initialized: true, stale: false, snapshot });
    reconcileMessageState();
    renderOutbox();
    if (state.activeId) {
      const active = state.messages.find((m) => m.id === state.activeId);
      els.previewUnread.hidden = !active.unread;
      els.previewDone.textContent = active.done ? "Reopen" : "Mark done";
      els.previewCategory.textContent =
        categoryById(active.category)?.name ||
        active.category ||
        "Unclassified";
      els.previewCategory.dataset.category = active.category || "unknown";
    }
    setSync(
      state.accounts.some((account) => account.status === "error")
        ? "error"
        : "ok",
      state.accounts.some((account) => account.status === "error")
        ? "Some accounts have errors"
        : state.refreshing
          ? "Syncing"
          : "Synced",
    );
    if (changed) {
      renderFilters();
      renderCounts();
      renderList();
      renderJobs();
    }
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
    if (type) {
      state.filters.type = type.dataset.typeFilter;
      syncFilterButtons();
      renderList();
    }
    const done = event.target.closest("[data-done-filter]");
    if (done) {
      state.filters.done = done.dataset.doneFilter;
      syncFilterButtons();
      renderList();
    }
    const account = event.target.closest("[data-account-filter]");
    if (account) {
      state.filters.account = account.dataset.accountFilter;
      renderFilters();
      renderList();
    }
    const category = event.target.closest("[data-category-filter]");
    if (category) {
      state.filters.category = category.dataset.categoryFilter;
      renderFilters();
      renderList();
    }
    if (type || done || account || category) {
      if (window.innerWidth < 1200) els.previewPane.classList.remove("is-open");
      if (window.innerWidth < 768) closeSidebar();
    }
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
  els.restart.addEventListener("click", () => window.location.reload());
  async function openAccountForm(account = null) {
    await loadSetup();
    els.accountForm.reset();
    delete els.imapUsername.dataset.auto;
    els.imapPresetHelp.hidden = true;
    els.useMicrosoft.hidden = true;
    els.imapServerDetails.open = true;
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
    setProviderFields();
    els.accountDialog.showModal();
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
    renderList();
  });
  els.clearFilters.addEventListener("click", () => {
    state.filters = {
      type: "all",
      done: "all",
      account: "all",
      category: "all",
      search: "",
    };
    els.search.value = "";
    renderFilters();
    renderList();
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
  els.markDone.addEventListener("click", () =>
    performAction([...state.selected], "done"),
  );
  els.reopen.addEventListener("click", () =>
    performAction([...state.selected], "reopen"),
  );
  els.previewDone.addEventListener("click", () => {
    const message = state.messages.find((item) => item.id === state.activeId);
    if (message) performAction([message.id], message.done ? "reopen" : "done");
  });
  els.previewCategoryButton.addEventListener("click", () => {
    if (state.activeId) showCategoryDialog([state.activeId]);
  });
  els.categoryForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const ids = JSON.parse(els.categoryDialog.dataset.ids || "[]");
    performAction(ids, "category", els.categorySelect.value);
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
    openMessage,
    accountById,
    renderList,
    reconcileMessageState,
    filteredMessages,
    render: null,
  };
  setProviderFields();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadState();
  });
  bootstrapSession();
})();
