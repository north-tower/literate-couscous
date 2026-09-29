/**
 * Shared header + CTA for public doc pages (Guide, Terms, Login).
 * Uses GET /api/session (works logged in or out).
 */
(function () {
  const body = document.body;
  const page = body.dataset.navPage || "";

  const brandLink = document.getElementById("brandHomeLink");
  const navCampaign = document.getElementById("navCampaign");
  const navAdmin = document.getElementById("navAdmin");
  const headerLoginBtn = document.getElementById("headerLoginBtn");
  const headerLogoutBtn = document.getElementById("headerLogoutBtn");
  const headerUserChip = document.getElementById("headerUserChip");
  const headerUserAvatar = document.getElementById("headerUserAvatar");
  const headerUserEmail = document.getElementById("headerUserEmail");
  const ctaPrimaryGuest = document.getElementById("ctaPrimaryGuest");
  const ctaPrimaryUser = document.getElementById("ctaPrimaryUser");

  function setNavCurrent() {
    const links = document.querySelectorAll(".app-nav [data-nav]");
    links.forEach((el) => {
      const match = el.getAttribute("data-nav") === page;
      el.classList.toggle("is-active", match);
      if (match) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });
  }

  function applyGuest() {
    body.classList.remove("user-signed-in");
    if (brandLink) brandLink.setAttribute("href", brandLink.dataset.hrefGuest || "/welcome");
    if (navCampaign) navCampaign.hidden = true;
    if (navAdmin) navAdmin.hidden = true;
    if (headerLoginBtn) headerLoginBtn.hidden = page === "login";
    if (headerLogoutBtn) headerLogoutBtn.hidden = true;
    if (headerUserChip) headerUserChip.hidden = true;
    if (ctaPrimaryGuest) ctaPrimaryGuest.hidden = false;
    if (ctaPrimaryUser) ctaPrimaryUser.hidden = true;
    setNavCurrent();
  }

  function applySignedIn(user) {
    body.classList.add("user-signed-in");
    if (brandLink) brandLink.setAttribute("href", brandLink.dataset.hrefUser || "/");
    if (navCampaign) navCampaign.hidden = false;
    if (navAdmin) navAdmin.hidden = user.role !== "admin";
    if (headerLoginBtn) headerLoginBtn.hidden = true;
    if (headerLogoutBtn) headerLogoutBtn.hidden = false;
    if (headerUserChip && headerUserEmail) {
      const email = user.email || "";
      headerUserEmail.textContent = email;
      headerUserChip.hidden = !email;
      if (headerUserAvatar && email) {
        headerUserAvatar.textContent = email.charAt(0).toUpperCase();
      }
    }
    if (ctaPrimaryGuest) ctaPrimaryGuest.hidden = true;
    if (ctaPrimaryUser) ctaPrimaryUser.hidden = false;
    setNavCurrent();
  }

  async function loadSession() {
    try {
      const res = await fetch("/api/session", { credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        applyGuest();
        return;
      }
      if (data.authenticated && data.user) applySignedIn(data.user);
      else applyGuest();
    } catch {
      applyGuest();
    }
  }

  if (headerLogoutBtn) {
    headerLogoutBtn.addEventListener("click", async () => {
      headerLogoutBtn.disabled = true;
      try {
        await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
      } catch {
        /* still send to login */
      }
      window.location.href = "/login";
    });
  }

  setNavCurrent();
  loadSession();
})();
