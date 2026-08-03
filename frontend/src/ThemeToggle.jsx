import { useEffect, useState } from "react";

/* Theme lives on <html data-theme>, not in React state alone, so the editor
   portal and everything else rendered outside the tree inherit it too. */

const KEY = "clipper_theme";

function initial() {
  const saved = localStorage.getItem(KEY);
  if (saved === "dark" || saved === "light") return saved;
  // Light is the designed default -- the cream palette is the product's face.
  // Dark is a preference someone opts into, not one inferred from their OS.
  return "light";
}

export function useTheme() {
  const [theme, setTheme] = useState(initial);

  useEffect(() => {
    const html = document.documentElement;
    html.dataset.theme = theme;
    localStorage.setItem(KEY, theme);
    // The transition class is added only around a real switch, so the first
    // paint is not a cross-fade from the wrong colours.
    html.classList.add("theme-anim");
    const t = setTimeout(() => html.classList.remove("theme-anim"), 400);
    return () => clearTimeout(t);
  }, [theme]);

  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

export default function ThemeToggle({ theme, onToggle }) {
  const dark = theme === "dark";
  return (
    <button
      className="theme-btn"
      onClick={onToggle}
      title={dark ? "Switch to light" : "Switch to dark"}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      data-testid="theme-toggle"
    >
      {dark ? (
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none">
          <circle cx="12" cy="12" r="4.2" stroke="currentColor" strokeWidth="2" />
          <path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4 17 7M7 17l-1.6 1.6"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none">
          <path d="M20 14.4A8.6 8.6 0 0 1 9.6 4a8.6 8.6 0 1 0 10.4 10.4Z"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                strokeLinejoin="round" />
        </svg>
      )}
    </button>
  );
}
