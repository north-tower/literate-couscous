const statusEl = document.getElementById("status");
const usersBody = document.getElementById("usersBody");
const queueMeta = document.getElementById("queueMeta");
const queueDetail = document.getElementById("queueDetail");
const queueList = document.getElementById("queueList");
const createForm = document.getElementById("createForm");
const createBtn = document.getElementById("createBtn");
const proxyForm = document.getElementById("proxyForm");
const proxySaveBtn = document.getElementById("proxySaveBtn");
const proxyMeta = document.getElementById("proxyMeta");
const proxySummary = document.getElementById("proxySummary");
const proxyLines = document.getElementById("proxyLines");
const proxyStatus = document.getElementById("proxyStatus");
let proxiesConfigured = false;

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

function roleLabel(role) {
  return role === "admin" ? "Can manage users" : "Campaign user";
}

function exitLabel(user) {
  if (user.proxy_label) return user.proxy_label;
  return proxiesConfigured ? "Next run" : "This server";
}

function setProxyStatus(text, kind = "") {
  proxyStatus.className = "status" + (kind ? ` ${kind}` : "");
  proxyStatus.textContent = text || "";
}

function renderProxies(data) {
  proxiesConfigured = Boolean(data && data.configured);
  proxyLines.replaceChildren();
  const used = new Set((data && data.assigned ? data.assigned : []).filter((row) => row.mode !== "sticky").map((row) => row.label));
  (data && data.static_masked ? data.static_masked : []).forEach((line) => {
    const item = document.createElement("li");
    const inUse = [...used].some((label) => label && line.endsWith(label));
    item.textContent = inUse ? `${line} — in use` : `${line} — free`;
    proxyLines.appendChild(item);
  });
  if (!data || !data.configured) {
    proxyMeta.textContent = "off";
    proxySummary.textContent = "No proxies yet. Campaigns use this server's IP until you add some.";
    return;
  }
  if (data.template && !data.static_count) {
    proxyMeta.textContent = "sticky";
    proxySummary.textContent = data.template_masked
      ? `Sticky gateway ${data.template_masked}. Each client keeps their own session.`
      : "Sticky gateway on. Each client keeps their own session.";
    return;
  }
  proxyMeta.textContent = data.free_count ? `${data.free_count} free` : "all in use";
  const sticky = data.template ? " Extra clients use the sticky gateway." : "";
  proxySummary.textContent = `${data.static_count} static exit(s), ${data.free_count} still free.${sticky}`;
}

function renderUsers(users) {
  usersBody.replaceChildren();
  for (const user of users || []) {
    const tr = document.createElement("tr");
    const email = document.createElement("td");
    email.textContent = user.email;
    const role = document.createElement("td");
    role.textContent = roleLabel(user.role);
    const exit = document.createElement("td");
    exit.textContent = exitLabel(user);
    const state = document.createElement("td");
    state.textContent = user.is_active ? "Active" : "Disabled";
    const actions = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "text-btn";
    btn.textContent = user.is_active ? "Disable" : "Enable";
    btn.addEventListener("click", () => toggleUser(user));
    actions.appendChild(btn);
    if (user.proxy_label) {
      const release = document.createElement("button");
      release.type = "button";
      release.className = "text-btn";
      release.textContent = "Release IP";
      release.addEventListener("click", () => releaseProxy(user));
      actions.appendChild(release);
    }
    tr.append(email, role, exit, state, actions);
    usersBody.appendChild(tr);
  }
}

async function loadAdmin() {
  const [usersRes, proxyRes] = await Promise.all([
    api("/api/admin/users"),
    api("/api/admin/proxies"),
  ]);
  const data = await usersRes.json();
  if (!usersRes.ok) {
    setStatus(data.error || "Could not load users", "error");
    return;
  }
  const proxies = await proxyRes.json().catch(() => ({}));
  if (proxyRes.ok) renderProxies(proxies);
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

async function releaseProxy(user) {
  const ok = window.confirm(
    `Release ${user.proxy_label} for ${user.email}? The next run picks a different free exit. Remove a burned IP from the list first.`
  );
  if (!ok) return;
  const res = await api(`/api/admin/users/${user.id}/proxy/release`, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.error || "Could not release that exit", "error");
    return;
  }
  setStatus(`Released the exit for ${user.email}`, "ok");
  loadAdmin();
}

proxyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  proxySaveBtn.disabled = true;
  try {
    const res = await api("/api/admin/proxies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxies: document.getElementById("proxyList").value || "" }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setProxyStatus(data.error || "Could not save the proxy list", "error");
      return;
    }
    document.getElementById("proxyList").value = "";
    renderProxies(data);
    setProxyStatus("Saved. Existing clients keep their current exit until you release it.", "ok");
    loadAdmin();
  } catch (_) {
    setProxyStatus("Cannot reach Sendline server", "error");
  } finally {
    proxySaveBtn.disabled = false;
  }
});

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
