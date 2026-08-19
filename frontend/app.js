const scriptContainer = document.querySelector("#scripts");
const message = document.querySelector("#message");
const apiKey = document.querySelector("#api-key");

function showMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("message-error", isError);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (options.method && options.method !== "GET") {
    headers["X-API-Key"] = apiKey.value;
  }

  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_error) {
      // The generic HTTP error above is enough for a non-JSON response.
    }
    throw new Error(detail);
  }
  return response.json();
}

function scriptCard(script) {
  const article = document.createElement("article");
  article.className = "script-card";
  article.dataset.scriptId = script.id;

  const stateClass = script.enabled ? "state-enabled" : "state-paused";
  const stateLabel = script.enabled ? "Scheduled" : "Paused";
  const running = script.running ? '<span class="running">Running now</span>' : "";
  article.innerHTML = `
    <div class="card-heading">
      <div>
        <h2>${escapeHtml(script.name)}</h2>
        <code>${escapeHtml(script.filename)}</code>
      </div>
      <span class="state ${stateClass}">${stateLabel}</span>
    </div>
    ${running}
    <form class="schedule-form">
      <label>
        <span>Cron schedule</span>
        <input name="cron" value="${escapeHtml(script.cron_expression)}" required />
      </label>
      <button class="button button-secondary" type="submit">Save schedule</button>
    </form>
    <div class="actions">
      <button class="button run-button" type="button" ${script.running ? "disabled" : ""}>
        Run now
      </button>
      <button class="button button-secondary state-button" type="button">
        ${script.enabled ? "Pause" : "Resume"}
      </button>
    </div>
  `;

  article.querySelector(".schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const cron = new FormData(event.currentTarget).get("cron");
    await perform(
      () => api(`/api/scripts/${script.id}/schedule`, {
        method: "PATCH",
        body: JSON.stringify({ cron_expression: cron }),
      }),
      "Schedule updated",
    );
  });

  article.querySelector(".run-button").addEventListener("click", async () => {
    await perform(
      () => api(`/api/scripts/${script.id}/run`, { method: "POST" }),
      "Script accepted for execution",
    );
  });

  article.querySelector(".state-button").addEventListener("click", async () => {
    const action = script.enabled ? "pause" : "resume";
    await perform(
      () => api(`/api/scripts/${script.id}/${action}`, { method: "POST" }),
      script.enabled ? "Schedule paused" : "Schedule resumed",
    );
  });

  return article;
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value);
  return element.innerHTML;
}

async function loadScripts() {
  try {
    const scripts = await api("/api/scripts", { method: "GET" });
    scriptContainer.replaceChildren(...scripts.map(scriptCard));
    if (scripts.length === 0) {
      scriptContainer.innerHTML = '<p class="empty">No Python scripts found.</p>';
    }
  } catch (error) {
    scriptContainer.innerHTML = '<p class="empty">Could not load scripts.</p>';
    showMessage(error.message, true);
  }
}

async function perform(action, successMessage) {
  if (!apiKey.value) {
    showMessage("Enter the control API key first.", true);
    apiKey.focus();
    return;
  }
  try {
    await action();
    showMessage(successMessage);
    await loadScripts();
  } catch (error) {
    showMessage(error.message, true);
  }
}

document.querySelector("#refresh").addEventListener("click", loadScripts);
loadScripts();
