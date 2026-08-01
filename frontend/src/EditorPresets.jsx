/* Platform handle presets, caption animations, speed and background.
 *
 * A handle is not just text: each platform has a signature colour and a
 * conventional placement, and matching them is the difference between "someone
 * typed their name on a video" and "this is a branded clip".
 */

/* Brand marks come from the backend as PNGs (the very files the renderer
   composites), so nothing here can drift from what gets exported. */
export const logoSrc = (id) => `/api/platform-logos/${id}.png`;

export const PLATFORMS = [
  {
    id: "instagram",
    name: "Instagram",
    prefix: "@",
    color: "#ffffff",
    accent: "#E1306C",
  },
  {
    id: "x",
    name: "X",
    prefix: "@",
    color: "#ffffff",
    accent: "#8A9099",
  },
  {
    id: "tiktok",
    name: "TikTok",
    prefix: "@",
    color: "#ffffff",
    accent: "#25F4EE",
  },
  {
    id: "youtube",
    name: "YouTube",
    prefix: "@",
    color: "#ffffff",
    accent: "#FF0033",
  },
  {
    id: "twitch",
    name: "Twitch",
    prefix: "",
    color: "#ffffff",
    accent: "#9146FF",
  },
  {
    id: "kick",
    name: "Kick",
    prefix: "",
    color: "#ffffff",
    accent: "#53FC18",
  },
];

/** Builds a text overlay preset for a platform handle.

    No `plate`: the renderer draws the pill itself (soft dark glass with an
    accent edge), so a plate here would paint a second box behind it. */
export function platformOverlay(platform, handle) {
  const text = `${platform.prefix}${(handle || "yourhandle").replace(/^@/, "")}`;
  return {
    type: "text",
    text,
    platform: platform.id,
    x: 0.5,
    y: 0.90,           // clear of the platform's own bottom chrome
    size: 0.038,
    color: "#ffffff",
    font: "poppins",
    pill: true,
    opacity: 1,
  };
}

/* Background swatches for the fit/pad frame. Neutral first, then colours that
   read as deliberate branding rather than a default. */
export const BACKGROUNDS = [
  { id: "black", label: "Black", css: "#000000" },
  { id: "#101018", label: "Ink", css: "#101018" },
  { id: "#f6f5f1", label: "Paper", css: "#f6f5f1" },
  { id: "#ffffff", label: "White", css: "#ffffff" },
  { id: "#6d28d9", label: "Violet", css: "#6d28d9" },
  { id: "#1fa48f", label: "Teal", css: "#1fa48f" },
  { id: "#d9467c", label: "Pink", css: "#d9467c" },
  { id: "#e8b400", label: "Gold", css: "#e8b400" },
];

/* Preview of how each animation behaves, used to drive the CSS demo in the
   picker. Names match backend subtitles.ANIMATIONS. */
export const ANIMATION_DEMO = {
  none: "anim-none",
  fade: "anim-fade",
  pop: "anim-pop",
  riseup: "anim-riseup",
  punch: "anim-punch",
  bounce: "anim-bounce",
};
