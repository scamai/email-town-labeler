(() => {
  "use strict";
  const host = window.EmailTownMailHost;
  if (!host) return;
  let generation = 0;
  let panel = null;
  const urls = new Set();
  const controllers = new Set();

  function clear() {
    generation += 1;
    controllers.forEach((controller) => controller.abort());
    controllers.clear();
    panel?.querySelectorAll("img").forEach((img) => img.removeAttribute("src"));
    panel?.remove();
    panel = null;
    urls.forEach((url) => URL.revokeObjectURL(url));
    urls.clear();
  }

  function render(detail) {
    clear();
    const images = Array.isArray(detail.images) ? detail.images.slice(0, 50) : [];
    if (!images.length) return;
    const version = generation;
    const current = () => version === generation && host.state.activeId === detail.id;
    panel = document.createElement("section");
    panel.className = "preview-images";
    panel.setAttribute("aria-label", "Images from this message");
    const heading = document.createElement("h3");
    heading.textContent = "Images from this message";
    panel.append(heading);
    document.getElementById("previewBody").after(panel);
    let usedBytes = 0;
    const embedded = [];
    const remote = [];

    images.forEach((item, index) => {
      const figure = document.createElement("figure");
      figure.className = "message-image";
      const img = document.createElement("img");
      img.alt = String(item.alt || `Message image ${index + 1}`);
      img.referrerPolicy = "no-referrer";
      img.decoding = "async";
      img.hidden = true;
      const status = document.createElement("figcaption");
      status.textContent = item.url ? "External image blocked" : "Loading image…";
      img.onload = () => {
        if (!current()) return;
        img.hidden = false;
        status.textContent = String(item.alt || "");
        status.hidden = !item.alt;
      };
      img.onerror = () => {
        if (!current()) return;
        img.hidden = true;
        status.hidden = false;
        status.textContent = "Image could not be loaded. Try opening the original message.";
      };
      figure.append(img, status);
      panel.append(figure);
      if (item.url) {
        // Only the validated HTTPS reference is used, never sender HTML or attributes.
        try {
          const url = new URL(item.url);
          if (url.protocol === "https:" && !url.username && !url.password)
            remote.push(() => {
              if (!current()) return;
              status.textContent = "Loading image…";
              img.src = url.href;
            });
        } catch (_) {
          status.textContent = "Unsupported image address";
        }
      } else if (item.attachment_id) {
        embedded.push(async () => {
          if (!current()) return;
          const controller = new AbortController();
          controllers.add(controller);
          const timer = setTimeout(() => controller.abort(), 15000);
          try {
            if (usedBytes >= 32 * 1024 * 1024)
              throw new Error("Preview image limit reached. Download the attachment to view it.");
            const blob = await host.fetchBinary(
              `/api/image?id=${encodeURIComponent(detail.id)}&attachment_id=${encodeURIComponent(item.attachment_id)}`,
              { signal: controller.signal },
            );
            if (!current()) return;
            if (blob.size > 8 * 1024 * 1024 || usedBytes + blob.size > 32 * 1024 * 1024)
              throw new Error("Preview image limit reached. Download the attachment to view it.");
            usedBytes += blob.size;
            const url = URL.createObjectURL(blob);
            urls.add(url);
            img.src = url;
          } catch (_) {
            if (current()) {
              status.textContent = "Image could not be loaded. Reopen the message to retry, or download the attachment.";
            }
          } finally {
            clearTimeout(timer);
            controllers.delete(controller);
          }
        });
      }
    });
    if (remote.length) {
      const notice = document.createElement("p");
      notice.className = "image-notice";
      notice.textContent = "External images may let the sender know you opened this message. ";
      const load = document.createElement("button");
      load.type = "button";
      load.className = "text-button";
      load.textContent = `Load external images (${remote.length})`;
      load.onclick = () => {
        if (!current()) return;
        load.disabled = true;
        remote.forEach((start) => start());
        load.textContent = "External images allowed for this view";
      };
      notice.append(load);
      heading.after(notice);
    }
    // One provider download at a time, so a large newsletter cannot occupy every connection.
    (async () => {
      for (const load of embedded) {
        if (!current()) return;
        await load();
      }
    })();
  }
  window.EmailTownImages = { render, clear };
  window.addEventListener("pagehide", clear);
})();
