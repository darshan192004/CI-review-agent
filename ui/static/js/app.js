document.addEventListener("DOMContentLoaded", function () {
  // Theme Setup
  const savedTheme = localStorage.getItem("theme") || "dark";
  document.documentElement.setAttribute("data-theme", savedTheme);

  const themeToggle = document.getElementById("theme-toggle");
  if (themeToggle) {
    themeToggle.setAttribute("aria-pressed", savedTheme === "light" ? "true" : "false");
    themeToggle.addEventListener("click", function () {
      const current = document.documentElement.getAttribute("data-theme");
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("theme", next);
      themeToggle.setAttribute("aria-pressed", next === "light" ? "true" : "false");
    });
  }

  // Webhook health check (called by dashboard.js on SSE connect, also every 30s)
  const webhookHealth = document.getElementById("webhook-health");

  // Refresh webhook health every 30s
  setInterval(fetchWebhookHealth, 30000);

  // HTMX Event Listeners for smooth notifications
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    const xhr = evt.detail.xhr;
    if (evt.detail.target && evt.detail.target.id === "settings-form") {
      if (xhr.status === 200) {
        showToast("Settings saved successfully!", "success");
      } else {
        showToast(extractErrorDetail(xhr, "Failed to save settings."), "error");
      }
    }
  });

  document.body.addEventListener("htmx:responseError", function (evt) {
    const xhr = evt.detail.xhr;
    showToast(
      extractErrorDetail(xhr, "Request failed (" + xhr.status + ")"),
      "error"
    );
  });

  // Handle test button responses (forgejo, github, ollama, mcp, messaging)
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    const xhr = evt.detail.xhr;
    const path = evt.detail.requestConfig?.path || evt.detail.path || "";
    
    if (path.includes("/api/test/")) {
      try {
        const res = JSON.parse(xhr.responseText);
        if (res.ok) {
          const detail = res.user ? `Connected as ${res.user}` : 
                         res.models ? `Models: ${res.models.join(", ")}` :
                         res.server ? `Server: ${res.server}` :
                         res.detail || "Success";
          showToast(detail, "success");
        } else {
          showToast(res.detail || "Test failed", "error");
        }
      } catch (e) {
        if (xhr.status >= 400) {
          showToast(
            extractErrorDetail(xhr, "Test failed (" + xhr.status + ")"),
            "error"
          );
        }
      }
    }
  });
});

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
