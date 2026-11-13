(() => {
  "use strict";
  const host = window.EmailTownMailHost;
  if (!host) return;
  const $ = (id) => document.getElementById(id);
  const text = (value) => String(value ?? "");
  const els = {
    compose: $("composeDialog"),
    form: $("composeForm"),
    account: $("composeAccount"),
    to: $("composeTo"),
    cc: $("composeCc"),
    bcc: $("composeBcc"),
    subject: $("composeSubject"),
    body: $("composeBody"),
    files: $("composeAttachments"),
    attach: $("attachmentSummary"),
    capability: $("composeCapabilityNote"),
    error: $("composeError"),
    send: $("sendButton"),
    save: $("saveDraftButton"),
    drafts: $("draftsDialog"),
    draftList: $("draftList"),
    toolbar: $("mailSelectionToolbar"),
    count: $("mailSelectionCount"),
  };
  const pendingKey = "email-town-pending-send";
  const local = {
    attachments: [],
    draftId: null,
    replyTo: null,
    detail: null,
    detailId: null,
    editorId: crypto.randomUUID(),
    editVersion: 0,
    dirty: false,
    busy: false,
    readingFiles: false,
    pending: null,
    accountsKey: "",
    folderBusy: false,
  };
  const accountFor = (id) =>
    host.state.accounts.find((account) => account.id === id);
  const ownAddress = (account) => account?.address || "";
  const unique = (items) => [
    ...new Map(items.map((value) => [value.toLowerCase(), value])).values(),
  ];
  const selectedIds = () => [...host.state.selected].slice(0, 500);
  function setError(message) {
    els.error.textContent = text(message);
    els.error.hidden = !message;
  }
  function notePending(pending) {
    if (pending) sessionStorage.setItem(pendingKey, JSON.stringify(pending));
    else sessionStorage.removeItem(pendingKey);
    local.pending = pending;
    updateControls();
  }
  try {
    local.pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
  } catch (_) {
    /* no prior receipt */
  }

  const pendingBar = document.createElement("div");
  pendingBar.className = "mail-pending-notice";
  const pendingText = document.createElement("p");
  pendingText.textContent =
    "A send is awaiting confirmation. Check its result before sending again.";
  const checkSend = document.createElement("button");
  checkSend.type = "button";
  checkSend.className = "button button-quiet button-small";
  checkSend.textContent = "Check send result";
  const resetSend = document.createElement("button");
  resetSend.type = "button";
  resetSend.className = "button button-quiet button-small";
  resetSend.textContent = "I checked Sent — start a new send";
  pendingBar.append(pendingText, checkSend, resetSend);
  els.error.after(pendingBar);

  function payload() {
    return {
      account_id: els.account.value,
      to: els.to.value.trim(),
      cc: els.cc.value.trim(),
      bcc: els.bcc.value.trim(),
      subject: els.subject.value,
      body: els.body.value,
      reply_to_id: local.replyTo || undefined,
      attachments: local.attachments.map(({ name, type, data }) => ({
        name,
        type,
        data,
      })),
    };
  }
  function attachmentBytes(item) {
    return (
      item.size ??
      Math.max(
        0,
        Math.floor((item.data.length * 3) / 4) -
          (item.data.match(/=*$/)?.[0].length || 0),
      )
    );
  }
  function attachmentError() {
    const max =
      accountFor(els.account.value)?.capabilities?.max_attachment_bytes || 0;
    if (local.attachments.length > 20) return "Attach at most 20 files.";
    if (
      local.attachments.reduce((sum, item) => sum + attachmentBytes(item), 0) >
      max
    )
      return `Attachments exceed this account's ${Math.round(max / 1024 / 1024)} MB limit.`;
    return "";
  }
  function updateControls() {
    const account = accountFor(els.account.value);
    const cap = account?.capabilities || {};
    const locked = local.busy || local.readingFiles;
    const canSend = account?.enabled && cap.send === true;
    for (const field of [
      els.to,
      els.cc,
      els.bcc,
      els.subject,
      els.body,
      els.files,
    ])
      field.disabled = locked;
    els.account.disabled = locked || Boolean(local.replyTo);
    $("addAttachmentButton").disabled = locked;
    els.send.disabled =
      locked ||
      !canSend ||
      Boolean(local.pending) ||
      Boolean(attachmentError());
    els.save.disabled = locked || !account;
    pendingBar.hidden = !local.pending;
    checkSend.disabled = locked;
    resetSend.disabled = locked;
    els.capability.textContent = canSend
      ? `From ${ownAddress(account) || account.name} · up to ${Math.round(cap.max_attachment_bytes / 1024 / 1024)} MB of attachments.`
      : `Sending unavailable: ${cap.reason || "connect an account with sending permission"}`;
    const active = host.state.messages.find(
      (m) => m.id === host.state.activeId,
    );
    document.querySelectorAll("[data-single-action]").forEach((button) => {
      if (["read", "unread"].includes(button.dataset.singleAction)) {
        button.dataset.singleAction = active?.unread ? "read" : "unread";
        button.textContent = active?.unread ? "Mark read" : "Mark unread";
      }
      if (["star", "unstar"].includes(button.dataset.singleAction)) {
        button.dataset.singleAction = active?.starred ? "unstar" : "star";
        button.textContent = active?.starred ? "Unstar" : "Star";
      }
      button.hidden =
        (button.dataset.singleAction === "restore" &&
          !["trash", "archive"].includes(host.state.folder)) ||
        (button.dataset.singleAction === "trash" &&
          host.state.folder === "trash");
      button.disabled =
        locked ||
        !host.state.activeId ||
        !allowed(button.dataset.singleAction, [host.state.activeId]);
    });
  }
  function renderAccounts() {
    const key = JSON.stringify(
      host.state.accounts.map((a) => [
        a.id,
        a.name,
        a.address,
        a.enabled,
        a.capabilities,
      ]),
    );
    if (key !== local.accountsKey) {
      const current = els.account.value;
      els.account.replaceChildren();
      host.state.accounts.forEach((a) => {
        const option = document.createElement("option");
        option.value = a.id;
        option.textContent = `${a.name}${a.address && a.address !== a.name ? ` · ${a.address}` : ""}`;
        els.account.append(option);
      });
      if (accountFor(current)) els.account.value = current;
      local.accountsKey = key;
    }
    updateControls();
  }
  function renderAttachments() {
    els.attach.replaceChildren();
    if (!local.attachments.length) {
      els.attach.textContent = "No attachments";
      return;
    }
    local.attachments.forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "attachment-item";
      const label = document.createElement("span");
      label.textContent = `${item.name} (${Math.ceil(attachmentBytes(item) / 1024)} KB)`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-button";
      remove.textContent = "Remove";
      remove.disabled = local.busy || local.readingFiles;
      remove.onclick = () => {
        if (local.busy) return;
        local.attachments.splice(index, 1);
        local.dirty = true;
        local.editVersion += 1;
        renderAttachments();
        updateControls();
      };
      row.append(label, remove);
      els.attach.append(row);
    });
  }
  function resetCompose() {
    local.editorId = crypto.randomUUID();
    local.editVersion = 0;
    els.form.reset();
    local.attachments = [];
    local.draftId = null;
    local.replyTo = null;
    local.dirty = false;
    setError("");
    renderAccounts();
    renderAttachments();
    updateControls();
  }
  function closeCompose(event) {
    if (event) event.preventDefault();
    if (local.busy || local.readingFiles) return;
    if (local.dirty && !confirm("Close without saving your latest changes?"))
      return;
    local.dirty = false;
    els.compose.close();
  }
  function openCompose(mode = "compose", detail = null) {
    if (local.busy) return;
    resetCompose();
    if (mode !== "compose" && detail) {
      const headers = detail.headers || {};
      const owner = ownAddress(accountFor(detail.account_id)).toLowerCase();
      const recipients = detail.recipients || {};
      const from = recipients["reply-to"]?.length
        ? recipients["reply-to"]
        : recipients.from || [];
      const to = recipients.to || [];
      const cc = recipients.cc || [];
      els.account.value = detail.account_id;
      if (mode === "forward") {
        els.to.value = "";
        els.cc.value = "";
        els.subject.value = /^Fwd:/i.test(detail.subject)
          ? detail.subject
          : `Fwd: ${detail.subject}`;
        els.body.value = `\n\n---------- Forwarded message ----------\nFrom: ${headers.from || detail.sender}\nTo: ${headers.to || ""}\nDate: ${new Date(detail.date).toLocaleString()}\nSubject: ${detail.subject}\n\n${text(detail.body)}`;
        setError(
          "Original attachments are not included. Download and add the files you want to forward.",
        );
      } else {
        const reply = from.every((a) => a.toLowerCase() === owner) ? to : from;
        const chosen = unique(
          (mode === "reply" ? reply : [...reply, ...to]).filter(
            (a) => a.toLowerCase() !== owner,
          ),
        );
        els.to.value = chosen.join(", ");
        els.cc.value =
          mode === "reply-all"
            ? unique(cc)
                .filter(
                  (a) =>
                    a.toLowerCase() !== owner &&
                    !chosen.some((b) => a.toLowerCase() === b.toLowerCase()),
                )
                .join(", ")
            : "";
        els.subject.value = /^Re:/i.test(detail.subject)
          ? detail.subject
          : `Re: ${detail.subject}`;
        els.body.value = `\n\nOn ${new Date(detail.date).toLocaleString()}, ${headers.from || detail.sender} wrote:\n> ${text(detail.body).split("\n").join("\n> ")}`;
        local.replyTo = detail.id;
      }
      local.dirty = true;
      local.editVersion += 1;
    } else if (
      host.state.filters.account !== "all" &&
      accountFor(host.state.filters.account)
    ) {
      els.account.value = host.state.filters.account;
    }
    updateControls();
    els.compose.showModal();
    (mode === "reply" || mode === "reply-all" ? els.body : els.to).focus();
  }
  async function persistDraft() {
    const result = await host.api("/api/draft", {
      method: "POST",
      body: JSON.stringify({ id: local.draftId || undefined, ...payload() }),
    });
    local.draftId = result.id;
    local.dirty = false;
    return result.id;
  }
  async function saveDraft() {
    if (local.busy || local.readingFiles) return;
    local.busy = true;
    updateControls();
    try {
      await persistDraft();
      setError("");
      host.showToast("Draft saved on this device");
    } catch (error) {
      setError(error.message);
    } finally {
      local.busy = false;
      updateControls();
    }
  }
  function jobMessage(job) {
    return [
      job.message,
      ...(job.failed || []).map(
        (item) =>
          `${item.error}${item.id?.includes("@") ? ` (${item.id})` : ""}`,
      ),
    ]
      .filter(Boolean)
      .join("\n");
  }
  async function pollJob(id) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const result = await host.api(
        `/api/mail-job?id=${encodeURIComponent(id)}`,
      );
      if (result.status !== "pending") return result;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    return {
      status: "pending",
      message: "Still working. Check the result again before retrying.",
    };
  }
  async function applySendResult(job) {
    if (job.status === "complete") {
      const submittedEditor =
        local.pending?.editor_id === local.editorId &&
        local.pending?.edit_version === local.editVersion;
      notePending(null);
      if (submittedEditor) {
        local.dirty = false;
        els.compose.close();
      } else
        setError(
          "The previous message was sent. Your current edits are preserved.",
        );
      host.showToast(job.message || "Accepted for delivery");
      await host.loadState();
    } else {
      setError(
        `${jobMessage(job)}\nThe local draft is retained. Check Sent before any new send.`,
      );
    }
  }
  async function send(event) {
    event.preventDefault();
    if (local.busy || local.readingFiles || local.pending || els.send.disabled)
      return;
    const data = payload();
    if (!data.to && !data.cc && !data.bcc)
      return setError("Add at least one recipient.");
    const account = accountFor(data.account_id);
    if (
      !confirm(
        `Send from ${ownAddress(account) || account.name}?\nTo: ${data.to || "—"}\nCc: ${data.cc || "—"}\nBcc: ${data.bcc || "—"}\nAttachments: ${local.attachments.length}`,
      )
    )
      return;
    local.busy = true;
    updateControls();
    setError("");
    let saved = false;
    try {
      await persistDraft();
      saved = true;
      const requestId = crypto.randomUUID();
      // Persist only IDs. Attachment content stays in the private on-device draft, not browser storage.
      notePending({
        request_id: requestId,
        draft_id: local.draftId,
        editor_id: local.editorId,
        edit_version: local.editVersion,
      });
      const result = await host.api("/api/send", {
        method: "POST",
        body: JSON.stringify({
          ...data,
          request_id: requestId,
          draft_id: local.draftId,
        }),
      });
      await applySendResult(await pollJob(result.job_id));
    } catch (error) {
      setError(
        saved
          ? `${error.message}\nDraft saved. Check the send result and Sent before retrying.`
          : `${error.message}\nNothing was submitted for sending; keep this window open to preserve your edits.`,
      );
    } finally {
      local.busy = false;
      updateControls();
    }
  }
  checkSend.onclick = async () => {
    if (!local.pending || local.busy) return;
    local.busy = true;
    updateControls();
    try {
      await applySendResult(await pollJob(local.pending.request_id));
    } catch (error) {
      setError(
        `${error.message}\nIf no receipt exists, check Sent before starting a new send.`,
      );
    } finally {
      local.busy = false;
      updateControls();
    }
  };
  resetSend.onclick = () => {
    if (local.busy) return;
    if (
      confirm(
        "Only continue after checking Sent and the operation result. A previous send may already have reached some recipients. Start a new send?",
      )
    ) {
      notePending(null);
      setError("");
    }
  };
  async function loadDraft(id) {
    try {
      const draft = await host.api(`/api/draft?id=${encodeURIComponent(id)}`);
      els.drafts.close();
      resetCompose();
      local.draftId = draft.id;
      els.account.value = draft.account_id;
      for (const field of ["to", "cc", "bcc", "subject", "body"])
        els[field].value = text(draft[field]);
      local.replyTo = draft.reply_to_id || null;
      local.attachments = draft.attachments || [];
      renderAttachments();
      updateControls();
      els.compose.showModal();
    } catch (error) {
      host.showToast(error.message);
    }
  }
  async function loadDraftList() {
    els.draftList.replaceChildren();
    try {
      const { drafts } = await host.api("/api/drafts");
      if (!drafts.length) els.draftList.textContent = "No saved drafts.";
      drafts.forEach((draft) => {
        const row = document.createElement("div");
        row.className = "draft-item";
        const open = document.createElement("button");
        open.className = "text-button";
        open.type = "button";
        open.textContent = `${accountFor(draft.account_id)?.name || "Account"} · ${draft.subject || "(No subject)"}`;
        open.onclick = () => loadDraft(draft.id);
        const date = document.createElement("time");
        date.textContent = new Date(draft.updated).toLocaleString();
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "text-button danger-text";
        remove.textContent = "Delete";
        remove.onclick = async () => {
          if (!confirm("Delete this draft from this device?")) return;
          try {
            await host.api("/api/draft-delete", {
              method: "POST",
              body: JSON.stringify({ id: draft.id }),
            });
            await loadDraftList();
          } catch (error) {
            host.showToast(error.message);
          }
        };
        row.append(open, date, remove);
        els.draftList.append(row);
      });
    } catch (error) {
      els.draftList.textContent = error.message;
    }
  }
  function allowed(action, ids) {
    if (
      action === "restore" &&
      !["trash", "archive"].includes(host.state.folder)
    )
      return false;
    if (action === "trash" && host.state.folder === "trash") return false;
    return (
      ids.length &&
      ids.every((id) => {
        const item = host.state.messages.find((m) => m.id === id);
        const account = accountFor(item?.account_id);
        return account?.enabled && account.capabilities?.write === true;
      })
    );
  }
  function renderSelection() {
    const ids = selectedIds();
    els.toolbar.hidden = !ids.length;
    els.count.textContent = `${ids.length} selected · loaded messages only`;
    els.toolbar.querySelectorAll("[data-mail-action]").forEach((button) => {
      button.hidden =
        (button.dataset.mailAction === "restore" &&
          !["trash", "archive"].includes(host.state.folder)) ||
        (button.dataset.mailAction === "trash" &&
          host.state.folder === "trash");
      button.disabled = local.busy || !allowed(button.dataset.mailAction, ids);
    });
  }
  const LEAVES_FOLDER = ["archive", "trash", "restore"];
  function nextAfter(id, leaving) {
    const ids = (host.filteredMessages?.() || host.state.messages).map((m) => m.id);
    const at = ids.indexOf(id);
    const stays = (other) => !leaving.includes(other);
    return ids.slice(at + 1).find(stays) || ids.slice(0, at).reverse().find(stays) || null;
  }
  // Bulk actions go to the outbox: the messages leave the list (or take their new flag) at once, the
  // provider writes run in the background one action after another, and only failures interrupt.
  async function mailAction(action, chosen = selectedIds(), via = "toolbar") {
    host.uiLog?.("mail-action", {
      action,
      via,
      ids: chosen.slice(0, 20).map(host.k8),
      count: chosen.length,
      active: host.k8(host.state.activeId),
      includes_active: chosen.includes(host.state.activeId),
      allowed: allowed(action, chosen),
    });
    if (!allowed(action, chosen)) return;
    // An explicit read/unread on the open message settles it: the delayed open-to-read must not undo it.
    if (["read", "unread"].includes(action) && chosen.includes(host.state.activeId)) {
      local.explicitFlag = host.state.activeId;
      host.holdUnread(host.state.activeId, null);
    }
    const names = unique(
      chosen.map(
        (id) =>
          accountFor(host.state.messages.find((m) => m.id === id)?.account_id)
            ?.name || "Account",
      ),
    );
    if (
      action === "trash" &&
      !confirm(
        `Move ${chosen.length} selected messages to Trash?\nAccounts: ${names.join(", ")}\nThis changes the original mailboxes and syncs to your other mail apps.`,
      )
    )
      return;
    // Gmail's auto-advance: archiving or trashing the open message opens the next (older) one in a
    // wide layout; a narrow one returns to the list.
    const active = host.state.activeId;
    const advance =
      LEAVES_FOLDER.includes(action) &&
      chosen.includes(active) &&
      window.innerWidth >= 1200
        ? nextAfter(active, chosen)
        : null;
    // As in Gmail, the selection outlives actions that leave messages in place (read, unread, star);
    // messages that leave the folder drop out of it, and out of the list, before the server answers.
    const leaving = LEAVES_FOLDER.includes(action) ? new Set(chosen) : null;
    const focusIndex = Math.max(
      0,
      (host.filteredMessages?.() || host.state.messages).findIndex((message) => leaving?.has(message.id)),
    );
    if (leaving) {
      chosen.forEach((id) => host.state.selected.delete(id));
      host.state.messages = host.state.messages.filter((m) => !leaving.has(m.id));
      host.reconcileMessageState(); // closes the reading pane if its message left
      host.renderScope();
    }
    if (advance) host.openMessage(advance, { via: "auto-advance" });
    if (leaving) host.ensureListFocus(focusIndex);
    let job;
    local.posting = true; // keyboard actions wait for this, so the server receives them in key order
    try {
      job = await host.api("/api/mail-action", {
        method: "POST",
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          action,
          ids: chosen,
        }),
      });
    } catch (error) {
      host.showToast(`${error.message} Nothing was changed.`);
      await host.loadState(); // puts back what was removed locally
      return;
    } finally {
      local.posting = false;
    }
    local.detailId = null;
    await host.loadState();
    pollJob(job.job_id).then((result) => {
      if (result.status === "complete") return;
      host.showToast(jobMessage(result));
      if (result.status !== "pending") alert(jobMessage(result));
    });
  }
  // A folder chosen while another switch (or a mail action) is still running is not dropped: the
  // last one chosen is opened when the current work finishes.
  async function changeFolder(folder) {
    if (local.folderBusy || local.busy) {
      local.pendingFolder = folder;
      setTimeout(flushFolder, 100);
      return;
    }
    local.pendingFolder = null;
    local.folderBusy = true;
    try {
      await host.api("/api/folder", {
        method: "POST",
        body: JSON.stringify({ folder }),
      });
      host.state.selected.clear();
      host.state.activeId = null;
      clearDetail();
      $("previewPane").classList.remove("is-open");
      $("previewEmpty").hidden = false;
      $("previewContent").hidden = true;
      host.showToast(`Loading ${folder}…`);
      await host.loadState();
    } catch (error) {
      host.showToast(error.message);
    } finally {
      local.folderBusy = false;
    }
    flushFolder();
  }
  function flushFolder() {
    const next = local.pendingFolder;
    if (!next) return;
    if (local.folderBusy || local.busy) {
      setTimeout(flushFolder, 100);
      return;
    }
    local.pendingFolder = null;
    if (next !== host.state.folder) changeFolder(next);
  }
  function clearDetail() {
    window.EmailTownImages?.clear();
    local.detail = null;
    document
      .querySelectorAll(
        ".preview-attachments, .preview-headers, [data-compose-mode]",
      )
      .forEach((node) => node.remove());
  }
  // As in Gmail's reading pane: a message you stay on is read. Passing over it with j/k is not reading
  // it, so the read is written only after READ_AFTER_MS on the same message. Once per open: a detail
  // reload after another action must not read it again, and an explicit Mark read/unread on the open
  // message settles it for this open; only opening a different message and coming back starts over.
  const READ_AFTER_MS = 1500;
  async function markReadOnOpen(id) {
    const log = (decision, extra = {}) =>
      host.uiLog?.("auto-read", { id: host.k8(id), decision, ...extra });
    if (local.autoRead === id) return log("skip:already-handled-this-open");
    local.autoRead = id;
    const opened = local.opened;
    await new Promise((resolve) => setTimeout(resolve, READ_AFTER_MS));
    if (host.state.activeId !== id || local.opened !== opened) return log("skip:left-within-1.5s");
    if (local.explicitFlag === id) return log("skip:user-set-read-state");
    const message = host.state.messages.find((m) => m.id === id);
    if (!message?.unread) return log("skip:not-unread");
    if (!accountFor(message.account_id)?.capabilities?.write) return log("skip:read-only-account");
    log("mark-read");
    const show = (unread, seq = null) => {
      host.holdUnread(id, unread === null ? null : unread, seq);
      const current = host.state.messages.find((m) => m.id === id);
      if (host.state.activeId === id && current) $("previewUnread").hidden = !current.unread;
      host.renderScope();
      host.renderList();
      render();
    };
    show(false);
    try {
      const result = await host.api("/api/mark-read", {
        method: "POST",
        body: JSON.stringify({ id, read: true }),
      });
      log(result.ok ? "done" : "refused", { changed: Boolean(result.changed), seq: result.seq ?? "-" });
      if (!result.ok) {
        show(true); // put the row back, then let the server's state decide again
        host.holdUnread(id, null);
      } else if (result.changed && Number.isFinite(result.seq)) show(false, result.seq);
      else host.holdUnread(id, null);
    } catch (_) {
      log("failed");
      host.holdUnread(id, null);
      await host.loadState(); // the server's state, not a guess
    }
  }
  async function loadDetail(id) {
    local.detailId = id;
    clearDetail();
    try {
      const detail = await host.messageDetail(id); // shared with the reading pane: one request
      if (host.state.activeId !== id || local.detailId !== id) return;
      local.detail = detail;
      renderDetail(detail);
      markReadOnOpen(id);
    } catch (_) {
      if (local.detailId === id) local.detailId = null;
    }
  }
  const REPLY_ICONS = {
    reply: "M9 14L4 9l5-5 M4 9h11a5 5 0 0 1 5 5v2",
    "reply-all": "M7 14L2 9l5-5 M12 14L7 9l5-5 M7 9h9a5 5 0 0 1 5 5v2",
    forward: "M15 14l5-5-5-5 M20 9H9a5 5 0 0 0-5 5v2",
  };
  function replyIcon(mode) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "action-icon");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", REPLY_ICONS[mode]);
    svg.append(path);
    return svg;
  }
  function renderDetail(detail) {
    window.EmailTownImages?.render(detail);
    const actions = document.querySelector(".reply-actions");
    if (!actions) return;
    [
      ["reply", "Reply"],
      ["reply-all", "Reply all"],
      ["forward", "Forward"],
    ].forEach(([mode, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button reply-button";
      button.dataset.composeMode = mode;
      button.append(replyIcon(mode), label);
      button.onclick = () => {
        if (host.state.activeId === detail.id) openCompose(mode, detail);
      };
      actions.append(button);
    });
    // Gmail's "to me, Li and 10 others ▾": recipients folded under the sender, full list on demand.
    const reader = window.EmailTownReader;
    const own = text(accountFor(detail.account_id)?.address).toLowerCase();
    const everyone = reader
      ? [
          ...reader.splitAddresses(detail.headers?.to),
          ...reader.splitAddresses(detail.headers?.cc),
        ]
      : [];
    const named = (who) =>
      own && who.address.toLowerCase() === own ? "me" : reader.displayName(who);
    const first = everyone.slice(0, 2).map(named);
    const others = everyone.length - first.length;
    const panel = document.createElement("details");
    panel.className = "preview-headers";
    const summary = document.createElement("summary");
    summary.textContent = everyone.length
      ? `to ${first.join(", ")}${others > 0 ? ` and ${others} other${others > 1 ? "s" : ""}` : ""}`
      : "to undisclosed recipients";
    const list = document.createElement("dl");
    for (const [label, value] of [
      ["From", detail.headers?.from],
      ["To", detail.headers?.to],
      ["Cc", detail.headers?.cc],
      ["Reply-To", detail.headers?.["reply-to"]],
    ]) {
      if (!value) continue;
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.append(dt, dd);
    }
    panel.append(summary, list);
    $("previewEnvelope").append(panel);
    if (detail.attachments?.length) {
      const list = document.createElement("div");
      list.className = "preview-attachments";
      const title = document.createElement("strong");
      const count = detail.attachments.length;
      title.textContent = `${count} attachment${count > 1 ? "s" : ""}`;
      list.append(title);
      detail.attachments.forEach((attachment) => {
        // Gmail-style card: type badge, name (two lines at most), readable size.
        const button = document.createElement("button");
        button.type = "button";
        button.className = "attachment-card";
        const name = text(attachment.name) || "attachment";
        button.title = `Download ${name}`;
        const kind = document.createElement("span");
        kind.className = "attachment-kind";
        kind.textContent = name.includes(".")
          ? name.split(".").pop().slice(0, 4).toUpperCase()
          : "FILE";
        const label = document.createElement("span");
        label.className = "attachment-name";
        label.textContent = name;
        const size = document.createElement("span");
        size.className = "attachment-size";
        size.textContent = window.EmailTownReader
          ? window.EmailTownReader.humanSize(attachment.size)
          : `${Math.ceil(attachment.size / 1024)} KB`;
        button.append(kind, label, size);
        button.onclick = async () => {
          button.disabled = true;
          try {
            const blob = await host.fetchBinary(
              `/api/attachment?id=${encodeURIComponent(detail.id)}&attachment_id=${encodeURIComponent(attachment.id)}`,
            );
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download =
              text(attachment.name).replace(/[\\/\x00-\x1f]/g, "_") ||
              "attachment";
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch (error) {
            host.showToast(error.message);
          } finally {
            button.disabled = false;
          }
        };
        list.append(button);
      });
      // Between the message and its reply buttons, as Gmail places them.
      const replies = document.querySelector(".reply-actions");
      if (replies) replies.before(list);
      else $("previewContent").append(list);
    }
    updateControls();
  }
  $("addAttachmentButton").onclick = () => {
    if (!local.busy && !local.readingFiles) els.files.click();
  };
  els.files.onchange = async () => {
    if (local.busy || local.readingFiles) return;
    const files = [...els.files.files];
    const account = accountFor(els.account.value);
    const total =
      local.attachments.reduce((sum, item) => sum + attachmentBytes(item), 0) +
      files.reduce((sum, file) => sum + file.size, 0);
    if (
      files.length + local.attachments.length > 20 ||
      total > (account?.capabilities?.max_attachment_bytes || 0)
    ) {
      els.files.value = "";
      setError(
        "Attachments exceed the account size limit or the 20-file limit.",
      );
      return;
    }
    local.readingFiles = true;
    updateControls();
    try {
      const added = await Promise.all(
        files.map(
          (file) =>
            new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onerror = () =>
                reject(new Error("Could not read the selected file"));
              reader.onload = () =>
                resolve({
                  name: file.name,
                  type: file.type || "application/octet-stream",
                  size: file.size,
                  data: text(reader.result).split(",")[1] || "",
                });
              reader.readAsDataURL(file);
            }),
        ),
      );
      local.attachments.push(...added);
      local.dirty = true;
      local.editVersion += 1;
      setError("");
    } catch (error) {
      setError(error.message);
    } finally {
      local.readingFiles = false;
      els.files.value = "";
      renderAttachments();
      updateControls();
    }
  };
  document
    .querySelectorAll("[data-mail-folder]")
    .forEach(
      (button) =>
        (button.onclick = () => changeFolder(button.dataset.mailFolder)),
    );
  document
    .querySelectorAll("[data-mail-action]")
    .forEach(
      (button) =>
        (button.onclick = () => mailAction(button.dataset.mailAction)),
    );
  document.querySelectorAll("[data-single-action]").forEach(
    (button) =>
      (button.onclick = () => {
        if (host.state.activeId)
          mailAction(button.dataset.singleAction, [host.state.activeId], "pane-menu");
      }),
  );
  $("composeButton").onclick = () => openCompose();
  $("draftsButton").onclick = () => {
    els.drafts.showModal();
    loadDraftList();
  };
  document
    .querySelectorAll("[data-compose-close]")
    .forEach((button) => (button.onclick = closeCompose));
  document
    .querySelectorAll("[data-drafts-close]")
    .forEach((button) => (button.onclick = () => els.drafts.close()));
  els.compose.addEventListener("cancel", closeCompose);
  els.form.onsubmit = send;
  els.save.onclick = saveDraft;
  els.account.onchange = () => {
    local.dirty = true;
    local.editVersion += 1;
    setError(attachmentError());
    updateControls();
  };
  for (const field of [els.to, els.cc, els.bcc, els.subject, els.body])
    field.oninput = () => {
      local.dirty = true;
      local.editVersion += 1;
    };
  window.addEventListener("beforeunload", (event) => {
    if (local.dirty || local.busy || local.readingFiles) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  function render() {
    renderAccounts();
    renderSelection();
    const id = host.state.activeId;
    if (id !== local.opened) {
      host.uiLog?.("open-changed", { from: host.k8(local.opened), to: host.k8(id) });
      local.opened = id; // a new open: allow one open-to-read for it
      local.autoRead = null;
      local.explicitFlag = null;
    }
    if (id && id !== local.detailId) loadDetail(id);
    if (!id) {
      local.detailId = null;
      clearDetail();
    }
    // Only the active folder has a loaded snapshot; its unread count follows every other active filter.
    const unread = (host.filteredMessages?.() || host.state.messages).filter((m) => m.unread).length;
    const folder =
      { inbox: "Inbox", sent: "Sent", archive: "Archive", trash: "Trash" }[
        host.state.folder
      ] || "Mail";
    document.querySelectorAll("[data-mail-folder]").forEach((button) => {
      const active = button.dataset.mailFolder === host.state.folder;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
      if (active)
        button.setAttribute("aria-label", `${folder}, ${unread} unread loaded in current filters`);
      else button.removeAttribute("aria-label");
      let badge = button.querySelector(".folder-count");
      if (active && unread && !badge) {
        badge = document.createElement("span");
        badge.className = "folder-count";
        badge.setAttribute("aria-hidden", "true");
        button.append(badge);
      }
      if (badge) {
        badge.hidden = !(active && unread);
        badge.textContent = active && unread ? String(unread) : "";
        badge.title = active && unread ? `${unread} unread loaded in current filters` : "";
      }
    });
    const view = host.state.filters.done === "done" ? "Completed" : folder;
    document.title = unread ? `${view} (${unread}) · Email Town` : `${view} · Email Town`;
  }
  host.render = render;
  // A send, or a bulk action still being handed to the outbox (a few ms): the next key action waits.
  host.mailBusy = () => local.busy || Boolean(local.posting);
  // the buttons' path, permissions and confirm
  host.mailAction = (action, ids, via = "key") => mailAction(action, ids, via);
  window.EmailTownMail = { render };
  render();
})();
