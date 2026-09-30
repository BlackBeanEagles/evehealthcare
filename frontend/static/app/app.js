/**
 * EVE Diagnostics — single-page client for the /api/v1 backend.
 *
 * No framework and no build step: the whole UI is template strings rendered
 * into <main id="view">, with hash routing. Everything that comes from the API
 * goes through esc() before it touches innerHTML.
 */

const API = "/api/v1";
const AUTH_KEY = "eve.auth";

/**
 * Non-secret server settings, loaded from GET /config/ at boot so the rules
 * shown to a patient can't drift from the rules the API enforces. These
 * defaults only apply if that request fails; the server re-validates anyway.
 */
let CONFIG = {
  currency: "INR",
  payment_simulator: false,
  booking: { min_lead_minutes: 30, max_advance_days: 90, cancellation_cutoff_hours: 2 },
};

const PALETTE = ["#FF6B57", "#FFB23F", "#1A9E8F", "#8C7BFF", "#4FB7F0", "#FF8FB1", "#7BC47F"];
const GROUNDS = ["#FFF4E6", "#EAF7F4", "#F0EDFF", "#E7F4FC", "#FFEFF3", "#F6F1E7"];
const INK = "#1C1A2E";

const CATEGORY = {
  BLOOD: "Blood test",
  IMAGING: "Imaging",
  CARDIOLOGY: "Cardiology",
  PATHOLOGY: "Pathology",
  PACKAGE: "Health package",
  OTHER: "Other",
};

const BOOKING_STATUS = {
  PENDING: { label: "Awaiting payment", cls: "pending" },
  CONFIRMED: { label: "Confirmed", cls: "confirmed" },
  FAILED: { label: "Payment failed", cls: "failed" },
  CANCELLED: { label: "Cancelled", cls: "cancelled" },
};

const PAYMENT_STATUS = {
  PENDING: { label: "Processing", cls: "pending" },
  SUCCESS: { label: "Paid", cls: "confirmed" },
  FAILED: { label: "Declined", cls: "failed" },
};

const SOURCE_LABEL = {
  api: "You",
  payment: "Payment gateway",
  webhook: "Provider webhook",
  system: "System",
};

/** Turn a stored transition reason into something a patient would say. */
function humanReason(reason) {
  return (reason || "")
    .replace(/^Payment (\S+) success$/, "Payment $1 went through")
    .replace(/^Payment (\S+) failed$/, "Payment $1 was declined")
    .replace(/^Booking created$/, "Slot reserved")
    .replace(/^Cancelled by patient$/, "Cancelled")
    .replace(/^New payment attempt started$/, "Trying payment again");
}

const METHODS = [
  ["UPI", "UPI"],
  ["CARD", "Card"],
  ["NETBANKING", "Net banking"],
  ["WALLET", "Wallet"],
];

/* ═══════════════════════════════ utilities ═══════════════════════════════ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = $("#view");
const modal = $("#modal");

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const money = (value, currency = "INR") => {
  const n = Number(value);
  const whole = Number.isInteger(n);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(n);
};

const fmtDate = (iso) =>
  new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" }).format(new Date(iso));
const fmtTime = (iso) => new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit" }).format(new Date(iso));
const fmtDateTime = (iso) => `${fmtDate(iso)} · ${fmtTime(iso)}`;
const fmtRelative = (iso) => {
  const diff = (new Date(iso) - Date.now()) / 1000;
  const units = [["day", 86400], ["hour", 3600], ["minute", 60]];
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, secs] of units) if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
  return "just now";
};
const hhmm = (t) => (t || "").slice(0, 5);
const to12h = (t) => {
  const [h, m] = t.split(":").map(Number);
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
};
const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

const newKey = () =>
  globalThis.crypto?.randomUUID?.() ?? `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

function debounce(fn, ms = 250) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/* ═══════════════════════════ generative artwork ══════════════════════════
 * Every centre, test and booking gets a small abstract composition seeded
 * from its identifier, so the same thing always looks the same. This is the
 * whole "abstract colour" system: identity through shape, not decoration.
 */

function hashString(input) {
  let h = 1779033703 ^ input.length;
  for (let i = 0; i < input.length; i++) {
    h = Math.imul(h ^ input.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return h >>> 0;
}

function seeded(seed) {
  let a = hashString(String(seed));
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function colorFor(seed) {
  return PALETTE[hashString(String(seed)) % PALETTE.length];
}

const TONES = {
  success: { colors: ["#1A9E8F", "#7BC47F", "#4FB7F0", "#8C7BFF", "#1A9E8F"], ground: "#EAF7F4" },
  failure: { colors: ["#FF6B57", "#FFB23F", "#FF8FB1", "#FF6B57", "#FFB23F"], ground: "#FFF1EC" },
};

function art(seed, { w = 400, h = 240, busy = 1, tone = null } = {}) {
  const r = seeded(seed);
  const pick = (arr) => arr[Math.floor(r() * arr.length)];
  const between = (a, b) => a + r() * (b - a);
  const toned = tone && TONES[tone];
  const colors = toned ? [...toned.colors].sort(() => r() - 0.5) : [...PALETTE].sort(() => r() - 0.5);
  const ground = toned ? toned.ground : pick(GROUNDS);
  const s = Math.min(w, h);
  const parts = [`<rect width="${w}" height="${h}" fill="${ground}"/>`];

  // 1. a large anchoring disc, usually bleeding off an edge
  parts.push(
    `<circle cx="${between(0, w)}" cy="${between(0, h)}" r="${between(s * 0.35, s * 0.6)}" fill="${colors[0]}"/>`
  );

  // 2. a half-disc sitting on a baseline
  {
    const cx = between(w * 0.2, w * 0.85);
    const rad = between(s * 0.2, s * 0.38);
    const base = between(h * 0.55, h * 1.02);
    parts.push(`<path d="M${cx - rad} ${base} A ${rad} ${rad} 0 0 1 ${cx + rad} ${base} Z" fill="${colors[1]}"/>`);
  }

  // 3. a rotated rounded block
  {
    const bw = between(s * 0.22, s * 0.4);
    const bh = between(s * 0.22, s * 0.5);
    const x = between(0, w - bw);
    const y = between(0, h - bh);
    parts.push(
      `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="${bw * 0.22}" fill="${colors[2]}" transform="rotate(${between(-25, 25)} ${x + bw / 2} ${y + bh / 2})"/>`
    );
  }

  // 4. an arch
  if (busy > 0.5 && r() > 0.35) {
    const aw = between(s * 0.16, s * 0.28);
    const ah = aw * between(1.2, 1.8);
    const x = between(0, w - aw);
    const y = h - ah + between(0, ah * 0.3);
    parts.push(
      `<path d="M${x} ${y + ah} V ${y + aw / 2} A ${aw / 2} ${aw / 2} 0 0 1 ${x + aw} ${y + aw / 2} V ${y + ah} Z" fill="${colors[3]}"/>`
    );
  }

  // 5. a squiggle in ink (dropped at icon size, where it reads as noise)
  if (busy >= 0.5) {
    const y0 = between(h * 0.15, h * 0.85);
    const amp = between(s * 0.04, s * 0.09);
    const len = between(w * 0.35, w * 0.7);
    const x0 = between(-w * 0.05, w - len);
    const steps = 6;
    let d = `M${x0} ${y0}`;
    for (let i = 1; i <= steps; i++) {
      const x = x0 + (len / steps) * i;
      const cy = y0 + (i % 2 ? -amp : amp) * 2;
      d += ` Q ${x - len / steps / 2} ${cy} ${x} ${y0}`;
    }
    parts.push(
      `<path d="${d}" fill="none" stroke="${INK}" stroke-width="${Math.max(2.5, s * 0.018)}" stroke-linecap="round"/>`
    );
  }

  // 6. a dot grid
  if (busy >= 0.5 && r() > 0.3) {
    const gx = between(w * 0.05, w * 0.7);
    const gy = between(h * 0.05, h * 0.6);
    const gap = s * 0.055;
    const dotR = Math.max(1.6, s * 0.011);
    for (let i = 0; i < 4; i++)
      for (let j = 0; j < 4; j++)
        parts.push(`<circle cx="${gx + i * gap}" cy="${gy + j * gap}" r="${dotR}" fill="${INK}" opacity=".75"/>`);
  }

  // 7. a small accent disc with an outline ring
  if (busy >= 0.5 || r() > 0.5) {
    const cx = between(w * 0.1, w * 0.9);
    const cy = between(h * 0.1, h * 0.9);
    const rad = between(s * 0.05, s * 0.1);
    parts.push(`<circle cx="${cx}" cy="${cy}" r="${rad}" fill="${colors[4]}"/>`);
    parts.push(
      `<circle cx="${cx + rad * 0.5}" cy="${cy - rad * 0.4}" r="${rad * 1.25}" fill="none" stroke="${INK}" stroke-width="${Math.max(1.5, s * 0.008)}"/>`
    );
  }

  // 8. Large canvases (login panel, hero) get a second layer so they never
  //    read as one shape floating in an empty field.
  if (w * h > 300000) {
    const ringR = between(s * 0.18, s * 0.28);
    parts.push(
      `<circle cx="${between(w * 0.55, w * 0.95)}" cy="${between(h * 0.08, h * 0.35)}" r="${ringR}" fill="none" stroke="${colors[5 % colors.length]}" stroke-width="${ringR * 0.32}"/>`
    );
    const bw = between(s * 0.25, s * 0.4);
    const x = between(w * 0.05, w * 0.4);
    const y = between(h * 0.3, h * 0.55);
    parts.push(
      `<rect x="${x}" y="${y}" width="${bw}" height="${bw}" rx="${bw * 0.5}" fill="${colors[6 % colors.length]}" transform="rotate(${between(0, 45)} ${x + bw / 2} ${y + bw / 2})"/>`
    );
    const rad = between(s * 0.22, s * 0.34);
    const cx = between(w * 0.3, w * 0.8);
    parts.push(`<path d="M${cx - rad} 0 A ${rad} ${rad} 0 0 0 ${cx + rad} 0 Z" fill="${colors[2]}" opacity=".9"/>`);
  }

  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden="true">${parts.join("")}</svg>`;
}

const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="12" cy="13" r="9" fill="#FF6B57"/><path d="M14 30a10 10 0 0 1 16-8H14z" fill="#1A9E8F"/><rect x="17" y="3" width="11" height="11" rx="3" fill="#FFB23F"/></svg>`;

const ICON = {
  search: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`,
  pin: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
  phone: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>`,
  flask: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3"/><path d="M7 15h10"/></svg>`,
  info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>`,
  close: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>`,
  back: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>`,
  arrow: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`,
  shield: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z"/></svg>`,
  compass: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/></svg>`,
  calendar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M8 3v4M16 3v4M3.5 10h17"/></svg>`,
  card: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5.5" width="18" height="13" rx="3"/><path d="M3 10h18M7 15h4"/></svg>`,
};

/* ═════════════════════════════════ auth ══════════════════════════════════ */

function readStoredAuth() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY)) || {};
  } catch {
    return {};
  }
}

const auth = readStoredAuth();

/** Adopt whatever another tab last saved (it may have rotated the tokens). */
function syncAuthFromStorage() {
  const stored = readStoredAuth();
  for (const key of Object.keys(auth)) delete auth[key];
  Object.assign(auth, stored);
}

// Keep tabs in step: a login, token rotation or logout in one tab applies to all.
window.addEventListener("storage", (e) => {
  if (e.key !== AUTH_KEY && e.key !== null) return;
  const wasIn = Boolean(auth.access);
  syncAuthFromStorage();
  if (wasIn !== Boolean(auth.access)) go(auth.access ? "#/" : "#/login");
});

function saveAuth(patch) {
  Object.assign(auth, patch);
  try {
    localStorage.setItem(AUTH_KEY, JSON.stringify(auth));
  } catch {
    /* private mode: the session simply won't survive a reload */
  }
}

function clearAuth() {
  for (const key of Object.keys(auth)) delete auth[key];
  try {
    localStorage.removeItem(AUTH_KEY);
  } catch {
    /* ignore */
  }
}

/* ═══════════════════════════════ API client ══════════════════════════════ */

class ApiError extends Error {
  constructor(status, body) {
    const error = body?.error ?? {};
    super(error.message || (status >= 500 ? "The server hit a problem. Please try again." : `Request failed (${status}).`));
    this.status = status;
    this.code = error.code;
    this.details = error.details;
  }
}

let refreshInFlight = null;

function refreshAccess() {
  syncAuthFromStorage(); // another tab may already hold newer tokens
  if (!auth.refresh) return Promise.resolve(false);
  refreshInFlight ??= fetch(`${API}/auth/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh: auth.refresh }),
  })
    .then(async (res) => {
      if (!res.ok) return false;
      const data = await res.json();
      saveAuth({ access: data.access, refresh: data.refresh ?? auth.refresh });
      return true;
    })
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

async function api(path, { method = "GET", body, headers = {}, authed = true } = {}, isRetry = false) {
  const h = { Accept: "application/json", ...headers };
  if (body !== undefined) h["Content-Type"] = "application/json";
  if (authed && auth.access) h.Authorization = `Bearer ${auth.access}`;

  let res;
  try {
    res = await fetch(API + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, { error: { message: "Can't reach the server. Check your connection." } });
  }

  // An expired access token is refreshed once, transparently. If another tab
  // refreshed in the meantime, just retry with the token it saved.
  if (res.status === 401 && authed && !isRetry) {
    const sent = auth.access;
    syncAuthFromStorage();
    if (auth.access && auth.access !== sent) return api(path, { method, body, headers, authed }, true);
  }
  if (res.status === 401 && authed && !isRetry && auth.refresh) {
    if (await refreshAccess()) return api(path, { method, body, headers, authed }, true);
    clearAuth();
    go("#/login");
    throw new ApiError(401, { error: { message: "Your session has expired. Please log in again." } });
  }

  if (res.status === 204 || res.status === 205) return null;
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

// Tiny per-session cache for catalogue reads the UI repeats (centre hours).
const memo = new Map();
function cached(key, loader) {
  if (!memo.has(key)) memo.set(key, loader().catch((e) => { memo.delete(key); throw e; }));
  return memo.get(key);
}

/* ═════════════════════════════ UI primitives ═════════════════════════════ */

function toast(message, kind = "info") {
  const host = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  host.append(el);
  // A modal <dialog> sits in the browser's top layer, above everything else.
  // Re-showing the popover puts the toasts above it (no-op where unsupported).
  if (host.showPopover) {
    try {
      if (host.matches(":popover-open")) host.hidePopover();
      host.showPopover();
    } catch {
      /* older browser: toasts simply render in normal flow */
    }
  }
  setTimeout(() => {
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 220);
  }, kind === "error" ? 5200 : 3600);
}

const chip = (status, map = BOOKING_STATUS, extra = "") => {
  const meta = map[status] ?? { label: status, cls: "" };
  return `<span class="chip ${meta.cls} ${extra}">${esc(meta.label)}</span>`;
};

const skeletons = (n, cls = "card-sk") =>
  Array.from({ length: n }, () => `<div class="skeleton ${cls}" aria-hidden="true"></div>`).join("");

const empty = (seed, title, body, action = "") => `
  <div class="empty">
    <div class="empty-art">${art(seed, { w: 200, h: 200 })}</div>
    <h3>${esc(title)}</h3>
    <p>${esc(body)}</p>
    ${action}
  </div>`;

const errorState = (err) => `
  <div class="card pad empty">
    <div class="empty-art">${art("error-state", { w: 200, h: 200 })}</div>
    <h3>Something went wrong</h3>
    <p>${esc(err?.message || "Unexpected error.")}</p>
    <button class="btn primary" data-action="retry">Try again</button>
  </div>`;

function showFieldErrors(form, err) {
  $$(".field .error", form).forEach((el) => el.remove());
  $$("[aria-invalid]", form).forEach((el) => el.removeAttribute("aria-invalid"));
  const banner = $(".form-error", form);
  const details = err?.details && typeof err.details === "object" ? err.details : {};
  let placed = false;
  for (const [field, messages] of Object.entries(details)) {
    const input = form.elements[field];
    if (!input || !input.closest) continue;
    const text = Array.isArray(messages) ? messages.join(" ") : String(messages);
    input.setAttribute("aria-invalid", "true");
    const note = document.createElement("span");
    note.className = "error";
    note.id = `${field}-error`;
    note.textContent = text;
    input.setAttribute("aria-describedby", note.id);
    input.closest(".field").append(note);
    placed = true;
  }
  if (banner) {
    banner.hidden = placed;
    banner.textContent = placed ? "" : err?.message || "Something went wrong.";
  }
  $("[aria-invalid='true']", form)?.focus();
}

function busy(button, on, label) {
  if (!button) return;
  if (on) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner" aria-hidden="true"></span>${esc(label || "Working…")}`;
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

/* ────────────────────────────────── modal ───────────────────────────────── */

let onModalClose = null;

function openModal(html, { onClose = null } = {}) {
  modal.innerHTML = `<div class="modal-card">${html}</div>`;
  onModalClose = onClose;
  if (!modal.open) modal.showModal();
  requestAnimationFrame(() => $("[autofocus]", modal)?.focus());
  return $(".modal-card", modal);
}

/**
 * Every close goes through here and runs its follow-up synchronously.
 * Relying on the dialog's async `close` event alone is not safe: some embedded
 * webviews and background tabs never dispatch it, which left "View booking"
 * closing the dialog without navigating anywhere.
 */
function closeModal() {
  if (!modal.open) return;
  modal.close();
  finishClose();
}

function finishClose() {
  const cb = onModalClose;
  onModalClose = null;
  modal.innerHTML = "";
  cb?.();
}

// Esc: take it over so it follows the same path as the close button.
modal.addEventListener("cancel", (e) => {
  e.preventDefault();
  closeModal();
});
// Anything else that closes the dialog (e.g. a <form method="dialog">).
// The event can arrive late: if a new dialog has been opened since, it is
// stale and must not wipe the new content or run the new dialog's callback.
modal.addEventListener("close", () => {
  if (!modal.open && modal.innerHTML) finishClose();
});
modal.addEventListener("click", (e) => {
  if (e.target === modal) closeModal(); // backdrop
});

const modalHead = (title, sub = "") => `
  <div class="modal-head">
    <div>
      ${sub ? `<p class="eyebrow">${esc(sub)}</p>` : ""}
      <h2 id="modal-title">${esc(title)}</h2>
    </div>
    <button class="icon-btn" type="button" data-action="close-modal" aria-label="Close">${ICON.close}</button>
  </div>`;

/* ═════════════════════════════════ router ════════════════════════════════ */

const routes = [
  [/^#\/login$/, loginView, { public: true }],
  [/^#\/signup$/, signupView, { public: true }],
  [/^#\/?$/, exploreView],
  [/^#\/tests$/, testsView],
  [/^#\/tests\/([^/]+)$/, testDetailView],
  [/^#\/centres\/(\d+)$/, centreView],
  [/^#\/bookings$/, bookingsView],
  [/^#\/bookings\/([A-Za-z0-9-]+)$/, bookingDetailView],
  [/^#\/payments$/, paymentsView],
];

let renderToken = 0;
let lastPath = null;

function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

/** Only the most recent navigation is allowed to write to the page. */
function paint(token, html) {
  if (token !== renderToken) return false;
  view.innerHTML = html;
  return true;
}

async function render() {
  const [path, qs = ""] = (location.hash || "#/").split("?");
  const query = new URLSearchParams(qs);
  const match = routes.find(([re]) => re.test(path));

  if (modal.open) {
    onModalClose = null;
    closeModal();
  }

  if (!match) {
    renderTopbar(path);
    view.innerHTML = empty("lost", "Nothing here", "That page doesn't exist.", `<a class="btn primary" href="#/">Go home</a>`);
    return;
  }

  const [re, handler, opts = {}] = match;
  if (!opts.public && !auth.access) return go("#/login");
  if (opts.public && auth.access) return go("#/");

  renderTopbar(path);
  const token = ++renderToken;
  const params = path.match(re).slice(1).map(decodeURIComponent);

  try {
    await handler({ params, query, token });
  } catch (err) {
    if (err.status === 401) return;
    paint(token, errorState(err));
  }
  // Re-rendering the same page (e.g. after a modal closes) keeps the scroll
  // position; navigating somewhere new starts at the top.
  if (token === renderToken && path !== lastPath) window.scrollTo(0, 0);
  lastPath = path;
}

function renderTopbar(path) {
  const bar = $("#topbar");
  if (!auth.access) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  const user = auth.user ?? {};
  const first = (user.full_name || user.email || "You").split(" ")[0];
  const initials = (user.full_name || user.email || "?")
    .split(/\s+/)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  // [href, label, short label for the phone tab bar, icon, is-active]
  const links = [
    ["#/", "Explore", "Explore", ICON.compass, (p) => p === "#/" || p === "#" || p.startsWith("#/centres")],
    ["#/tests", "Tests", "Tests", ICON.flask, (p) => p.startsWith("#/tests")],
    ["#/bookings", "My bookings", "Bookings", ICON.calendar, (p) => p.startsWith("#/bookings")],
    ["#/payments", "Payments", "Payments", ICON.card, (p) => p.startsWith("#/payments")],
  ];
  bar.hidden = false;
  // On phones .nav becomes a bottom tab bar (see styles.css), so all four
  // destinations stay visible instead of scrolling off the edge.
  bar.innerHTML = `
    <a class="brand" href="#/">${LOGO}<span>EVE <em>Diagnostics</em></span></a>
    <nav class="nav" aria-label="Main">
      ${links
        .map(([href, label, short, icon, active]) => {
          const on = active(path);
          return `<a href="${href}" class="${on ? "active" : ""}" ${on ? 'aria-current="page"' : ""}>
            <span class="nav-icon" aria-hidden="true">${icon}</span><span class="nav-full">${label}</span><span class="nav-short" aria-hidden="true">${short}</span></a>`;
        })
        .join("")}
    </nav>
    <div class="me">
      <span class="avatar" style="background:${colorFor(user.email || "me")}" aria-hidden="true">${esc(initials)}</span>
      <span class="me-name">${esc(first)}</span>
      <button class="btn ghost sm" data-action="logout">Log out</button>
    </div>`;
}

/* ═════════════════════════════ global actions ════════════════════════════ */

document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const action = el.dataset.action;

  if (action === "close-modal") return closeModal();
  if (action === "retry") return render();

  if (action === "logout") {
    try {
      if (auth.refresh) await api("/auth/logout/", { method: "POST", body: { refresh: auth.refresh } });
    } catch {
      /* logging out locally is what matters */
    }
    clearAuth();
    memo.clear();
    toast("Logged out. See you soon.");
    return go("#/login");
  }

  if (action === "book") {
    const d = el.dataset;
    return openBookingModal({
      offeringId: Number(d.offering),
      price: d.price,
      centre: { id: Number(d.centre), name: d.centreName },
      test: { code: d.testCode, name: d.testName },
    });
  }

  if (action === "pay") {
    const booking = await api(`/bookings/${encodeURIComponent(el.dataset.ref)}/`);
    return openPayModal(booking, { onClose: render });
  }

  if (action === "cancel") return openCancelModal(el.dataset.ref);

  if (action === "simulate") return runSimulation(el);
});

window.addEventListener("hashchange", render);

/* ═══════════════════════════════ auth views ══════════════════════════════ */

function authLayout(inner, seed) {
  return `
    <section class="auth">
      <div class="auth-art" aria-hidden="true">
        ${art(seed, { w: 600, h: 820 })}
        <div class="tag">
          <h2>Diagnostics, booked in a minute.</h2>
          <p>Compare what each centre charges, pick a slot, pay. That's it.</p>
        </div>
      </div>
      <div class="auth-form">
        <a class="brand" href="#/login">${LOGO}<span>EVE <em>Diagnostics</em></span></a>
        ${inner}
      </div>
    </section>`;
}

async function loginView({ token }) {
  paint(
    token,
    authLayout(
      `
      <h1>Welcome back.</h1>
      <p class="lede" style="margin-bottom:28px">Log in to book and manage your tests.</p>
      <form class="form-grid" id="login-form" novalidate>
        <div class="demo">
          <span>Trying it out? Use the seeded demo patient.</span>
          <button class="btn sm" type="button" id="use-demo">Fill in</button>
        </div>
        <p class="form-error" role="alert" hidden></p>
        <div class="field">
          <label for="email">Email</label>
          <input class="input" id="email" name="email" type="email" autocomplete="email" required autofocus>
        </div>
        <div class="field">
          <label for="password">Password</label>
          <input class="input" id="password" name="password" type="password" autocomplete="current-password" required>
        </div>
        <button class="btn primary block" type="submit">Log in</button>
        <p class="switch">New here? <a href="#/signup">Create an account</a></p>
      </form>`,
      "eve-login"
    )
  );

  const form = $("#login-form");
  $("#email").focus();
  $("#use-demo").addEventListener("click", () => {
    form.email.value = "patient@example.com";
    form.password.value = "DemoPass123!";
    form.querySelector("[type=submit]").focus();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = form.querySelector("[type=submit]");
    busy(button, true, "Logging in…");
    try {
      const data = await api("/auth/login/", {
        method: "POST",
        authed: false,
        body: { email: form.email.value.trim(), password: form.password.value },
      });
      saveAuth({ access: data.access, refresh: data.refresh, user: data.user });
      toast(`Hi ${data.user.full_name.split(" ")[0]} 👋`, "success");
      go("#/");
    } catch (err) {
      busy(button, false);
      const message = err.status === 401 ? "That email and password don't match an account." : err.message;
      showFieldErrors(form, { message, details: err.status === 400 ? err.details : null });
    }
  });
}

async function signupView({ token }) {
  paint(
    token,
    authLayout(
      `
      <h1>Create your account.</h1>
      <p class="lede" style="margin-bottom:28px">It takes less time than finding your old reports.</p>
      <form class="form-grid" id="signup-form" novalidate>
        <p class="form-error" role="alert" hidden></p>
        <div class="field">
          <label for="full_name">Full name</label>
          <input class="input" id="full_name" name="full_name" autocomplete="name" required autofocus>
        </div>
        <div class="form-row">
          <div class="field">
            <label for="email">Email</label>
            <input class="input" id="email" name="email" type="email" autocomplete="email" required>
          </div>
          <div class="field">
            <label for="phone">Phone <span class="muted">(optional)</span></label>
            <input class="input" id="phone" name="phone" type="tel" autocomplete="tel" placeholder="+91 98765 43210">
          </div>
        </div>
        <div class="form-row">
          <div class="field">
            <label for="password">Password</label>
            <input class="input" id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>
            <span class="hint">8+ characters, not too common.</span>
          </div>
          <div class="field">
            <label for="password_confirm">Confirm password</label>
            <input class="input" id="password_confirm" name="password_confirm" type="password" autocomplete="new-password" required>
          </div>
        </div>
        <button class="btn primary block" type="submit">Create account</button>
        <p class="switch">Already registered? <a href="#/login">Log in</a></p>
      </form>`,
      "eve-signup"
    )
  );

  const form = $("#signup-form");
  $("#full_name").focus();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = form.querySelector("[type=submit]");
    const body = {
      full_name: form.full_name.value.trim(),
      email: form.email.value.trim(),
      password: form.password.value,
      password_confirm: form.password_confirm.value,
    };
    const phone = form.phone.value.replace(/[\s-]/g, "");
    if (phone) body.phone = phone;

    busy(button, true, "Creating account…");
    try {
      await api("/auth/signup/", { method: "POST", authed: false, body });
      const data = await api("/auth/login/", {
        method: "POST",
        authed: false,
        body: { email: body.email, password: body.password },
      });
      saveAuth({ access: data.access, refresh: data.refresh, user: data.user });
      toast("Account created. Welcome to EVE.", "success");
      go("#/");
    } catch (err) {
      busy(button, false);
      showFieldErrors(form, err);
    }
  });
}

/* ═══════════════════════════════ catalogue ═══════════════════════════════ */

function centreCard(c) {
  return `
    <a class="card card-link" href="#/centres/${c.id}">
      <div class="card-art">${art(`centre-${c.id}`, { w: 480, h: 210 })}</div>
      <div class="card-body">
        <h3>${esc(c.name)}</h3>
        <p class="muted">${esc(c.address_line)}</p>
        <div class="meta">
          <span>${ICON.pin}${esc(c.city)}</span>
          <span>${ICON.clock}${esc(to12h(hhmm(c.opens_at)))} – ${esc(to12h(hhmm(c.closes_at)))}</span>
        </div>
        <div class="card-foot">
          <span class="chip lilac plain">${c.tests_offered} tests</span>
          <span class="muted" style="display:inline-flex;align-items:center;gap:6px;font-weight:600">View prices ${ICON.arrow}</span>
        </div>
      </div>
    </a>`;
}

async function exploreView({ token }) {
  const name = (auth.user?.full_name || "").split(" ")[0];
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  paint(
    token,
    `
    <section class="hero">
      <div class="stack">
        <p class="eyebrow">${esc(greeting)}${name ? `, ${esc(name)}` : ""}</p>
        <h1>Find a test.<br>Pick a centre.<br>Book in a minute.</h1>
        <p class="lede">Every centre sets its own price. We show you all of them, cheapest first.</p>
        <form class="search" id="hero-search" role="search">
          ${ICON.search}
          <label class="sr-only" for="hero-q">Search tests</label>
          <input id="hero-q" name="q" placeholder="Search a test — try “CBC” or “thyroid”" autocomplete="off">
          <button class="btn primary" type="submit">Search</button>
        </form>
        <div class="quick" id="quick-tests" aria-label="Popular tests"></div>
      </div>
      <div class="hero-art" aria-hidden="true">${art("eve-hero", { w: 520, h: 520 })}</div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">Partner centres</p>
          <h2>Where you can go</h2>
        </div>
        <div class="choices" id="city-filter" role="radiogroup" aria-label="Filter by city"></div>
      </div>
      <div class="grid" id="centre-grid">${skeletons(4)}</div>
    </section>`
  );

  $("#hero-search").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = e.target.q.value.trim();
    go(q ? `#/tests?q=${encodeURIComponent(q)}` : "#/tests");
  });

  const [centres, tests] = await Promise.all([
    api("/centres/?page_size=100"),
    api("/tests/?page_size=6"),
  ]);
  if (token !== renderToken) return;

  $("#quick-tests").innerHTML =
    `<span class="muted" style="font-size:.88rem;font-weight:600">Popular:</span>` +
    tests.results.map((t) => `<a href="#/tests/${encodeURIComponent(t.code)}">${esc(t.name)}</a>`).join("");

  const cities = [...new Set(centres.results.map((c) => c.city))].sort();
  const filter = $("#city-filter");
  filter.innerHTML = ["All", ...cities]
    .map(
      (city, i) => `
      <label class="choice"><input type="radio" name="city" value="${esc(city)}" ${i === 0 ? "checked" : ""}><span>${esc(city)}</span></label>`
    )
    .join("");

  const drawCentres = (city) => {
    const list = city === "All" ? centres.results : centres.results.filter((c) => c.city === city);
    $("#centre-grid").innerHTML = list.length
      ? list.map(centreCard).join("")
      : empty("no-centres", "No centres yet", "There are no partner centres in this city.");
  };
  filter.addEventListener("change", (e) => drawCentres(e.target.value));
  drawCentres("All");
}

function testCard(t) {
  return `
    <a class="card card-link test-card" href="#/tests/${encodeURIComponent(t.code)}">
      <div class="thumb">${art(`test-${t.code}`, { w: 160, h: 160, busy: 0.6 })}</div>
      <div>
        <h3>${esc(t.name)}</h3>
        <div class="meta" style="margin-top:6px">
          <span class="chip sky plain">${esc(CATEGORY[t.category] ?? t.category)}</span>
          <span>${ICON.clock}Report in ${t.report_eta_hours}h</span>
        </div>
        ${t.description ? `<p>${esc(t.description)}</p>` : ""}
      </div>
    </a>`;
}

async function testsView({ query, token }) {
  const initialQ = query.get("q") || "";
  const initialCat = query.get("category") || "";

  paint(
    token,
    `
    <div class="section-head" style="margin-bottom:28px">
      <div class="stack" style="--gap:10px">
        <p class="eyebrow">Test catalogue</p>
        <h1>What do you need checked?</h1>
      </div>
    </div>
    <form class="search" id="test-search" role="search" style="margin-bottom:22px">
      ${ICON.search}
      <label class="sr-only" for="test-q">Search tests</label>
      <input id="test-q" name="q" value="${esc(initialQ)}" placeholder="Name, code or what it checks" autocomplete="off">
    </form>
    <fieldset class="choices" id="cat-filter" style="margin-bottom:28px">
      <legend class="sr-only">Category</legend>
      ${[["", "All"], ...Object.entries(CATEGORY)]
        .map(
          ([value, label]) =>
            `<label class="choice"><input type="radio" name="category" value="${value}" ${value === initialCat ? "checked" : ""}><span>${esc(label)}</span></label>`
        )
        .join("")}
    </fieldset>
    <div class="grid" id="test-results" aria-live="polite">${skeletons(6, "row-sk")}</div>`
  );

  const form = $("#test-search");
  let seq = 0;
  const load = async () => {
    const q = form.q.value.trim();
    const category = $("input[name=category]:checked")?.value || "";
    const params = new URLSearchParams({ page_size: "100" });
    if (q) params.set("search", q);
    if (category) params.set("category", category);
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    if (category) qs.set("category", category);
    history.replaceState(null, "", `#/tests${qs.toString() ? `?${qs}` : ""}`);

    const mine = ++seq;
    const data = await api(`/tests/?${params}`);
    if (mine !== seq || token !== renderToken) return;
    $("#test-results").innerHTML = data.results.length
      ? data.results.map(testCard).join("")
      : empty(`none-${q}`, "No tests match", `Nothing matches “${q}”. Try a shorter word or another category.`);
  };

  form.addEventListener("submit", (e) => e.preventDefault());
  form.q.addEventListener("input", debounce(load, 220));
  $("#cat-filter").addEventListener("change", load);
  const input = $("#test-q");
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  await load();
}

async function testDetailView({ params: [code], token }) {
  paint(token, `<a class="back" href="#/tests">${ICON.back} All tests</a>${skeletons(1, "card-sk")}<div class="offer-list" style="margin-top:24px">${skeletons(3, "row-sk")}</div>`);

  const [offers, found] = await Promise.all([
    api(`/offerings/?test=${encodeURIComponent(code)}&ordering=price&page_size=100`),
    api(`/tests/?search=${encodeURIComponent(code)}&page_size=100`),
  ]);
  const test =
    found.results.find((t) => t.code.toUpperCase() === code.toUpperCase()) ?? offers.results[0]?.test;
  if (!test) {
    paint(token, `<a class="back" href="#/tests">${ICON.back} All tests</a>${empty("missing-test", "Test not found", "We couldn't find that test in the catalogue.")}`);
    return;
  }

  const rows = offers.results;
  const prices = rows.map((o) => Number(o.price));
  const min = Math.min(...prices);
  const max = Math.max(...prices);

  paint(
    token,
    `
    <a class="back" href="#/tests">${ICON.back} All tests</a>
    <article class="banner">
      <div class="banner-art">${art(`test-${test.code}`, { w: 1200, h: 260 })}</div>
      <div class="banner-body">
        <div class="banner-top">
          <div class="stack" style="--gap:10px">
            <div class="actions">
              <span class="chip sky plain">${esc(CATEGORY[test.category] ?? test.category)}</span>
              <span class="code">${esc(test.code)}</span>
            </div>
            <h1 style="font-size:clamp(1.8rem,4vw,2.6rem)">${esc(test.name)}</h1>
            ${test.description ? `<p class="lede">${esc(test.description)}</p>` : ""}
          </div>
          ${
            rows.length
              ? `<div class="price-block"><p class="eyebrow">From</p><p class="price xl">${money(min)}</p><p class="muted">at ${rows.length} centre${rows.length > 1 ? "s" : ""}</p></div>`
              : ""
          }
        </div>
        <div class="meta"><span>${ICON.clock}Report in about ${test.report_eta_hours} hours</span></div>
        ${test.preparation ? `<div class="callout">${ICON.info}<span><b>Before your test:</b> ${esc(test.preparation)}</span></div>` : ""}
      </div>
    </article>

    <section class="section" style="margin-top:36px">
      <div class="section-head">
        <div>
          <p class="eyebrow">Compare prices</p>
          <h2>Where to get it</h2>
        </div>
        ${rows.length > 1 ? `<p class="muted">Prices range ${money(min)} – ${money(max)}. Same test, different centre.</p>` : ""}
      </div>
      <div class="offer-list">
        ${
          rows.length
            ? rows
                .map((o, i) => {
                  const pct = max === min ? 100 : 18 + ((Number(o.price) - min) / (max - min)) * 82;
                  const saving = max - Number(o.price);
                  return `
            <div class="card offer ${i === 0 && rows.length > 1 ? "best" : ""}">
              <div class="thumb">${art(`centre-${o.centre.id}`, { w: 120, h: 120, busy: 0.4 })}</div>
              <div>
                <h3><a href="#/centres/${o.centre.id}">${esc(o.centre.name)}</a></h3>
                <div class="meta" style="margin-top:4px">
                  <span>${ICON.pin}${esc(o.centre.city)}, ${esc(o.centre.state)}</span>
                  ${i === 0 && rows.length > 1 ? `<span class="chip confirmed">Cheapest</span>` : ""}
                </div>
              </div>
              <div class="price-col">
                <div>
                  <div class="price">${money(o.price)}</div>
                  ${saving > 0 && i === 0 ? `<div class="save">Save ${money(saving)}</div>` : ""}
                </div>
                <div class="bar" aria-hidden="true"><i style="width:${pct}%"></i></div>
              </div>
              <button class="btn ${i === 0 ? "primary" : ""}" data-action="book"
                data-offering="${o.id}" data-price="${esc(o.price)}"
                data-centre="${o.centre.id}" data-centre-name="${esc(o.centre.name)}"
                data-test-code="${esc(o.test.code)}" data-test-name="${esc(o.test.name)}"
                ${o.is_bookable ? "" : 'disabled aria-disabled="true"'}>Book</button>
            </div>`;
                })
                .join("")
            : empty("no-offers", "Not offered right now", "No partner centre offers this test at the moment.")
        }
      </div>
    </section>`
  );
}

function isOpenNow(c) {
  const now = new Date();
  const t = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  return hhmm(c.opens_at) <= t && t < hhmm(c.closes_at);
}

async function centreView({ params: [id], token }) {
  paint(token, `<a class="back" href="#/">${ICON.back} All centres</a>${skeletons(1, "card-sk")}`);
  const c = await api(`/centres/${id}/`);
  memo.set(`centre-${c.id}`, Promise.resolve(c));
  const offerings = c.offerings.filter((o) => o.is_available).sort((a, b) => a.test.name.localeCompare(b.test.name));
  const open = isOpenNow(c);

  paint(
    token,
    `
    <a class="back" href="#/">${ICON.back} All centres</a>
    <article class="banner">
      <div class="banner-art">${art(`centre-${c.id}`, { w: 1200, h: 260 })}</div>
      <div class="banner-body">
        <div class="banner-top">
          <div class="stack" style="--gap:10px">
            <span class="chip ${open ? "confirmed" : "cancelled"}">${open ? "Open now" : "Closed now"}</span>
            <h1 style="font-size:clamp(1.8rem,4vw,2.6rem)">${esc(c.name)}</h1>
            <p class="lede">${esc(c.location)}</p>
          </div>
        </div>
        <div class="meta" style="font-size:.95rem">
          <span>${ICON.clock}${esc(to12h(hhmm(c.opens_at)))} – ${esc(to12h(hhmm(c.closes_at)))}, every day</span>
          ${c.phone ? `<span>${ICON.phone}<a href="tel:${esc(c.phone)}">${esc(c.phone)}</a></span>` : ""}
          <span>${ICON.flask}${offerings.length} tests available</span>
        </div>
      </div>
    </article>

    <section class="section" style="margin-top:36px">
      <div class="section-head">
        <div>
          <p class="eyebrow">Price list</p>
          <h2>Tests at this centre</h2>
        </div>
      </div>
      ${
        offerings.length
          ? `<div class="card table-wrap">
        <table class="list">
          <thead><tr><th>Test</th><th>Category</th><th>Report</th><th class="num">Price</th><th><span class="sr-only">Book</span></th></tr></thead>
          <tbody>
            ${offerings
              .map(
                (o) => `
            <tr>
              <td><a class="t-name" href="#/tests/${encodeURIComponent(o.test.code)}">${esc(o.test.name)}</a><br><span class="code">${esc(o.test.code)}</span></td>
              <td><span class="chip sky plain">${esc(CATEGORY[o.test.category] ?? o.test.category)}</span></td>
              <td class="muted">${o.test.report_eta_hours}h</td>
              <td class="num price" style="font-size:1.1rem">${money(o.price)}</td>
              <td class="num">
                <button class="btn sm primary" data-action="book"
                  data-offering="${o.id}" data-price="${esc(o.price)}"
                  data-centre="${c.id}" data-centre-name="${esc(c.name)}"
                  data-test-code="${esc(o.test.code)}" data-test-name="${esc(o.test.name)}"
                  ${o.is_bookable ? "" : 'disabled aria-disabled="true"'}>Book</button>
              </td>
            </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>`
          : empty("no-tests-here", "No tests listed", "This centre isn't taking bookings right now.")
      }
    </section>`
  );
}

/* ═════════════════════════════ booking flow ══════════════════════════════ */

function slotsFor(opens, closes) {
  const [oh, om] = hhmm(opens).split(":").map(Number);
  const [ch, cm] = hhmm(closes).split(":").map(Number);
  const out = [];
  for (let m = oh * 60 + om; m < ch * 60 + cm; m += 30) out.push(`${pad(Math.floor(m / 60))}:${pad(m % 60)}`);
  return out;
}

const tooSoon = (day, time) =>
  new Date(`${day}T${time}:00`).getTime() < Date.now() + CONFIG.booking.min_lead_minutes * 60000;

async function openBookingModal({ offeringId, price, centre, test }) {
  openModal(`${modalHead("Book your test", "New booking")}<div class="modal-body" style="place-items:center;min-height:200px"><span class="spinner"></span></div>`);

  let full;
  try {
    full = await cached(`centre-${centre.id}`, () => api(`/centres/${centre.id}/`));
  } catch (err) {
    toast(err.message, "error");
    return closeModal();
  }
  if (!modal.open) return;

  const allSlots = slotsFor(full.opens_at, full.closes_at);
  const days = Array.from({ length: 14 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() + i);
    return d;
  })
    .map((d) => ({ date: d, iso: isoDay(d) }))
    .filter((d) => allSlots.some((t) => !tooSoon(d.iso, t)));

  const card = openModal(`
    ${modalHead("Book your test", "New booking")}
    <form id="book-form" novalidate style="display:contents">
      <div class="modal-body">
        <div class="summary">
          <div class="thumb">${art(`test-${test.code}`, { w: 128, h: 128, busy: 0.5 })}</div>
          <div>
            <h3>${esc(test.name)}</h3>
            <p class="muted" style="font-size:.9rem">${esc(full.name)}</p>
          </div>
          <div class="price">${money(price)}</div>
        </div>

        <fieldset class="choices scroll" id="days">
          <legend>Pick a day</legend>
          ${days
            .map(
              (d, i) => `
            <label class="choice tile">
              <input type="radio" name="day" value="${d.iso}" ${i === 0 ? "checked" : ""}>
              <span><small>${i === 0 && d.iso === isoDay(new Date()) ? "Today" : d.date.toLocaleDateString("en-IN", { weekday: "short" })}</small><b>${d.date.getDate()}</b><small>${d.date.toLocaleDateString("en-IN", { month: "short" })}</small></span>
            </label>`
            )
            .join("")}
        </fieldset>

        <fieldset class="choices" style="display:block">
          <legend>Pick a time <span class="muted" style="font-weight:500">· open ${esc(to12h(hhmm(full.opens_at)))} – ${esc(to12h(hhmm(full.closes_at)))}</span></legend>
          <div id="slots"></div>
        </fieldset>

        <div class="field">
          <label for="notes">Notes for the lab <span class="muted">(optional)</span></label>
          <textarea class="input" id="notes" name="patient_notes" maxlength="2000" placeholder="e.g. fasting since 10 pm, prefer a female phlebotomist"></textarea>
        </div>
        <p class="form-error" role="alert" hidden></p>
      </div>
      <div class="modal-foot">
        <span class="muted" id="book-summary">Choose a time</span>
        <button class="btn primary" type="submit" id="book-submit" disabled>Book &amp; continue to payment</button>
      </div>
    </form>`);

  const form = $("#book-form", card);
  const submit = $("#book-submit", card);

  const drawSlots = () => {
    const day = form.day.value;
    const groups = [
      ["Morning", (t) => t < "12:00"],
      ["Afternoon", (t) => t >= "12:00" && t < "17:00"],
      ["Evening", (t) => t >= "17:00"],
    ];
    $("#slots", card).innerHTML = groups
      .map(([label, test]) => {
        const times = allSlots.filter(test).filter((t) => !tooSoon(day, t));
        if (!times.length) return "";
        return `
          <div class="slot-group">
            <span class="label">${label}</span>
            <div class="choices">
              ${times
                .map((t) => `<label class="choice"><input type="radio" name="time" value="${t}"><span>${to12h(t)}</span></label>`)
                .join("")}
            </div>
          </div>`;
      })
      .join("");
    submit.disabled = true;
    $("#book-summary", card).textContent = "Choose a time";
  };

  form.addEventListener("change", (e) => {
    if (e.target.name === "day") drawSlots();
    if (e.target.name === "time") {
      const d = new Date(`${form.day.value}T${e.target.value}:00`);
      $("#book-summary", card).textContent = `${d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short" })}, ${to12h(e.target.value)}`;
      submit.disabled = false;
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const time = form.querySelector("input[name=time]:checked")?.value;
    if (!time) return;
    busy(submit, true, "Booking…");
    try {
      // A naive local timestamp: the server reads it in the centre's timezone,
      // which is exactly what a wall-clock slot at a centre means.
      const booking = await api("/bookings/", {
        method: "POST",
        body: {
          offering: offeringId,
          scheduled_for: `${form.day.value}T${time}:00`,
          patient_notes: form.patient_notes.value.trim(),
        },
      });
      toast(`Booked · ${booking.reference}`, "success");
      openPayModal(booking, { onClose: () => go(`#/bookings/${booking.reference}`) });
    } catch (err) {
      busy(submit, false);
      showFieldErrors(form, { message: err.message });
    }
  });

  drawSlots();
}

function openPayModal(booking, { onClose } = {}) {
  let key = newKey();

  const drawForm = (error = "") => {
    const card = openModal(
      `
      ${modalHead("Pay for your booking", booking.reference)}
      <form id="pay-form" novalidate style="display:contents">
        <div class="modal-body">
          <div class="summary">
            <div class="thumb">${art(`test-${booking.test.code}`, { w: 128, h: 128, busy: 0.5 })}</div>
            <div>
              <h3>${esc(booking.test.name)}</h3>
              <p class="muted" style="font-size:.9rem">${esc(booking.centre.name)} · ${esc(fmtDateTime(booking.scheduled_for))}</p>
            </div>
            <div class="price">${money(booking.amount, booking.currency)}</div>
          </div>

          <fieldset class="choices">
            <legend>Pay with</legend>
            ${METHODS.map(([v, l], i) => `<label class="choice"><input type="radio" name="method" value="${v}" ${i === 0 ? "checked" : ""}><span>${l}</span></label>`).join("")}
          </fieldset>

          <fieldset class="choices">
            <legend>Simulated outcome</legend>
            <label class="choice"><input type="radio" name="outcome" value="success" checked><span>Approve</span></label>
            <label class="choice"><input type="radio" name="outcome" value="failure"><span>Decline</span></label>
            <label class="choice"><input type="radio" name="outcome" value="random"><span>Let the gateway decide</span></label>
            ${CONFIG.payment_simulator ? `<label class="choice"><input type="radio" name="outcome" value="pending"><span>Approve later, by webhook</span></label>` : ""}
          </fieldset>

          <p class="sim-note">${ICON.shield}This is a simulated gateway. No real money moves.</p>
          ${error ? `<p class="form-error" role="alert">${esc(error)}</p>` : ""}
        </div>
        <div class="modal-foot">
          <span class="muted" style="font-size:.8rem">Idempotency key <span class="code">${esc(key.slice(0, 8))}…</span></span>
          <button class="btn accent" type="submit" autofocus>Pay ${money(booking.amount, booking.currency)}</button>
        </div>
      </form>`,
      { onClose }
    );

    $("#pay-form", card).addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.target;
      const button = form.querySelector("[type=submit]");
      busy(button, true, "Processing…");
      try {
        // The same key is resent if this request is retried (double click,
        // flaky network): the server returns the original payment, never a
        // second charge.
        const payment = await api("/payments/", {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: { booking: booking.reference, method: form.method.value, outcome: form.outcome.value },
        });
        drawResult(payment);
      } catch (err) {
        if (err.code === "booking_already_paid") {
          toast("This booking is already paid for.", "info");
          return closeModal();
        }
        drawForm(err.message);
      }
    });
  };

  const drawResult = (p) => {
    const ok = p.status === "SUCCESS";
    const waiting = p.status === "PENDING";
    const title = ok ? "Payment received" : waiting ? "Waiting for approval" : "Payment declined";
    const headline = ok ? "You're booked in." : waiting ? "Approve it in your UPI app." : "That didn't go through.";
    const detail = ok
      ? `Your appointment on ${esc(fmtDateTime(booking.scheduled_for))} is confirmed.`
      : waiting
        ? "The provider accepted the request and will confirm it through our webhook. On the booking page, the simulator plays the provider."
        : esc(p.failure_reason || "The payment provider declined the charge.");
    openModal(
      `
      ${modalHead(title, p.reference)}
      <div class="modal-body">
        <div class="result">
          <div class="result-art">${art(p.reference, { w: 300, h: 300, tone: ok ? "success" : waiting ? null : "failure" })}</div>
          <h2>${headline}</h2>
          <p class="muted">${detail}</p>
          <dl class="kv">
            <div><dt>Amount</dt><dd>${money(p.amount, p.currency)}</dd></div>
            <div><dt>Method</dt><dd>${esc(METHODS.find(([v]) => v === p.method)?.[1] ?? p.method)}</dd></div>
            <div><dt>Status</dt><dd>${chip(p.status, PAYMENT_STATUS)}</dd></div>
            <div><dt>Booking</dt><dd>${chip(p.booking_status)}</dd></div>
            ${p.failure_code ? `<div><dt>Reason code</dt><dd><span class="code">${esc(p.failure_code)}</span></dd></div>` : ""}
            ${p.provider_reference ? `<div><dt>Provider ref</dt><dd><span class="code">${esc(p.provider_reference)}</span></dd></div>` : ""}
          </dl>
        </div>
      </div>
      <div class="modal-foot">
        ${
          ok || waiting
            ? `<span></span><button class="btn primary" data-action="close-modal" autofocus>View booking</button>`
            : `<button class="btn ghost" data-action="close-modal">Pay later</button><button class="btn accent" id="retry-pay" autofocus>Try again</button>`
        }
      </div>`,
      { onClose }
    );
    if (!ok && !waiting) {
      $("#retry-pay", modal).addEventListener("click", () => {
        key = newKey(); // a genuinely new attempt gets a new key
        booking = { ...booking, status: "FAILED" };
        drawForm();
      });
    }
  };

  drawForm();
}

function openCancelModal(reference) {
  const card = openModal(`
    ${modalHead("Cancel this booking?", reference)}
    <form id="cancel-form" novalidate style="display:contents">
      <div class="modal-body">
        <p>You can book the same slot again afterwards. Confirmed bookings can't be cancelled within ${CONFIG.booking.cancellation_cutoff_hours} hours of the appointment.</p>
        <div class="field">
          <label for="reason">Reason <span class="muted">(optional)</span></label>
          <textarea class="input" id="reason" name="reason" maxlength="255" placeholder="Helps the centre plan their day" autofocus></textarea>
        </div>
        <p class="form-error" role="alert" hidden></p>
      </div>
      <div class="modal-foot">
        <button class="btn ghost" type="button" data-action="close-modal">Keep booking</button>
        <button class="btn danger solid" type="submit">Cancel booking</button>
      </div>
    </form>`);

  $("#cancel-form", card).addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = e.target.querySelector("[type=submit]");
    busy(button, true, "Cancelling…");
    try {
      await api(`/bookings/${encodeURIComponent(reference)}/cancel/`, {
        method: "POST",
        body: { reason: e.target.reason.value.trim() },
      });
      onModalClose = render;
      closeModal();
      toast("Booking cancelled.", "success");
    } catch (err) {
      busy(button, false);
      showFieldErrors(e.target, { message: err.message });
    }
  });
}

/* ═══════════════════════════════ bookings ════════════════════════════════ */

function dateTile(iso, seed) {
  const d = new Date(iso);
  return `<div class="date-tile" style="background:${colorFor(seed)}" aria-hidden="true">
      <b>${d.getDate()}</b><small>${d.toLocaleDateString("en-IN", { month: "short" })}</small>
    </div>`;
}

function bookingRow(b) {
  const past = new Date(b.scheduled_for) < new Date();
  return `
    <div class="card booking-row">
      ${dateTile(b.scheduled_for, b.test.code)}
      <div>
        <h3><a href="#/bookings/${esc(b.reference)}" style="text-decoration:none">${esc(b.test.name)}</a></h3>
        <div class="meta" style="margin-top:4px">
          <span>${ICON.pin}${esc(b.centre.name)}</span>
          <span>${ICON.clock}${esc(fmtDateTime(b.scheduled_for))}${past ? " · past" : ""}</span>
        </div>
        <div class="meta" style="margin-top:8px">${chip(b.status)}<span class="code">${esc(b.reference)}</span></div>
      </div>
      <div class="right">
        <span class="price">${money(b.amount, b.currency)}</span>
        <div class="actions">
          ${b.can_be_paid ? `<button class="btn sm accent" data-action="pay" data-ref="${esc(b.reference)}">Pay now</button>` : ""}
          <a class="btn sm" href="#/bookings/${esc(b.reference)}">Details</a>
        </div>
      </div>
    </div>`;
}

async function bookingsView({ query, token }) {
  const status = (query.get("status") || "").toUpperCase();
  const tabs = [["", "All"], ...Object.entries(BOOKING_STATUS).map(([k, v]) => [k, v.label])];

  paint(
    token,
    `
    <div class="section-head" style="margin-bottom:24px">
      <div class="stack" style="--gap:10px">
        <p class="eyebrow">Your appointments</p>
        <h1>My bookings</h1>
      </div>
      <a class="btn primary" href="#/tests">Book a test</a>
    </div>
    <nav class="tabs" aria-label="Filter by status" style="margin-bottom:22px">
      ${tabs
        .map(
          ([k, label]) =>
            `<a class="tab" href="#/bookings${k ? `?status=${k}` : ""}" aria-current="${k === status}">${esc(label)}</a>`
        )
        .join("")}
    </nav>
    <div class="booking-list" id="booking-list">${skeletons(3, "row-sk")}</div>`
  );

  const data = await api(`/bookings/?page_size=100&ordering=scheduled_for${status ? `&status=${status}` : ""}`);
  if (token !== renderToken) return;

  // Patients think in appointment dates, not booking dates: next appointment
  // first, then past ones (most recent first) under their own heading.
  const now = Date.now();
  const upcoming = data.results.filter((b) => new Date(b.scheduled_for).getTime() >= now);
  const past = data.results.filter((b) => new Date(b.scheduled_for).getTime() < now).reverse();
  const group = (title, rows) =>
    rows.length ? `<p class="eyebrow" style="margin:8px 0 2px">${title}</p>${rows.map(bookingRow).join("")}` : "";

  $("#booking-list").innerHTML = data.results.length
    ? upcoming.length && past.length
      ? group("Upcoming", upcoming) + group("Past", past)
      : [...upcoming, ...past].map(bookingRow).join("")
    : empty(
        `no-bookings-${status}`,
        status ? "Nothing here" : "No bookings yet",
        status ? "You have no bookings with this status." : "When you book a test, it shows up here.",
        `<a class="btn primary" href="#/tests">Find a test</a>`
      );
}

/* ─────────────────────────── booking lifecycle ─────────────────────────── */

const LIFECYCLE_NODES = {
  PENDING: { x: 160, y: 34, fill: "#FFB23F" },
  CONFIRMED: { x: 70, y: 130, fill: "#1A9E8F" },
  FAILED: { x: 250, y: 130, fill: "#FF6B57" },
  CANCELLED: { x: 160, y: 226, fill: "#D9D5E2" },
};

// Mirrors ALLOWED_TRANSITIONS in apps/bookings/services.py. Laid out
// vertically so it stays legible in a narrow sidebar column.
const LIFECYCLE_EDGES = [
  { from: "PENDING", to: "CONFIRMED", d: "M140 53 L90 110", label: "paid", lx: 96, ly: 76 },
  { from: "PENDING", to: "FAILED", d: "M180 53 L230 110", label: "declined", lx: 238, ly: 76 },
  { from: "FAILED", to: "PENDING", d: "M282 111 Q312 40 220 30", label: "retry", lx: 304, ly: 98 },
  { from: "PENDING", to: "CANCELLED", d: "M160 53 L160 204", label: "cancel", lx: 184, ly: 164 },
  { from: "CONFIRMED", to: "CANCELLED", d: "M90 149 L138 206", label: "", lx: 0, ly: 0 },
  { from: "FAILED", to: "CANCELLED", d: "M230 149 L182 206", label: "", lx: 0, ly: 0 },
];

/** The booking state machine, with the path this booking actually took. */
function lifecycleDiagram(b) {
  const taken = new Set(
    b.status_changes.filter((c) => c.from_status).map((c) => `${c.from_status}>${c.to_status}`)
  );
  const visited = new Set(b.status_changes.map((c) => c.to_status));
  const path = [...b.status_changes].reverse().map((c) => c.to_status).join(" then ");

  const edges = LIFECYCLE_EDGES.map((e) => {
    const on = taken.has(`${e.from}>${e.to}`);
    return `
      <path d="${e.d}" fill="none" stroke="${on ? INK : "#D6CEC0"}" stroke-width="${on ? 2.5 : 1.5}"
        ${on ? "" : 'stroke-dasharray="5 5"'} marker-end="url(#arrow-${on ? "on" : "off"})"/>
      ${e.label ? `<text x="${e.lx}" y="${e.ly}" class="lc-edge ${on ? "on" : ""}">${e.label}</text>` : ""}`;
  }).join("");

  const nodes = Object.entries(LIFECYCLE_NODES).map(([name, n]) => {
    const current = name === b.status;
    const seen = visited.has(name);
    const fill = current ? n.fill : seen ? "#FFFFFF" : "#F4EEE4";
    const stroke = current || seen ? INK : "#DCD3C5";
    return `
      <g class="lc-node ${current ? "current" : ""}">
        <rect x="${n.x - 58}" y="${n.y - 18}" width="116" height="36" rx="18"
          fill="${fill}" stroke="${stroke}" stroke-width="${current ? 2.5 : seen ? 2 : 1.5}"/>
        <text x="${n.x}" y="${n.y + 5}" class="lc-label ${current || seen ? "" : "dim"}">${name}</text>
      </g>`;
  }).join("");

  return `
    <svg class="lifecycle" viewBox="0 0 320 250" role="img" aria-labelledby="lc-title">
      <title id="lc-title">Booking lifecycle. Current state ${esc(b.status)}. Path taken: ${esc(path)}.</title>
      <defs>
        <marker id="arrow-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="${INK}"/></marker>
        <marker id="arrow-off" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="#D6CEC0"/></marker>
      </defs>
      ${edges}
      ${nodes}
    </svg>`;
}

/* ─────────────────────────── provider simulator ────────────────────────── */

/** Simulation runs per booking, newest first. Survives re-renders. */
const simLog = new Map();
/** The chosen deliveries-per-event, kept across re-renders. */
let simDeliveries = 3;

const DELIVERY_OUTCOMES = {
  booking_confirmed: ["ok", "Processed: booking confirmed"],
  booking_failed: ["ok", "Processed: booking marked as payment failed"],
  already_success: ["ok", "Processed: already paid, nothing to change"],
  already_failed: ["ok", "Processed: already declined, nothing to change"],
  conflicting_outcome_ignored: ["refused", "Refused: payment already settled, the first outcome wins"],
  refund_required: ["refused", "Recorded: booking was cancelled, refund flagged"],
  booking_already_terminal: ["refused", "Recorded: booking already closed"],
  queued: ["queued", "Accepted: queued for the background worker"],
  processing_failed: ["error", "Processing failed: stored and scheduled for retry"],
};

function describeDelivery(d) {
  if (d.http_status >= 400) return ["error", `Rejected (HTTP ${d.http_status})`];
  if (d.duplicate) return ["dup", "Duplicate: already received, not processed again"];
  return DELIVERY_OUTCOMES[d.result] ?? ["ok", d.result || "Processed"];
}

function simRun(run) {
  const processed = run.deliveries.filter((d) => !d.duplicate && d.http_status < 400).length;
  const after = run.final?.booking_status ?? run.booking_status_after;
  const changed = run.booking_status_before !== after;
  const final = run.final ? DELIVERY_OUTCOMES[run.final.result] : null;
  return `
    <div class="sim-run">
      <div class="sim-run-head">
        <span class="sim-type ${run.event_type === "payment.succeeded" ? "ok" : "refused"}">${esc(run.event_type)}</span>
        <span class="code" title="${esc(run.event_id)}">${esc(run.event_id.slice(0, 16))}…</span>
        <span class="muted">× ${run.deliveries.length} · handled ${processed === 1 ? "once" : `${processed}×`}</span>
      </div>
      <ol class="sim-deliveries">
        ${run.deliveries
          .map((d) => {
            const [kind, text] = describeDelivery(d);
            return `<li class="${kind}"><span class="n">#${d.attempt}</span><span class="http">${d.http_status}</span><span>${esc(text)}</span></li>`;
          })
          .join("")}
        ${final ? `<li class="${final[0]}"><span class="n">↳</span><span class="http">worker</span><span>${esc(final[1])}</span></li>` : ""}
      </ol>
      <div class="sim-outcome">
        Booking ${changed ? `${chip(run.booking_status_before)} → ${chip(after)}` : `unchanged ${chip(after)}`}
      </div>
    </div>`;
}

function simulatorCard(b, target) {
  if (!CONFIG.payment_simulator || !target) return "";
  const runs = simLog.get(b.reference) ?? [];
  return `
    <section class="card pad sim" aria-labelledby="sim-title">
      <div class="sim-head">
        <div>
          <p class="eyebrow">Demo mode</p>
          <h3 id="sim-title">Payment provider simulator</h3>
        </div>
        <span class="chip lilac plain">Signed · real webhook endpoint</span>
      </div>
      <p class="sim-lede">
        Plays the payment provider calling <span class="code">POST /payments/webhook/</span> about
        <span class="code">${esc(target.reference)}</span> ${chip(target.status, PAYMENT_STATUS)}.
        Each event is signed on the server with the real secret and goes through the real endpoint.
        Send one several times and watch it apply exactly once.
      </p>
      <div class="sim-controls">
        <fieldset class="choices">
          <legend>Deliver each event</legend>
          ${[1, 2, 3, 5].map((n) => `<label class="choice"><input type="radio" name="sim-deliveries" value="${n}" ${n === simDeliveries ? "checked" : ""}><span>${n}×</span></label>`).join("")}
        </fieldset>
        <div class="actions">
          <button class="btn primary" data-action="simulate" data-outcome="success"
            data-payment="${esc(target.reference)}" data-booking="${esc(b.reference)}">Send “payment succeeded”</button>
          <button class="btn danger" data-action="simulate" data-outcome="failure"
            data-payment="${esc(target.reference)}" data-booking="${esc(b.reference)}">Send “payment failed”</button>
        </div>
      </div>
      <div class="sim-log" aria-live="polite">
        ${runs.length ? runs.map(simRun).join("") : `<p class="muted sim-empty">${
          target.status === "PENDING"
            ? "No simulated deliveries yet. Try sending “payment succeeded” 3× and watch it confirm the booking once."
            : "This payment is already settled. Send either event to see duplicates recorded and a contradicting outcome refused."
        }</p>`}
      </div>
      <div class="sim-events">
        <p class="label">Webhook deliveries received for this payment</p>
        <div id="sim-events" data-payment="${esc(target.reference)}"><p class="muted">Loading…</p></div>
      </div>
    </section>`;
}

function webhookEventsTable(events) {
  if (!events.length) return `<p class="muted">None yet.</p>`;
  return `
    <div class="table-wrap">
      <table class="list compact">
        <thead><tr><th>Event</th><th>Type</th><th class="num">Delivered</th><th>Status</th><th>What it did</th></tr></thead>
        <tbody>
          ${events
            .map(
              (e) => `
            <tr>
              <td><span class="code" title="${esc(e.event_id)}">${esc(e.event_id.slice(0, 12))}…</span></td>
              <td>${esc(e.event_type.replace("payment.", ""))}</td>
              <td class="num"><b>${e.delivery_count}×</b></td>
              <td>${esc(e.status.toLowerCase())}</td>
              <td class="muted">${esc((DELIVERY_OUTCOMES[e.result]?.[1] ?? e.result) || "Waiting for the worker")}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>`;
}

async function runSimulation(button) {
  const { payment, outcome, booking } = button.dataset;
  const deliveries = Number($("input[name=sim-deliveries]:checked")?.value || 3);
  simDeliveries = deliveries;
  $$("[data-action=simulate]").forEach((b) => (b.disabled = true));
  busy(button, true, `Delivering ${deliveries}×…`);
  try {
    const run = await api(`/payments/${encodeURIComponent(payment)}/simulate-webhook/`, {
      method: "POST",
      body: { outcome, deliveries },
    });
    const runs = simLog.get(booking) ?? [];
    runs.unshift(run);
    simLog.set(booking, runs.slice(0, 5));

    // With a Celery worker the first delivery is only queued: give the
    // worker a moment and record what it actually did.
    if (run.deliveries.some((d) => d.result === "queued")) {
      for (let i = 0; i < 16; i++) {
        await new Promise((r) => setTimeout(r, 600));
        const events = await api(`/payments/${encodeURIComponent(payment)}/webhook-events/`);
        const ev = events.find((e) => e.event_id === run.event_id);
        if (ev && ev.status !== "RECEIVED") {
          const fresh = await api(`/bookings/${encodeURIComponent(booking)}/`);
          run.final = { result: ev.result, booking_status: fresh.status };
          break;
        }
      }
    }
    const processed = run.deliveries.filter((d) => !d.duplicate).length;
    const n = run.deliveries.length;
    toast(`${n} ${n === 1 ? "delivery" : "deliveries"}, handled ${processed === 1 ? "once" : `${processed}×`}`, "success");
    await render();
  } catch (err) {
    toast(err.message, "error");
    busy(button, false);
    $$("[data-action=simulate]").forEach((b) => (b.disabled = false));
  }
}

/* ───────────────────────────── booking detail ─────────────────────────── */

async function bookingDetailView({ params: [reference], token }) {
  paint(token, `<a class="back" href="#/bookings">${ICON.back} My bookings</a>${skeletons(1, "card-sk")}`);

  const [b, payments] = await Promise.all([
    api(`/bookings/${encodeURIComponent(reference)}/`),
    api(`/payments/?booking=${encodeURIComponent(reference)}&page_size=50`),
  ]);

  const history = [...b.status_changes].reverse(); // oldest first
  const latest = payments.results[0] ?? null;
  const awaitingProvider = latest?.status === "PENDING";
  const canCancel = b.status !== "CANCELLED";
  const statusCallout = awaitingProvider
    ? `<div class="callout sky">${ICON.clock}<span><b>Waiting for the payment provider</b> to confirm ${esc(latest.reference)}.
        ${CONFIG.payment_simulator ? "Play the provider with the simulator below." : "This usually takes a few seconds."}</span></div>`
    : {
        PENDING: `<div class="callout">${ICON.info}<span>Your slot is held but not confirmed until you pay.</span></div>`,
        FAILED: `<div class="callout coral">${ICON.info}<span>The last payment was declined. Your slot is still held, so try again.</span></div>`,
        CONFIRMED: `<div class="callout teal">${ICON.shield}<span>You're all set. Bring a photo ID and arrive ten minutes early.</span></div>`,
        CANCELLED: "",
      }[b.status];

  paint(
    token,
    `
    <a class="back" href="#/bookings">${ICON.back} My bookings</a>
    <div class="detail-grid">
      <div class="stack" style="--gap:24px">
        <article class="banner">
          <div class="banner-art" style="height:150px">${art(`test-${b.test.code}`, { w: 1000, h: 200 })}</div>
          <div class="banner-body">
            <div class="banner-top">
              <div class="stack" style="--gap:8px">
                ${chip(b.status, BOOKING_STATUS, "big")}
                <h1 style="font-size:clamp(1.6rem,3.5vw,2.3rem)">${esc(b.test.name)}</h1>
                <p class="muted"><a href="#/centres/${b.centre.id}">${esc(b.centre.name)}</a>, ${esc(b.centre.city)}</p>
              </div>
              <div class="price-block">
                <p class="price xl">${money(b.amount, b.currency)}</p>
                <span class="code">${esc(b.reference)}</span>
              </div>
            </div>

            <div class="facts">
              <div class="fact"><div class="label">Date</div><div class="value">${esc(fmtDate(b.scheduled_for))}</div></div>
              <div class="fact"><div class="label">Time</div><div class="value">${esc(fmtTime(b.scheduled_for))}</div></div>
              <div class="fact"><div class="label">Report</div><div class="value">~${b.test.report_eta_hours}h after</div></div>
            </div>

            ${statusCallout}
            ${b.patient_notes ? `<div><p class="label">Your notes</p><p>${esc(b.patient_notes)}</p></div>` : ""}

            <div class="actions">
              ${b.can_be_paid && !awaitingProvider ? `<button class="btn accent" data-action="pay" data-ref="${esc(b.reference)}">Pay ${money(b.amount, b.currency)}</button>` : ""}
              ${canCancel ? `<button class="btn danger" data-action="cancel" data-ref="${esc(b.reference)}">Cancel booking</button>` : ""}
              <a class="btn ghost" href="#/tests/${encodeURIComponent(b.test.code)}">About this test</a>
            </div>
          </div>
        </article>
        ${simulatorCard(b, latest)}
      </div>

      <div class="stack" style="--gap:24px">
        <section class="card pad">
          <h3>Lifecycle</h3>
          <p class="muted" style="font-size:.88rem;margin:2px 0 8px">The booking state machine. Bold arrows are the path this booking took.</p>
          ${lifecycleDiagram(b)}
        </section>

        <section class="card pad">
          <h3 style="margin-bottom:18px">History</h3>
          <ol class="timeline">
            ${history
              .map((c) => {
                const meta = BOOKING_STATUS[c.to_status] ?? { label: c.to_status, cls: "" };
                return `
              <li>
                <span class="dot ${meta.cls}"></span>
                <div class="what">${esc(meta.label)}</div>
                ${c.reason ? `<div class="why">${esc(humanReason(c.reason))}</div>` : ""}
                <div class="when" title="${esc(fmtDateTime(c.created_at))}">${esc(SOURCE_LABEL[c.source] ?? "")} · ${esc(fmtRelative(c.created_at))}</div>
              </li>`;
              })
              .join("")}
          </ol>
        </section>

        <section class="card pad">
          <h3 style="margin-bottom:6px">Payments</h3>
          ${
            payments.results.length
              ? payments.results
                  .map(
                    (p) => `
            <div class="pay-row">
              <div>
                <div style="font-weight:700">${money(p.amount, p.currency)} <span class="muted" style="font-weight:500">· ${esc(METHODS.find(([v]) => v === p.method)?.[1] ?? p.method)}</span></div>
                <div class="muted" style="font-size:.85rem">${esc(fmtDateTime(p.created_at))}${p.failure_reason ? ` · ${esc(p.failure_reason)}` : ""}</div>
                <div class="pay-ids">
                  <span class="code">${esc(p.reference)}</span>
                  ${p.idempotency_key ? `<span class="code" title="Idempotency-Key: ${esc(p.idempotency_key)}">key ${esc(p.idempotency_key.slice(0, 8))}…</span>` : ""}
                </div>
              </div>
              ${chip(p.status, PAYMENT_STATUS)}
            </div>`
                  )
                  .join("")
              : `<p class="muted" style="padding-top:8px">No payment attempts yet.</p>`
          }
        </section>
      </div>
    </div>`
  );

  const eventsBox = $("#sim-events");
  if (eventsBox) {
    const events = await api(`/payments/${encodeURIComponent(eventsBox.dataset.payment)}/webhook-events/`);
    if (token === renderToken) eventsBox.innerHTML = webhookEventsTable(events);
  }
}

/* ═══════════════════════════════ payments ════════════════════════════════ */

async function paymentsView({ token }) {
  paint(
    token,
    `
    <div class="section-head" style="margin-bottom:24px">
      <div class="stack" style="--gap:10px">
        <p class="eyebrow">Billing</p>
        <h1>Payments</h1>
      </div>
    </div>
    <div id="pay-list">${skeletons(2, "row-sk")}</div>`
  );

  const data = await api("/payments/?page_size=100");
  if (token !== renderToken) return;

  if (!data.results.length) {
    $("#pay-list").innerHTML = empty("no-payments", "No payments yet", "Payments for your bookings will appear here.", `<a class="btn primary" href="#/bookings">Go to bookings</a>`);
    return;
  }

  const paid = data.results.filter((p) => p.status === "SUCCESS").reduce((sum, p) => sum + Number(p.amount), 0);
  $("#pay-list").innerHTML = `
    <div class="facts" style="margin-bottom:22px">
      <div class="fact"><div class="label">Total paid</div><div class="value price">${money(paid)}</div></div>
      <div class="fact"><div class="label">Attempts</div><div class="value price">${data.count}</div></div>
      <div class="fact"><div class="label">Declined</div><div class="value price">${data.results.filter((p) => p.status === "FAILED").length}</div></div>
    </div>
    <div class="card table-wrap">
      <table class="list">
        <thead><tr><th>Payment</th><th>Booking</th><th>Method</th><th>Status</th><th class="num">Amount</th></tr></thead>
        <tbody>
          ${data.results
            .map(
              (p) => `
          <tr>
            <td><span class="code">${esc(p.reference)}</span><div class="muted" style="font-size:.85rem;margin-top:4px">${esc(fmtDateTime(p.created_at))}</div></td>
            <td><a href="#/bookings/${esc(p.booking)}">${esc(p.booking)}</a></td>
            <td>${esc(METHODS.find(([v]) => v === p.method)?.[1] ?? p.method)}</td>
            <td>${chip(p.status, PAYMENT_STATUS)}${p.failure_reason ? `<div class="muted" style="font-size:.82rem;margin-top:4px;max-width:28ch">${esc(p.failure_reason)}</div>` : ""}</td>
            <td class="num price" style="font-size:1.05rem">${money(p.amount, p.currency)}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>`;
}

/* ═════════════════════════════════ boot ══════════════════════════════════ */

api("/config/", { authed: false })
  .then((config) => {
    CONFIG = config;
  })
  .catch(() => {
    /* keep the defaults; the server enforces the real rules either way */
  })
  .finally(render);
