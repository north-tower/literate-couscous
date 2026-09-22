const statusEl = document.getElementById("status");
const usersBody = document.getElementById("usersBody");
const queueMeta = document.getElementById("queueMeta");
const queueDetail = document.getElementById("queueDetail");
const queueList = document.getElementById("queueList");
const createForm = document.getElementById("createForm");
const createBtn = document.getElementById("createBtn");

async function api(url, options = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...options });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("auth");
  }
  if (res.status === 403) {
    window.location.href = "/";
    throw new Error("forbidden");
  }
  return res;
}

function setStatus(text, kind = "") {
  statusEl.className = "status" + (kind ? ` ${kind}` : "");
  statusEl.textContent = text || "";
}

function renderQueue(queue) {
  const running = queue && queue.running;
  const queued = (queue && queue.queued) || [];
  queueList.replaceChildren();
  if (running) {
    queueMeta.textContent = running.status === "stopping" ? "stopping" : "running";
    queueDetail.textContent = running.email
      ? `${running.email} is using Chrome (${running.people_count || 0} people).`
      : "A campaign is using Chrome.";
  } else {
    queueMeta.textContent = queued.length ? "queued" : "idle";
    queueDetail.textContent = queued.length
      ? `${queued.length} campaign(s) waiting for Chrome.`
      : "No campaign is using Chrome.";
  }
  queued.forEach((job, index) => {
    const item = document.createElement("li");
    item.textContent = `${index + 1}. ${job.email} — ${job.people_count || 0} people`;
    queueList.appendChild(item);
  });
}

function renderUsers(users) {
  usersBody.replaceChildren();
  for (const user of users || []) {
    const tr = document.createElement("tr");
    const email = document.createElement("td");
    email.textContent = user.email;
    const role = document.createElement("td");
    role.textContent = user.role;
    const state = document.createElement("td");
    state.textContent = user.is_active ? "Active" : "Disabled";
    const actions = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "text-btn";
    btn.textContent = user.is_active ? "Disable" : "Enable";
    btn.addEventListener("click", () => toggleUser(user));
    actions.appendChild(btn);
    tr.append(email, role, state, actions);
    usersBody.appendChild(tr);
  }
}

async function loadAdmin() {
  const res = await api("/api/admin/users");
  const data = await res.json();
  if (!res.ok) {
    setStatus(data.error || "Could not load users", "error");
    return;
  }
  renderUsers(data.users);
  renderQueue(data.queue);
}

async function toggleUser(user) {
  const path = user.is_active
    ? `/api/admin/users/${user.id}/disable`
    : `/api/admin/users/${user.id}/enable`;
  const res = await api(path, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.error || "Could not update user", "error");
    return;
  }
  setStatus(user.is_active ? `Disabled ${user.email}` : `Enabled ${user.email}`, "ok");
  loadAdmin();
}

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  createBtn.disabled = true;
  try {
    const res = await api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: document.getElementById("newEmail").value,
        password: document.getElementById("newPassword").value,
        role: document.getElementById("newRole").value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(data.error || "Could not create user", "error");
      return;
    }
    createForm.reset();
    setStatus(`Created ${data.user.email}`, "ok");
    loadAdmin();
  } catch (_) {
    setStatus("Cannot reach Sendline server", "error");
  } finally {
    createBtn.disabled = false;
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  window.location.href = "/login";
});

loadAdmin().catch(() => setStatus("Could not load admin data", "error"));
setInterval(() => loadAdmin().catch(() => {}), 4000);
