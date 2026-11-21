// Coachal: the little JavaScript htmx doesn't cover. Toasts, upload progress, the service worker.
"use strict";

// Server responses raise toasts with an `HX-Trigger: {"toast": "..."}` header.
document.addEventListener("toast", (event) => {
  const box = document.getElementById("toasts");
  if (!box) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = event.detail.value;
  box.append(toast);
  setTimeout(() => toast.classList.add("leaving"), 2600);
  setTimeout(() => toast.remove(), 3000);
});

// File uploads report progress on a <progress> inside the form.
document.addEventListener("htmx:xhr:progress", (event) => {
  const bar = event.target.querySelector && event.target.querySelector("progress");
  if (bar && event.detail.lengthComputable) {
    bar.hidden = false;
    bar.value = (event.detail.loaded / event.detail.total) * 100;
  }
});

// Show the chosen file's name in a drop zone.
document.addEventListener("change", (event) => {
  const input = event.target;
  if (input.matches && input.matches(".dropzone input[type=file]") && input.files.length) {
    input.closest(".dropzone").querySelector(".dropzone-name").textContent = input.files[0].name;
  }
});

// After a background sync the header chip raises `synced`: reload to show the new data, unless
// you have started filling in a form.
// Autosaving forms (hx-trigger="change") don't count as a draft; navigating or submitting ends one.
let typing = false;
document.addEventListener("input", (event) => {
  if (event.target.closest && event.target.closest("form:not([hx-trigger])")) typing = true;
});
document.addEventListener("htmx:afterRequest", (event) => {
  if (event.detail.successful && (event.detail.boosted || event.detail.elt.tagName === "FORM")) typing = false;
});
document.addEventListener("synced", () => {
  if (typing) document.dispatchEvent(new CustomEvent("toast", { detail: { value: "Synced. Refresh to see new data." } }));
  // Swap only <main>: a full reload flashes the page and loses your scroll position.
  else htmx.ajax("GET", location.href, { target: "#main", select: "#main", swap: "outerHTML show:none" });
});

// A failed request used to fail silently: say so. Background GETs (the sync chip's polling) stay quiet.
function failed(message) {
  return (event) => {
    const config = event.detail.requestConfig;
    if (config && config.verb === "get" && !event.detail.boosted) return;
    document.dispatchEvent(new CustomEvent("toast", { detail: { value: message } }));
  };
}
document.addEventListener("htmx:sendError", failed("Can't reach Coachal. Check your connection and try again."));
document.addEventListener("htmx:responseError", failed("Something went wrong on our side. Please try again."));

// A link to /page#form-id opens that collapsed form.
function openTarget() {
  const target = location.hash && document.getElementById(location.hash.slice(1));
  if (target && target.tagName === "DETAILS") target.open = true;
}
window.addEventListener("hashchange", openTarget);
document.addEventListener("DOMContentLoaded", openTarget);
document.addEventListener("htmx:afterSettle", openTarget);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
