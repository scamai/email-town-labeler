// The reading pane, laid out as in Gmail: a plain-text body with clickable links, the quoted
// history at the end folded behind "•••", a dimmed signature; sender name/address, avatar, dates and
// sizes for the header and attachment cards. Mail text only ever reaches the page as textContent.
(() => {
  "use strict";

  const URL_RE = /\bhttps?:\/\/[^\s<>"']+/g;
  // Lines that introduce quoted history: "On … wrote:", "在 … 写道：", Outlook's separators.
  const ATTRIBUTION = /^(on\s.+\bwrote:|在.+写道[:：]|-{2,}\s*original message\s*-{2,}|_{8,}|from:\s.+)$/i;

  function linkify(parent, text) {
    let last = 0;
    for (const match of text.matchAll(URL_RE)) {
      let url = match[0].replace(/[).,;:!?]+$/, ""); // trailing punctuation belongs to the sentence
      parent.append(text.slice(last, match.index));
      const a = document.createElement("a");
      a.href = url;
      a.textContent = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      parent.append(a);
      last = match.index + url.length;
    }
    parent.append(text.slice(last));
  }
  const quoted = (line) => /^\s*>/.test(line);
  const blank = (line) => !line.trim();

  // Index where trailing quoted history starts (attribution or first quote), or -1.
  function trimmedStart(lines) {
    let i = lines.length;
    while (i > 0 && (quoted(lines[i - 1]) || blank(lines[i - 1]))) i -= 1;
    if (i === lines.length) return -1;
    if (i > 0 && ATTRIBUTION.test(lines[i - 1].trim())) i -= 1;
    else if (i > 1 && blank(lines[i - 1]) && ATTRIBUTION.test(lines[i - 2].trim())) i -= 2;
    return lines.slice(i).some(quoted) ? i : -1;
  }

  const isDelimiter = (line) => line === "-- " || line === "--"; // RFC 3676 signature separator

  // Split a body into its own text, the signature and the trailing quoted history. The signature
  // usually comes before the quote; some clients put it after, so both orders are recognised.
  function parts(lines) {
    let quote = trimmedStart(lines);
    let quoteEnd = lines.length;
    if (quote < 0) {
      let last = lines.length - 1;
      while (last >= 0 && !isDelimiter(lines[last])) last -= 1;
      const before = last > 0 ? trimmedStart(lines.slice(0, last)) : -1;
      // Only when the text above has no signature of its own; otherwise nothing is folded or dropped.
      if (before >= 0 && !lines.slice(0, before).some(isDelimiter)) [quote, quoteEnd] = [before, last];
    }
    const own = quote < 0 ? lines : lines.slice(0, quote);
    const signature = own.findIndex(isDelimiter);
    return {
      text: signature < 0 ? own : own.slice(0, signature),
      signature: signature >= 0 ? own.slice(signature) : quoteEnd < lines.length ? lines.slice(quoteEnd) : [],
      quote: quote < 0 ? [] : lines.slice(quote, quoteEnd),
      signatureLast: quoteEnd < lines.length,
    };
  }

  function block(className, lines) {
    const element = document.createElement("div");
    element.className = className;
    linkify(element, lines.join("\n").trim());
    return element;
  }

  function renderBody(target, body) {
    target.replaceChildren();
    const lines = String(body ?? "").replace(/\r\n?/g, "\n").split("\n");
    const { text, signature, quote, signatureLast } = parts(lines);
    const main = document.createElement("div");
    main.className = "email-text";
    linkify(main, text.join("\n").trimEnd());
    target.append(main);
    const sig = signature.length ? block("email-signature", signature) : null;
    if (sig && !signatureLast) target.append(sig);
    if (quote.length) {
      const fold = document.createElement("details");
      fold.className = "email-quoted";
      const summary = document.createElement("summary");
      summary.title = "Show trimmed content";
      summary.setAttribute("aria-label", "Show trimmed content");
      summary.textContent = "•••";
      fold.append(summary, block("email-text", quote));
      target.append(fold);
    }
    if (sig && signatureLast) target.append(sig);
  }

  // "Name <addr>" -> {name, address}; a bare address has no name.
  function parseAddress(value) {
    const text = String(value ?? "").trim();
    const match = text.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/);
    if (match) return { name: match[1].trim(), address: match[2].trim() };
    return { name: "", address: text };
  }
  // Split a header list on commas outside quotes and angle brackets.
  function splitAddresses(value) {
    const out = [];
    let current = "",
      depth = 0,
      quote = false;
    for (const ch of String(value ?? "")) {
      if (ch === '"') quote = !quote;
      else if (!quote && ch === "<") depth += 1;
      else if (!quote && ch === ">") depth = Math.max(0, depth - 1);
      if (ch === "," && !quote && !depth) {
        if (current.trim()) out.push(parseAddress(current));
        current = "";
      } else current += ch;
    }
    if (current.trim()) out.push(parseAddress(current));
    return out;
  }
  const displayName = (who) => who.name || who.address.split("@")[0] || who.address;

  function avatar(element, who) {
    const label = displayName(who).replace(/^[^\p{L}\p{N}]+/u, "");
    element.textContent = (label[0] || "?").toUpperCase();
    let hash = 0;
    for (const ch of who.address.toLowerCase()) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
    element.style.setProperty("--avatar-hue", String(hash % 360));
  }

  // "Sat, Nov 8, 2:01 PM", with the year when it is not this year.
  function longDate(epoch) {
    const date = new Date(Number(epoch) || 0);
    if (Number.isNaN(date.getTime())) return "—";
    const options = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
    if (date.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
    return new Intl.DateTimeFormat("en-US", options).format(date);
  }
  function humanSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  window.EmailTownReader = {
    renderBody,
    parseAddress,
    splitAddresses,
    displayName,
    avatar,
    longDate,
    humanSize,
  };
})();
