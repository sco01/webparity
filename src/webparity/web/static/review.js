// SPDX-License-Identifier: BSD-2-Clause
// The review page: the filing queue on the left, one draft at a time on the right, with the bug report form's fields
// in GitHub's order. Edits are saved to webparity (never to the draft file). "Copy the GitHub link" opens the form
// prefilled; GitHub resets prefilled fields when they are edited, so all editing happens here first, and the
// Screenshots field is left empty in the link (paste the caption lines there and drag the images in).
"use strict";

const URL_LIMIT = 7500;
const FIELDS = [
  ["title", "Title", "input"],
  ["summary", "Summary", "text"],
  ["os", "Operating system", "input", "narrow"],
  ["distro-package", "Distro/package (if Linux)", "input", "narrow"],
  ["architecture", "Architecture", "input", "narrow"],
  ["commit", "Commit hash", "input-code", "narrow"],
  ["reproduction-steps", "Steps to reproduce", "text"],
  ["expected-behavior", "Expected behavior", "code"],
  ["actual-behavior", "Actual behavior", "code"],
  ["reduced-test-case-url", "URL for a reduced test case", "input"],
  ["reduced-test-case-source", "HTML/SVG/etc. source for a reduced test case", "code"],
  ["log-output", "Log output and (if possible) backtrace", "code"],
  ["screenshots", "Screenshots or screen recordings", "text"],
  ["build-flags", "Build flags or config settings", "text"],
];
const COMMENT_FIELDS = [["body", "Comment", "code"]];

const root = document.getElementById("review");
const REPO = root.dataset.repository;
let items = [], selected = null, unsaved = {}, timer = null, saving = Promise.resolve();
try { selected = Number(localStorage.getItem("review-selected")) || null; } catch (e) {}

const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...children) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
};

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Webparity-Review": "1" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

const current = p => Object.assign({}, p.fields, unsaved[p.id] || {});
const isDone = p => p.state === "filed" || p.state === "posted";

function chip(p) {
  if (p.state === "filed") return el("span", { class: "chip done", text: "Filed #" + p.number });
  if (p.state === "posted") return el("span", { class: "chip done", text: "Posted" });
  if (p.changed || unsaved[p.id]) return el("span", { class: "chip changed", text: "Changed" });
  return el("span", { class: "chip", text: p.kind === "comment" ? "Comment" : "Draft" });
}

function drawQueue() {
  const list = $("#queue"); list.replaceChildren();
  const left = items.filter(p => !isDone(p)).length;
  $("#queue-summary").textContent = items.length ? `${left} of ${items.length} left to file` : "No drafts waiting.";
  for (const p of items) {
    const f = current(p);
    const label = p.kind === "comment" ? `#${p.comment_on || "?"}: comment` : (f.title || p.file || String(p.id));
    list.append(el("li", {}, el("button", { "aria-current": p.id === selected ? "true" : "false", onclick: () => choose(p.id) },
      el("span", { class: "nr", text: p.kind === "comment" ? "C" : (p.position < 10000 ? String(p.position) : "·") }),
      el("span", {}, el("span", { class: "name", text: label }), el("br"), chip(p)))));
  }
}

function githubUrl(f) {
  const q = new URLSearchParams({ template: "bug_report.yml" });
  for (const [id] of FIELDS) if (f[id] && id !== "screenshots") q.set(id, f[id]);
  let url = `https://github.com/${REPO}/issues/new?${q}`, dropped = null;
  if (url.length > URL_LIMIT && f["reduced-test-case-source"]) {
    q.delete("reduced-test-case-source");
    url = `https://github.com/${REPO}/issues/new?${q}`;
    dropped = "reduced-test-case-source";
  }
  return { url, dropped };
}

async function copy(text, button) {
  const label = button.textContent;
  try { await navigator.clipboard.writeText(text); button.textContent = "Copied"; }
  catch (e) { button.textContent = "Copy refused – select the text"; }
  setTimeout(() => { button.textContent = label; }, 1600);
}

async function copyImage(src, button) {
  const label = button.textContent;
  try {
    const blob = await (await fetch(src)).blob();
    await navigator.clipboard.write([new ClipboardItem({ [blob.type || "image/png"]: blob })]);
    button.textContent = "Image copied";
  } catch (e) { button.textContent = "Not possible – use Save"; }
  setTimeout(() => { button.textContent = label; }, 2000);
}

function setStatus(text, warn) {
  const s = $("#save-status"); if (!s) return;
  s.textContent = text; s.className = warn ? "note warn" : "note";
}

function scheduleSave(p) {
  clearTimeout(timer);
  setStatus("Unsaved changes …");
  timer = setTimeout(() => save(p.id), 1200);
}

function save(id) {
  clearTimeout(timer);
  const pending = unsaved[id]; if (!pending) return saving;
  saving = saving.then(async () => {
    const sent = JSON.stringify(pending);
    try {
      const updated = await api(`/api/review/${id}/fields`, { fields: pending });
      replace(updated);
      // keep what was typed while the request ran
      if (JSON.stringify(unsaved[id]) === sent) delete unsaved[id]; else scheduleSave(updated);
      if (selected === id && !unsaved[id]) setStatus(updated.changed ? "Saved (changed from the draft file)" : "Saved");
      drawQueue();
    } catch (e) { setStatus("Could not save – the changes are still on the page (" + e.message + ")", true); }
  });
  return saving;
}

function replace(updated) {
  const i = items.findIndex(x => x.id === updated.id);
  if (i >= 0) items[i] = updated;
}

function drawView() {
  const view = $("#view"); view.replaceChildren();
  const p = items.find(x => x.id === selected) || items[0];
  if (!p) { view.append(el("p", { class: "empty", text: "No drafts waiting for review." })); return; }
  selected = p.id; try { localStorage.setItem("review-selected", String(selected)); } catch (e) {}
  const f = current(p);
  const i = items.indexOf(p);
  const nav = el("div", { class: "actions" },
    el("button", { class: "button small", onclick: () => i > 0 && choose(items[i - 1].id), disabled: i === 0 }, "← Previous"),
    el("button", { class: "button small", onclick: () => i < items.length - 1 && choose(items[i + 1].id), disabled: i === items.length - 1 }, "Next →"));

  const top = el("section", { class: "panel top" });
  top.append(el("div", { class: "meta" }, chip(p), el("code", { text: p.file || "" }),
    el("span", { id: "save-status", class: "note", text: p.changed ? "Changed from the draft file" : "" }), nav));
  if (!p.found) top.append(el("p", { class: "note warn", text: "The draft file was not found under data/drafts." }));
  if (p.unknown_headings.length) top.append(el("p", { class: "note warn", text: "Headings that are not form fields (left out): " + p.unknown_headings.join(", ") }));
  if (p.kind === "comment") {
    const issueUrl = `https://github.com/${REPO}/issues/${p.comment_on}`;
    top.append(el("div", { class: "actions" },
      el("a", { class: "button primary", href: issueUrl, target: "_blank", rel: "noopener" }, `Open #${p.comment_on}`),
      el("button", { class: "button", onclick: e => copy(current(p).body || "", e.currentTarget) }, "Copy the comment")));
    top.append(el("p", { class: "note", text: "Copy the comment, paste it at the bottom of the issue, and drag the screenshots in below it." }));
  } else {
    const { url } = githubUrl(f);
    top.append(el("div", { class: "actions" },
      el("button", { class: "button primary", onclick: e => { save(p.id); copy($("#github-url").value, e.currentTarget); } }, "Copy the GitHub link"),
      el("a", { class: "button", id: "github-link", href: url, target: "_blank", rel: "noopener", onclick: () => save(p.id) }, "Open the form on GitHub"),
      el("button", { class: "button", onclick: e => copy(current(p).title || "", e.currentTarget) }, "Copy the title")));
    top.append(el("div", { class: "f" },
      el("label", { for: "github-url", class: "note", text: "The link opens Ladybird's bug report form with the fields filled in." }),
      el("input", { id: "github-url", class: "code", readonly: true, value: url, onfocus: e => e.currentTarget.select() })));
    top.append(el("p", { class: "note", id: "url-note" }));
  }
  const post = el("div", { class: "post" });
  const error = el("span", { class: "note warn" });
  if (isDone(p)) {
    post.append(el("span", { class: "note", text: p.kind === "comment" ? "Marked as posted." : `Marked as filed as #${p.number}.` }),
      p.kind === "comment" ? null : el("a", { class: "button small", href: `https://github.com/${REPO}/issues/${p.number}`, target: "_blank", rel: "noopener" }, `Open #${p.number}`),
      el("button", { class: "button small", onclick: () => markAs(p, null, error) }, "Undo"), error);
  } else if (p.kind === "comment") {
    post.append(el("button", { class: "button", onclick: () => markAs(p, p.comment_on || 0, error) }, "Mark as posted"), error);
  } else {
    const nr = el("input", { id: "issue-number", inputmode: "numeric", placeholder: "number", "aria-label": "Issue number" });
    post.append(el("label", { for: "issue-number", text: "Filed as #" }), nr,
      el("button", { class: "button", onclick: () => {
        const n = parseInt(nr.value.replace(/\D/g, ""), 10);
        if (!(n > 0)) { error.textContent = "Type the issue number first."; return; }
        markAs(p, n, error);
      } }, "Mark as filed"), error);
  }
  top.append(post);
  top.append(el("p", { class: "note", text: "After filing, register it as usual (registrera); that updates the draft in the gallery and here." }));
  view.append(top);

  if (p.images.length) {
    view.append(el("section", { class: "panel" }, el("div", { class: "images" }, ...p.images.map(x => {
      const src = `/review/image/${p.id}/${encodeURIComponent(x.file)}`;
      if (!x.available) return el("figure", {}, el("figcaption", { text: `${x.text || x.file} – the file is missing` }));
      const img = el("img", { src, alt: x.text || x.file, loading: "lazy", onclick: () => { $("#zoom-img").src = src; $("#zoom").hidden = false; } });
      return el("figure", {}, img, el("figcaption", {}, el("span", { text: x.text || x.file }),
        el("span", { class: "actions" },
          el("button", { class: "button small", onclick: e => copyImage(src, e.currentTarget) }, "Copy image"),
          el("a", { class: "button small", href: src, download: x.file }, "Save"))));
    }))));
  }
  if (p.note) view.append(el("section", { class: "panel" }, el("details", { class: "background" },
    el("summary", { text: "Background (NOTE – not part of the issue)" }), el("pre", { text: p.note }))));

  const form = el("section", { class: "panel fields" });
  let narrow = null;
  for (const [id, label, kind, group] of (p.kind === "comment" ? COMMENT_FIELDS : FIELDS)) {
    const oneLine = kind.startsWith("input");
    const control = oneLine
      ? el("input", { id: "f-" + id, class: kind === "input-code" ? "code" : null, value: f[id] || "" })
      : el("textarea", { id: "f-" + id, class: kind === "code" ? "code" : null });
    if (!oneLine) control.value = f[id] || "";
    control.addEventListener("input", () => {
      (unsaved[p.id] ||= {})[id] = control.value;
      if (!oneLine) grow(control);
      if (p.kind !== "comment") updateUrl(p);
      scheduleSave(p);
    });
    control.addEventListener("blur", () => save(p.id));
    const row = el("div", { class: "row" }, el("label", { for: "f-" + id, text: label }),
      el("span", { class: "actions" }, el("span", { class: "id", text: id }),
        el("button", { class: "button small", onclick: e => copy(control.value, e.currentTarget) }, "Copy")));
    const box = el("div", { class: "f" + (id === "title" ? " title" : "") }, row, control);
    if (group === "narrow") { if (!narrow) { narrow = el("div", { class: "narrow" }); form.append(narrow); } narrow.append(box); }
    else { narrow = null; form.append(box); }
  }
  form.append(el("div", { class: "actions" }, el("button", { class: "button small", onclick: () => resetDraft(p) }, "Reset to the draft file's text")));
  view.append(form);
  if (p.kind !== "comment") updateUrl(p);
  requestAnimationFrame(() => document.querySelectorAll("#view textarea").forEach(grow));
}

function grow(t) { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight + 2, 900) + "px"; }

function updateUrl(p) {
  const link = $("#github-link"), note = $("#url-note"); if (!link || !note) return;
  const { url, dropped } = githubUrl(current(p));
  link.href = url; const field = $("#github-url"); if (field) field.value = url;
  note.className = dropped ? "note warn" : "note";
  note.textContent = (dropped ? "The test case is too long for the link and is not filled in: copy its field below and paste it on GitHub. " : "")
    + "Make every text change here first: GitHub resets prefilled fields when you edit them. Open each draft in a new tab "
    + "(GitHub remembers the form per tab). The Screenshots field is left empty: copy the caption lines from its field below, paste them, and drag the images in.";
}

async function markAs(p, number, error) {
  await save(p.id);
  try { replace(await api(`/api/review/${p.id}/mark`, { number })); drawQueue(); drawView(); }
  catch (e) { if (error) error.textContent = "Could not mark it: " + e.message; }
}

async function resetDraft(p) {
  delete unsaved[p.id];
  try { replace(await api(`/api/review/${p.id}/reset`, {})); drawQueue(); drawView(); }
  catch (e) { setStatus("Could not reset: " + e.message, true); }
}

async function choose(id) {
  if (selected && selected !== id) await save(selected);
  selected = id; drawQueue(); drawView(); scrollTo({ top: 0 });
}

$("#zoom").addEventListener("click", () => { $("#zoom").hidden = true; });
addEventListener("keydown", e => { if (e.key === "Escape") $("#zoom").hidden = true; });
addEventListener("beforeunload", e => { if (Object.keys(unsaved).length) { save(selected); e.preventDefault(); } });

(async () => {
  try { items = await api("/api/review"); }
  catch (e) { $("#queue-summary").textContent = "Could not load the drafts: " + e.message; return; }
  drawQueue(); drawView();
})();
