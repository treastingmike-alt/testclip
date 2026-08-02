export const API_BASE = "/api";

function authHeaders() {
  const token = localStorage.getItem("clipper_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}


export async function submitJob({ url, nClips, mode, burnSubtitles, autoCensor, multilingual, voice, language, template, ratio, lengthPref, intent }) {
  const resp = await fetch(`${API_BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      url,
      n_clips: nClips,
      mode,
      burn_subtitles: burnSubtitles,
      auto_censor: autoCensor !== false,
      multilingual: multilingual === true,
      voice,
      language,
      template,
      ratio,
      length_pref: lengthPref,
      intent,
    }),
  });
  if (!resp.ok) {
    let detail = "";
    try {
      detail = (await resp.json()).detail || "";
    } catch {
      // non-JSON error body -- fall through to the generic message
    }
    throw new Error(detail || `Failed to submit job (${resp.status})`);
  }
  return resp.json(); // { job_id }
}

export async function getJob(jobId) {
  const resp = await fetch(`${API_BASE}/jobs/${jobId}`);
  if (!resp.ok) throw new Error(`Failed to fetch job (${resp.status})`);
  return resp.json();
}

/* `version` is the rendered file's mtime. Re-exporting rewrites the same
   filename, so without it the browser keeps showing the cached pre-edit video
   -- the reason an edited title updated on the card but not in the picture. */
export function clipUrl(jobId, filename, version) {
  const base = `${API_BASE}/clips/${jobId}/${filename}`;
  return version ? `${base}?v=${version}` : base;
}

export async function getTranscript(jobId) {
  const resp = await fetch(`${API_BASE}/jobs/${jobId}/transcript`);
  if (!resp.ok) throw await readError(resp, `Could not load transcript (${resp.status})`);
  return resp.json();
}

export async function rerenderClip(jobId, index, start, end, opts = {}) {
  const resp = await fetch(`${API_BASE}/jobs/${jobId}/clips/${index}/rerender`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      start,
      end,
      caption_style: opts.captionStyle || null,
      caption_font: opts.captionFont || null,
      translate_to: opts.translateTo || null,
      caption_lines: opts.captionLines || null,
    }),
  });
  if (!resp.ok) throw await readError(resp, `Re-render failed (${resp.status})`);
  return resp.json();
}

/* ---- billing ---- */

async function readError(resp, fallback) {
  let detail = "";
  try {
    const body = await resp.json();
    // FastAPI validation errors arrive as a list of objects, not a string.
    detail = Array.isArray(body.detail)
      ? body.detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : body.detail || "";
  } catch { /* non-JSON body */ }
  return new Error(detail || fallback);
}

export async function getPlans() {
  const resp = await fetch(`${API_BASE}/billing/plans`);
  if (!resp.ok) throw await readError(resp, `Could not load plans (${resp.status})`);
  return resp.json();
}

export async function getBilling() {
  const resp = await fetch(`${API_BASE}/billing/me`, { headers: authHeaders() });
  if (!resp.ok) throw await readError(resp, `Could not load billing (${resp.status})`);
  return resp.json();
}

export async function startCheckout({ planId, credits, interval = "monthly" }) {
  const resp = await fetch(`${API_BASE}/billing/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ plan_id: planId, credits, interval }),
  });
  if (!resp.ok) throw await readError(resp, `Checkout failed (${resp.status})`);
  return resp.json();
}

/* ---- auth ---- */

export async function register(email, password) {
  const resp = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw await readError(resp, `Sign up failed (${resp.status})`);
  const data = await resp.json();
  localStorage.setItem("clipper_token", data.token);
  return data.user;
}

export async function login(email, password) {
  const resp = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw await readError(resp, `Sign in failed (${resp.status})`);
  const data = await resp.json();
  localStorage.setItem("clipper_token", data.token);
  return data.user;
}

export async function fetchMe() {
  const token = localStorage.getItem("clipper_token");
  if (!token) return null;
  const resp = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
  if (!resp.ok) {
    localStorage.removeItem("clipper_token");   // expired or forged
    return null;
  }
  return resp.json();
}

export function logout() {
  localStorage.removeItem("clipper_token");
}

export async function listJobs() {
  const resp = await fetch(`${API_BASE}/jobs`, { headers: authHeaders() });
  if (!resp.ok) throw await readError(resp, `Could not load history (${resp.status})`);
  return resp.json();
}

/* ---- live (non-destructive) editing ---- */

/* Each clip has its own proxy, covering only the window its trim handles can
   reach. `clipIndex` is the editor's 0-based index; the files on disk are
   1-based (preview_1.mp4, next to clip_1.mp4). */
export function previewUrl(jobId, clipIndex) {
  const q = clipIndex === undefined || clipIndex === null
    ? "" : `?clip=${clipIndex + 1}`;
  return `${API_BASE}/jobs/${jobId}/preview${q}`;
}

/** Saves the clip recipe. No render, no cost -- safe to call on every change. */
export async function saveClipEdit(jobId, index, recipe) {
  const resp = await fetch(`${API_BASE}/jobs/${jobId}/clips/${index}/edit`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(recipe),
  });
  if (!resp.ok) throw await readError(resp, `Could not save edit (${resp.status})`);
  return resp.json();
}

/** Renders the stored recipe to a real MP4. The only expensive call here. */
export async function exportClip(jobId, index) {
  const resp = await fetch(`${API_BASE}/jobs/${jobId}/clips/${index}/export`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!resp.ok) throw await readError(resp, `Export failed (${resp.status})`);
  return resp.json();
}

export function overlayImageUrl(jobId, file) {
  return `${API_BASE}/jobs/${jobId}/overlays/${file}`;
}

/** Uploads a logo for this job. Returns { file, url }. */
export async function uploadOverlayImage(jobId, file) {
  const body = new FormData();
  body.append("file", file);
  const resp = await fetch(`${API_BASE}/jobs/${jobId}/overlays/upload`, {
    method: "POST", headers: authHeaders(), body,
  });
  if (!resp.ok) throw await readError(resp, `Upload failed (${resp.status})`);
  return resp.json();
}

export function gameplayUrl(jobId) {
  return `${API_BASE}/jobs/${jobId}/gameplay`;
}

/* Fonts, animations and speed range come from the renderer, so a .ttf dropped
   into assets/fonts appears here without a matching frontend edit. */
let _capOptions = null;
export async function getCaptionOptions() {
  if (_capOptions) return _capOptions;
  const resp = await fetch(`${API_BASE}/caption-options`);
  if (!resp.ok) throw new Error("Could not load caption options");
  _capOptions = await resp.json();
  return _capOptions;
}
