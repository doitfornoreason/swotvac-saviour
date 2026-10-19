// Dashboard sync status polling.
// Initial state is embedded in #sync-status[data-state] by the server;
// while a job is running we poll /sync/status and refresh when it ends.

(function () {
  "use strict";

  const el = document.getElementById("sync-status");
  if (!el) return;

  let state;
  try {
    state = JSON.parse(el.dataset.state || "{}");
  } catch (e) {
    state = {};
  }

  function render(job) {
    if (job.error) {
      el.innerHTML = '<p class="notice"><strong>Sync failed:</strong> ' +
        escapeHtml(job.error) + "</p>";
      return;
    }
    if (job.running) {
      let msg = escapeHtml(job.stage || "working");
      if (job.name) msg += ": " + escapeHtml(job.name);
      if (job.tag) msg += " (tag: " + escapeHtml(job.tag) + ")";
      if (job.question_total > 0) {
        msg += " [question " + job.question_index + "/" + job.question_total + "]";
      }
      el.innerHTML = '<p class="notice"><strong>Syncing:</strong> ' + msg + "</p>";
      return;
    }
    if (job.summary) {
      el.innerHTML = '<p><strong>Last sync:</strong> ' +
        escapeHtml(String(job.summary)) + "</p>";
    } else {
      el.innerHTML = "";
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
  }

  function poll() {
    fetch("/sync/status", { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("status " + r.status);
        return r.json();
      })
      .then(function (job) {
        render(job);
        if (!job.running) {
          // Job finished: reload so documents/queue reflect the new DB.
          window.location.reload();
        }
      })
      .catch(function () {
        // Transient network error: keep polling.
      });
  }

  render(state);
  if (state.running) {
    setInterval(poll, 2000);
  }
})();
