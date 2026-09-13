"use strict";

const elements = {
  connectionPill: document.querySelector("#connection-pill"),
  connectionError: document.querySelector("#connection-error"),
  providerName: document.querySelector("#provider-name"),
  providerModel: document.querySelector("#provider-model"),
  serverList: document.querySelector("#server-list"),
  messageList: document.querySelector("#message-list"),
  emptyState: document.querySelector("#empty-state"),
  processing: document.querySelector("#processing"),
  composer: document.querySelector("#composer"),
  messageInput: document.querySelector("#message-input"),
  characterCount: document.querySelector("#character-count"),
  sendButton: document.querySelector("#send-button"),
  clearButton: document.querySelector("#clear-button"),
  dialog: document.querySelector("#confirmation-dialog"),
  confirmServer: document.querySelector("#confirmation-server"),
  confirmTool: document.querySelector("#confirmation-tool"),
  confirmEffect: document.querySelector("#confirmation-effect"),
  confirmArguments: document.querySelector("#confirmation-arguments"),
  confirmExpiry: document.querySelector("#confirmation-expiry"),
  rejectButton: document.querySelector("#reject-button"),
  acceptButton: document.querySelector("#accept-button"),
};

let pollTimer = null;
let currentConfirmationId = null;
let confirmationSubmitting = false;

async function requestJson(path, options = {}) {
  const request = {
    method: options.method || "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  };
  if (Object.prototype.hasOwnProperty.call(options, "body")) {
    request.headers["Content-Type"] = "application/json; charset=utf-8";
    request.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, request);
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error("El backend devolvió una respuesta inválida.");
  }
  if (!response.ok) {
    const message = typeof payload.error === "string"
      ? payload.error
      : `La solicitud falló con HTTP ${response.status}.`;
    throw new Error(message);
  }
  return payload;
}

function setConnection(online, message = "") {
  elements.connectionPill.dataset.state = online ? "online" : "error";
  elements.connectionPill.textContent = online ? "Backend local" : "Sin conexión";
  elements.connectionError.hidden = online;
  if (!online && message) {
    elements.connectionError.textContent = message;
  }
}

function renderStatus(payload) {
  const provider = payload.provider || {};
  elements.providerName.textContent = String(provider.name || "No disponible");
  elements.providerModel.textContent = String(provider.model || "");
  elements.serverList.replaceChildren();
  const servers = Array.isArray(payload.servers) ? payload.servers : [];
  for (const server of servers) {
    const item = document.createElement("li");
    const safeStatus = ["ready", "error", "stopped"].includes(server.status)
      ? server.status
      : "stopped";
    item.className = "server-item";
    item.dataset.status = safeStatus;

    const dot = document.createElement("span");
    dot.className = "server-dot";
    dot.setAttribute("aria-hidden", "true");
    const content = document.createElement("span");
    const name = document.createElement("span");
    name.className = "server-name";
    name.textContent = String(server.name || "Servidor");
    const meta = document.createElement("span");
    meta.className = "server-meta";
    meta.textContent = `${String(server.transport || "MCP")} · ${statusLabel(safeStatus)}`;
    content.append(name, meta);
    item.append(dot, content);
    elements.serverList.append(item);
  }
  if (servers.length === 0) {
    const empty = document.createElement("li");
    empty.className = "server-item";
    empty.textContent = "Sin servidores configurados";
    elements.serverList.append(empty);
  }
  renderSession(payload.session || {});
}

function statusLabel(status) {
  return { ready: "listo", error: "error", stopped: "detenido" }[status] || "desconocido";
}

function renderSession(session) {
  const messages = Array.isArray(session.messages) ? session.messages : [];
  elements.messageList.replaceChildren();
  if (messages.length === 0) {
    elements.messageList.append(elements.emptyState);
    elements.emptyState.hidden = false;
  } else {
    elements.emptyState.hidden = true;
    for (const message of messages) {
      elements.messageList.append(createMessage(message));
    }
  }
  elements.messageList.scrollTop = elements.messageList.scrollHeight;

  const busy = session.state === "processing" || session.state === "awaiting_confirmation";
  elements.processing.hidden = !busy;
  elements.processing.querySelector("span:last-child").textContent =
    session.state === "awaiting_confirmation"
      ? "Esperando tu confirmación…"
      : "Procesando respuesta…";
  elements.messageInput.disabled = busy;
  elements.sendButton.disabled = busy;
  elements.clearButton.disabled = busy;
  showPendingConfirmation(session.pending_confirmation || null);

  if (busy) {
    schedulePoll();
  } else {
    stopPolling();
  }
}

function createMessage(message) {
  const allowedRoles = new Set(["user", "assistant", "tool", "error"]);
  const role = allowedRoles.has(message.role) ? message.role : "error";
  const labels = {
    user: "Tú",
    assistant: "Asistente",
    tool: "Tool MCP",
    error: "Error",
  };
  const article = document.createElement("article");
  article.className = "message";
  article.dataset.role = role;
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = labels[role];
  const text = document.createElement("span");
  text.textContent = typeof message.text === "string" ? message.text : "Mensaje inválido.";
  article.append(label, text);
  return article;
}

function showPendingConfirmation(pending) {
  if (!pending || typeof pending.id !== "string") {
    currentConfirmationId = null;
    if (elements.dialog.open && !confirmationSubmitting) {
      elements.dialog.close();
    }
    return;
  }
  currentConfirmationId = pending.id;
  elements.confirmServer.textContent = String(pending.server || "Servidor MCP");
  elements.confirmTool.textContent = String(pending.tool || "Operación mutable");
  elements.confirmEffect.textContent = String(pending.effect || "Puede modificar estado.");
  elements.confirmArguments.textContent = String(pending.arguments || "Resumen no disponible.");
  elements.confirmExpiry.textContent = `Expira en aproximadamente ${Number(pending.expires_in_seconds) || 0} segundos. Si no respondes, se rechazará.`;
  if (!elements.dialog.open) {
    elements.dialog.showModal();
    elements.rejectButton.focus();
  }
}

function schedulePoll() {
  if (pollTimer !== null) return;
  pollTimer = window.setTimeout(pollConversation, 500);
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function pollConversation() {
  pollTimer = null;
  try {
    const session = await requestJson("/api/chat");
    setConnection(true);
    renderSession(session);
  } catch (error) {
    setConnection(false, error instanceof Error ? error.message : "Se perdió la conexión local.");
    pollTimer = window.setTimeout(pollConversation, 1500);
  }
}

async function submitMessage(event) {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message) {
    elements.messageInput.focus();
    return;
  }
  elements.sendButton.disabled = true;
  try {
    const session = await requestJson("/api/chat", {
      method: "POST",
      body: { message },
    });
    elements.messageInput.value = "";
    updateCharacterCount();
    setConnection(true);
    renderSession(session);
  } catch (error) {
    setConnection(false, error instanceof Error ? error.message : "No se pudo enviar el mensaje.");
    elements.sendButton.disabled = false;
  }
}

async function decideConfirmation(accept) {
  if (confirmationSubmitting || currentConfirmationId === null) return;
  confirmationSubmitting = true;
  elements.rejectButton.disabled = true;
  elements.acceptButton.disabled = true;
  const confirmationId = currentConfirmationId;
  try {
    const session = await requestJson("/api/confirm", {
      method: "POST",
      body: { confirmation_id: confirmationId, accept },
    });
    currentConfirmationId = null;
    elements.dialog.close();
    setConnection(true);
    renderSession(session);
    schedulePoll();
  } catch (error) {
    setConnection(false, error instanceof Error ? error.message : "No se pudo registrar la decisión.");
    schedulePoll();
  } finally {
    confirmationSubmitting = false;
    elements.rejectButton.disabled = false;
    elements.acceptButton.disabled = false;
  }
}

async function clearConversation() {
  elements.clearButton.disabled = true;
  try {
    const session = await requestJson("/api/clear", { method: "POST", body: {} });
    renderSession(session);
    setConnection(true);
    elements.messageInput.focus();
  } catch (error) {
    setConnection(false, error instanceof Error ? error.message : "No se pudo limpiar la conversación.");
  } finally {
    elements.clearButton.disabled = false;
  }
}

function updateCharacterCount() {
  elements.characterCount.textContent = `${elements.messageInput.value.length} / 8000`;
}

async function initialize() {
  try {
    const status = await requestJson("/api/status");
    renderStatus(status);
    setConnection(true);
  } catch (error) {
    setConnection(
      false,
      error instanceof Error
        ? error.message
        : "No se pudo iniciar la interfaz local.",
    );
  }
}

elements.composer.addEventListener("submit", submitMessage);
elements.messageInput.addEventListener("input", updateCharacterCount);
elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});
elements.clearButton.addEventListener("click", clearConversation);
elements.rejectButton.addEventListener("click", () => decideConfirmation(false));
elements.acceptButton.addEventListener("click", () => decideConfirmation(true));
elements.dialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  decideConfirmation(false);
});

initialize();

(async function initialize() {
  try {
    const status = await requestJson("/api/status");
    renderStatus(status);
    setConnection(true);
    elements.messageInput.focus();
  } catch (error) {
    setConnection(false, error instanceof Error ? error.message : "No se pudo iniciar la interfaz local.");
  }
})();
