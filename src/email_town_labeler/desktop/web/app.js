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
    refreshing: false,
    stale: false,
    modelError: "",
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
    poll: null,
    toastTimer: null,
    snapshot: "",
  };
  const text = (value) => String(value ?? "");
  const formatDate = (epoch) => {
    const date = new Date(Number(epoch) || 0);
    return Number.isNaN(date.getTime())
      ? "—"
      : new Intl.DateTimeFormat("zh-CN", {
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
      mailbox: "邮箱标签",
      model: "本机自动分类",
      manual: "本机手动分类",
      pending: "等待分类",
    })[source] || "本机分类";
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
    label.textContent = "已保存 · ";
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "text-button";
    undo.textContent = "撤销";
    undo.addEventListener("click", async () => {
      try {
        await api("/api/undo", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        els.toast.textContent = "已撤销本次更改";
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
      throw new Error(data.error || `请求失败（${response.status}）`);
    return data;
  }
  function handleUnauthorized() {
    state.authLost = true;
    state.sessionToken = "";
    sessionStorage.removeItem("email-town-session");
    if (state.poll) {
      clearInterval(state.poll);
      state.poll = null;
    }
    setSync("error", "需要重新启动");
    els.footerStatus.textContent =
      "会话已过期，请从启动器重新打开 Email Town。";
    els.restart.hidden = false;
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
    document
      .querySelectorAll("[data-type-filter]")
      .forEach((button) =>
        button.classList.toggle(
          "is-active",
          button.dataset.typeFilter === state.filters.type,
        ),
      );
    document
      .querySelectorAll("[data-done-filter]")
      .forEach((button) =>
        button.classList.toggle(
          "is-active",
          button.dataset.doneFilter === state.filters.done,
        ),
      );
  }

  function renderFilters() {
    const focus = saveFocus();
    els.accountFilters.replaceChildren();
    [{ id: "all", name: "全部账户" }, ...state.accounts].forEach((account) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `account-filter${state.filters.account === account.id ? " is-active" : ""}`;
      button.dataset.accountFilter = account.id;
      const name = document.createElement("span");
      name.textContent = text(account.name);
      button.append(name);
      if (account.id !== "all") {
        const status = document.createElement("span");
        status.className = "count";
        status.textContent =
          account.enabled === false
            ? "暂停同步"
            : account.status === "error"
              ? "异常"
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
        manage.setAttribute("aria-label", `管理 ${account.name}`);
        manage.addEventListener("click", () =>
          showAccountManagement(account.id),
        );
        entry.append(button, manage);
        els.accountFilters.append(entry);
      }
    });
    $("accountCount").textContent = state.accounts.length
      ? `${state.accounts.length} 个`
      : "—";
    els.categoryFilters.replaceChildren();
    [{ id: "all", name: "全部分类" }, ...state.categories].forEach(
      (category) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `category-filter${state.filters.category === category.id ? " is-active" : ""}`;
        button.dataset.categoryFilter = category.id;
        button.textContent = text(category.name);
        els.categoryFilters.append(button);
      },
    );
    $("categoryCount").textContent = state.categories.length
      ? `${state.categories.length} 个`
      : "—";
    syncFilterButtons();
    restoreFocus(focus);
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
      ? `仅显示已载入收件箱邮件 · ${loaded} 封${state.limit >= 1000 ? " · 已达每账户 1000 封上限" : ""}`
      : "";
  }

  function renderList() {
    const focus = saveFocus();
    const messages = filteredMessages();
    const hasLoading = state.refreshing && !state.messages.length;
    els.listTitle.textContent =
      state.filters.type === "today"
        ? "今天"
        : state.filters.type === "backlog"
          ? "历史积压"
          : "全部邮件";
    els.listStatus.textContent = state.modelError
      ? `分类模型提示：${text(state.modelError)}`
      : state.stale
        ? "同步失败，当前显示上次已载入的数据。"
        : state.refreshing && state.messages.length
          ? "正在同步最新变化…"
          : state.lastRefresh
            ? `上次同步 ${formatDate(state.lastRefresh)}`
            : "";
    renderMailboxNotice();
    els.selectionToolbar.hidden = !state.messages.length;
    els.selectionCount.textContent = `已选 ${state.selected.size} 封`;
    els.markDone.disabled = els.reopen.disabled = state.selected.size === 0;
    els.selectAll.checked =
      messages.length > 0 && messages.every((m) => state.selected.has(m.id));
    els.messageList.replaceChildren();
    if (hasLoading) {
      const loading = document.createElement("div");
      loading.className = "loading-state";
      const h = document.createElement("h3");
      h.textContent = "正在读取本机账户";
      loading.append(h, document.createTextNode("邮件列表马上显示。"));
      els.messageList.append(loading);
      return;
    }
    if (!messages.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const h = document.createElement("h3");
      h.textContent = state.messages.length
        ? "没有符合条件的邮件"
        : "还没有邮件";
      const p = document.createElement("p");
      p.textContent = state.messages.length
        ? "试试清除筛选或搜索词。"
        : "添加账户后，邮件会从原服务加载到本机工作台。";
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
        `选择 ${text(message.subject || "无主题")}`,
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
      subject.textContent = text(message.subject || "无主题");
      const sender = document.createElement("span");
      sender.className = "row-sender";
      sender.textContent = text(message.sender || "未知发件人");
      title.append(subject, sender);
      const snippet = document.createElement("div");
      snippet.className = "row-preview";
      snippet.textContent = `分类来源：${sourceName(message.source)}`;
      const account = document.createElement("div");
      account.className = "row-account";
      account.textContent = text(
        accountById(message.account_id)?.name || "未知账户",
      );
      body.append(account, title, snippet);
      const meta = document.createElement("div");
      meta.className = "row-meta";
      const date = document.createElement("time");
      date.textContent = formatDate(message.date);
      const category = document.createElement("span");
      category.className = "category-badge";
      category.textContent = text(
        categoryById(message.category)?.name || message.category || "未分类",
      );
      meta.append(date, category);
      if (message.unread) {
        const unread = document.createElement("span");
        unread.className = "unread-badge";
        unread.textContent = "未读";
        meta.append(unread);
      }
      row.append(checkbox, body, meta);
      row.addEventListener("click", () => openMessage(message.id));
      els.messageList.append(row);
    });
    els.selectionToolbar.hidden =
      state.selected.size === 0 && !state.messages.length;
    els.selectionCount.textContent = `已选 ${state.selected.size} 封`;
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
    els.previewSource.textContent = `账户：${text(accountById(message.account_id)?.name || "未知账户")} · 分类来源：${sourceName(message.source)}`;
    els.previewCategory.textContent = text(
      categoryById(message.category)?.name || message.category || "未分类",
    );
    els.previewUnread.hidden = !message.unread;
    els.previewSubject.textContent = text(message.subject || "无主题");
    els.previewByline.textContent = text(message.sender || "未知发件人");
    els.previewDate.textContent = formatDate(message.date);
    els.previewBody.textContent = "正在读取正文…";
    els.previewDone.textContent = message.done ? "重新打开" : "标记完成";
    const setOriginalLink = (value) => {
      const knownUrl = safeOriginalUrl(value);
      els.originalLink.hidden = !knownUrl;
      if (knownUrl) els.originalLink.href = knownUrl;
    };
    setOriginalLink(message.url);
    try {
      const data = await api(`/api/message?id=${encodeURIComponent(id)}`);
      if (state.activeId === id) {
        els.previewBody.textContent = text(data.body || "（没有纯文本正文）");
        setOriginalLink(data.url || message.url);
      }
    } catch (error) {
      if (state.activeId === id)
        els.previewBody.textContent = `无法读取正文：${text(error.message)}`;
    }
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
          if (action === "category") message.category = category;
        }
      });
      state.selected.clear();
      renderCounts();
      renderList();
      if (state.activeId) {
        const current = state.messages.find(
          (message) => message.id === state.activeId,
        );
        if (current) {
          els.previewDone.textContent = current.done ? "重新打开" : "标记完成";
          els.previewCategory.textContent = text(
            categoryById(current.category)?.name ||
              current.category ||
              "未分类",
          );
        }
      }
      if (result.undo) showUndoToast(result.undo);
      else
        showToast(
          action === "done"
            ? "已在本机标记完成"
            : action === "reopen"
              ? "已重新打开"
              : "分类已在本机更新",
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
      item.textContent = `${text(account.name)}：${text(account.error)}`;
      els.jobStatus.append(item);
    });
    jobs.forEach((job) => {
      const item = document.createElement("div");
      item.textContent = `${text(job.message || "账户连接任务")} ${job.status === "error" ? "（失败）" : job.status === "pending" || job.status === "waiting" ? "（等待操作）" : job.status === "complete" ? "（已连接）" : ""}`;
      if (job.user_code) {
        const code = document.createElement("strong");
        code.textContent = ` 验证码：${text(job.user_code)}`;
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
          link.textContent = "打开验证页面";
          item.append(" ", link);
        }
      } catch (_) {
        /* Ignore unsafe job URLs. */
      }
      els.jobStatus.append(item);
    });
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
      setSync("loading", "正在读取");
      renderList();
    }
    try {
      const data = await api("/api/state");
      const next = {
        accounts: Array.isArray(data.accounts) ? data.accounts : [],
        messages: Array.isArray(data.messages) ? data.messages : [],
        categories: Array.isArray(data.categories) ? data.categories : [],
        refreshing: Boolean(data.refreshing),
        lastRefresh: data.last_refresh || null,
        jobs: Array.isArray(data.jobs) ? data.jobs : [],
        demo: Boolean(data.demo),
        limit: Number(data.limit) || 100,
        modelError: data.model_error || "",
      };
      const snapshot = JSON.stringify(next);
      const changed = snapshot !== state.snapshot;
      Object.assign(state, next, { initialized: true, stale: false, snapshot });
      reconcileMessageState();
      if (state.activeId) {
        const active = state.messages.find((m) => m.id === state.activeId);
        els.previewDone.textContent = active.done ? "重新打开" : "标记完成";
        els.previewCategory.textContent =
          categoryById(active.category)?.name || active.category;
      }
      setSync(
        state.accounts.some((account) => account.status === "error")
          ? "error"
          : "ok",
        state.accounts.some((account) => account.status === "error")
          ? "部分账户异常"
          : state.refreshing
            ? "正在同步"
            : "已同步",
      );
      if (changed) {
        renderFilters();
        renderCounts();
        renderList();
        renderJobs();
      }
    } catch (error) {
      if (error.message !== "unauthorized") {
        state.stale = state.initialized;
        setSync("error", "读取失败");
        els.listStatus.textContent = error.message;
        els.restart.hidden = false;
        renderList();
      }
    } finally {
      state.syncing = false;
    }
  }
  async function refresh() {
    if (state.syncing || state.authLost || !state.sessionToken) return;
    state.refreshing = true;
    setSync("loading", "正在刷新");
    renderList();
    try {
      await api("/api/refresh", { method: "POST", body: "{}" });
      showToast("刷新已开始，邮件会在本机更新");
      await loadState();
    } catch (error) {
      showToast(error.message);
      state.refreshing = false;
      renderList();
    }
  }

  function setProviderFields() {
    const provider = els.provider.value;
    els.existingFields.hidden = provider !== "gmail-existing";
    els.imapFields.hidden = provider !== "imap";
    els.microsoftHelp.hidden = provider !== "microsoft";
    els.clientIdFields.hidden = provider !== "microsoft";
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
        ? "开始设备登录"
        : provider === "imap"
          ? "保存并连接"
          : "开始连接";
  }
  async function submitAccount(event) {
    event.preventDefault();
    els.accountError.hidden = true;
    const form = new FormData(els.accountForm);
    const provider = els.provider.value;
    const payload = { provider, name: text(form.get("name")).trim() };
    if (els.accountForm.dataset.reconnectId)
      payload.reconnect_id = els.accountForm.dataset.reconnectId;
    if (!payload.name) {
      els.accountError.textContent = "请输入显示名称。";
      els.accountError.hidden = false;
      return;
    }
    if (provider === "gmail-existing")
      payload.home = text(form.get("home")).trim();
    if (provider === "imap")
      Object.assign(payload, {
        host: text(form.get("host")).trim(),
        port: Number(form.get("port") || 993),
        username: text(form.get("username")).trim(),
        password: text(form.get("password")),
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
      showToast(data.message || "账户连接已开始");
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
        if (!data.session) throw new Error("会话初始化失败");
        state.sessionToken = data.session;
        sessionStorage.setItem("email-town-session", data.session);
        loadState();
      })
      .catch((error) => {
        state.authLost = true;
        setSync("error", "启动连接失败");
        els.listStatus.textContent = `无法启动本机会话：${text(error.message)}`;
        els.footerStatus.textContent = "请从启动器重新打开 Email Town。";
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
  });
  function syncMobileSidebar() {
    els.sidebar.inert =
      window.innerWidth < 768 && !els.sidebar.classList.contains("is-open");
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
  els.refresh.addEventListener("click", refresh);
  els.restart.addEventListener("click", () => window.location.reload());
  function openAccountForm(account = null) {
    els.accountForm.reset();
    els.accountError.hidden = true;
    els.accountForm.dataset.reconnectId = account?.id || "";
    els.provider.disabled = Boolean(account);
    $("accountDialogTitle").textContent = account
      ? "重新授权此邮箱"
      : "添加账户";
    if (account) {
      $("accountName").value = account.name;
      els.provider.value = account.provider;
      const connection = account.connection || {};
      for (const [field, key] of [
        ["imapHost", "host"],
        ["imapPort", "port"],
        ["imapUsername", "username"],
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
      account.error || (account.enabled ? "自动同步已开启" : "自动同步已暂停");
    $("pauseAccountButton").textContent = account.enabled
      ? "暂停同步"
      : "恢复同步";
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
        showToast("每次最多处理 500 封，已选择前 500 封");
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
      showToast("正在载入更多邮件");
      await loadState();
    } catch (error) {
      showToast(error.message);
    } finally {
      els.loadMore.disabled = false;
    }
  });
  setProviderFields();
  state.poll = window.setInterval(() => {
    if (!document.hidden) loadState();
  }, 5000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadState();
  });
  bootstrapSession();
})();
