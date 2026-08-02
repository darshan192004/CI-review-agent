(function () {
  "use strict";

  function getSavedTheme() {
    return localStorage.getItem("theme") || "dark";
  }

  function applyTheme() {
    var theme = getSavedTheme();
    document.documentElement.setAttribute("data-theme", theme);
    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", theme === "light" ? "true" : "false");
    }
  }

  // Delegated so it survives hx-boost body swaps without re-binding (and never
  // double-binds when htmx:load fires for boosted content).
  function bindThemeToggle() {
    document.addEventListener("click", function (evt) {
      var target = evt.target;
      var toggle = target && target.closest ? target.closest("#theme-toggle") : null;
      if (!toggle) return;
      var next = getSavedTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("theme", next);
      toggle.setAttribute("aria-pressed", next === "light" ? "true" : "false");
    });
  }

  function requestPath(evt) {
    return evt.detail && evt.detail.requestConfig && evt.detail.requestConfig.path
      ? evt.detail.requestConfig.path
      : evt.detail && evt.detail.path
        ? evt.detail.path
        : "";
  }

  function isPageNavigation(path) {
    if (!path) return true;
    if (path.indexOf("/api/") === 0) return false;
    if (path.indexOf("/static/") === 0) return false;
    return true;
  }

  // hx-boost swaps <body> innerHTML on page navigation; the page never
  // unloads, so any open SSE connection must be closed before the swap or it
  // leaks. The dashboard owns the connection and publishes it on this slot.
  function bindSseCleanup() {
    document.addEventListener("htmx:beforeSwap", function (evt) {
      if (!isPageNavigation(requestPath(evt))) return;
      if (window.__ciDashboardEs) {
        window.__ciDashboardEs.close();
        window.__ciDashboardEs = null;
      }
    });
  }

  function handleAfterRequest(evt) {
    var xhr = evt.detail && evt.detail.xhr;
    if (!xhr) return;

    var target = evt.detail.target;
    if (target && target.id === "settings-form") {
      if (xhr.status === 200) {
        showToast("Settings saved successfully!", "success");
      } else {
        showToast(extractErrorDetail(xhr, "Failed to save settings."), "error");
      }
    }

    var path = requestPath(evt);
    if (path.indexOf("/api/test/") === 0) {
      try {
        var res = JSON.parse(xhr.responseText);
        if (res.ok) {
          var detail = res.user
            ? "Connected as " + res.user
            : res.models
              ? "Models: " + res.models.join(", ")
              : res.server
                ? "Server: " + res.server
                : res.detail || "Success";
          showToast(detail, "success");
        } else {
          showToast(res.detail || "Test failed", "error");
        }
      } catch (e) {
        if (xhr.status >= 400) {
          showToast(extractErrorDetail(xhr, "Test failed (" + xhr.status + ")"), "error");
        }
      }
    }
  }

  function handleResponseError(evt) {
    var xhr = evt.detail && evt.detail.xhr;
    if (!xhr) return;
    showToast(extractErrorDetail(xhr, "Request failed (" + xhr.status + ")"), "error");
  }

  function init() {
    applyTheme();
    bindThemeToggle();
    bindSseCleanup();

    // Webhook health check (called by the dashboard on SSE connect, also every 30s).
    setInterval(fetchWebhookHealth, 30000);

    document.addEventListener("htmx:afterRequest", handleAfterRequest);
    document.addEventListener("htmx:responseError", handleResponseError);
  }

  // Re-sync the theme button state after hx-boost swaps in a fresh toggle.
  document.addEventListener("htmx:load", applyTheme);

  // hx-boost history restore swaps cached HTML back in WITHOUT re-executing
  // inline scripts. Re-run any page-scoped scripts (marked with
  // data-page-script) so the dashboard's SSE/repo wiring comes back.
  document.addEventListener("htmx:restored", function () {
    applyTheme();
    var scripts = document.querySelectorAll("script[data-page-script]");
    Array.prototype.forEach.call(scripts, function (script) {
      var clone = document.createElement("script");
      clone.textContent = script.textContent;
      document.body.appendChild(clone);
      document.body.removeChild(clone);
    });
  });

  document.addEventListener("DOMContentLoaded", init);
})();

function extractErrorDetail(xhr, fallback) {
  try {
    const res = JSON.parse(xhr.responseText);
    if (res && res.detail) return res.detail;
  } catch (e) {}
  return fallback;
}

function toggleSecret(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === "password") {
    input.type = "text";
    btn.innerHTML = `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858-5.858A9.954 9.954 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m-4.588-4.588a3 3 0 11-4.243-4.243M9.878 9.878l4.242 4.242M3 3l18 18"/></svg>`;
  } else {
    input.type = "password";
    btn.innerHTML = `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>`;
  }
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  const icon = type === "success"
    ? `<svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>`
    : type === "error"
    ? `<svg class="w-5 h-5 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>`
    : `<svg class="w-5 h-5 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`;

  toast.innerHTML = `${icon} <span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(10px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function filterRunsTable() {
  const input = document.getElementById("run-search-input");
  if (!input) return;
  const filter = input.value.toLowerCase();
  const rows = document.querySelectorAll("#runs-table-body tr");

  rows.forEach(row => {
    const text = row.textContent.toLowerCase();
    row.style.display = text.includes(filter) ? "" : "none";
  });
}

function fetchWebhookHealth() {
  const el = document.getElementById("webhook-health");
  if (!el) return;

  fetch("/api/webhook-health")
    .then(r => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(data => {
      const repos = Object.keys(data);
      if (repos.length === 0) {
        el.textContent = "";
        return;
      }
      const stale = repos.filter(r => data[r].status === "stale");
      if (stale.length > 0) {
        el.textContent = `Webhooks: ${stale.length} stale`;
        el.className = "text-xs font-mono text-amber-400";
      } else {
        el.textContent = "Webhooks: healthy";
        el.className = "text-xs font-mono text-emerald-400";
      }
    })
    .catch(() => {
      el.textContent = "Webhooks: unavailable";
      el.className = "text-xs font-mono text-amber-400";
    });
}
