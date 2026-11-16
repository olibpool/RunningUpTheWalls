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
let typing = false;
document.addEventListener("input", () => { typing = true; });
document.addEventListener("synced", () => {
  if (typing) document.dispatchEvent(new CustomEvent("toast", { detail: { value: "Synced. Refresh to see new data." } }));
  else location.reload();
});

// A link to /page#form-id opens that collapsed form.
function openTarget() {
  const target = location.hash && document.getElementById(location.hash.slice(1));
  if (target && target.tagName === "DETAILS") target.open = true;
}
window.addEventListener("hashchange", openTarget);
document.addEventListener("DOMContentLoaded", openTarget);
document.addEventListener("htmx:afterSettle", openTarget);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
