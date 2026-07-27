/* Knowledge-Ops shell — theme switching.
   Themes live on <html data-theme="...">; every token exists in all four.
   Persists localStorage 'ko-theme'; honors prefers-color-scheme on first visit.
   Emits bus 'theme' {name} on change. */

export const THEMES = [
  { name: "aurora",   label: "Aurora",   hint: "deep-space default" },
  { name: "daylight", label: "Daylight", hint: "ink & gold on paper" },
  { name: "porcelain", label: "Porcelain", hint: "cool gallery light" },
  { name: "nebula",   label: "Nebula",   hint: "saturated demo reel" },
  { name: "midnight", label: "Midnight", hint: "quiet OLED black" },
];

const KEY = "ko-theme";
const valid = (name) => THEMES.some((t) => t.name === name);

export function currentTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  return valid(t) ? t : "aurora";
}

export function initTheme() {
  // ?theme=nebula deep-links a theme (demos, screenshots) and persists it.
  let fromUrl = null;
  try { fromUrl = new URLSearchParams(location.search).get("theme"); } catch { /* no URL API */ }
  if (valid(fromUrl)) {
    try { localStorage.setItem(KEY, fromUrl); } catch { /* private mode */ }
    document.documentElement.setAttribute("data-theme", fromUrl);
    return fromUrl;
  }
  let stored = null;
  try { stored = localStorage.getItem(KEY); } catch { /* private mode */ }
  let name = valid(stored) ? stored : null;
  if (!name) {
    let light = false;
    try { light = window.matchMedia("(prefers-color-scheme: light)").matches; } catch { /* no matchMedia */ }
    name = light ? "daylight" : "aurora";
  }
  document.documentElement.setAttribute("data-theme", name);
  return name;
}

export function setTheme(name, bus) {
  if (!valid(name)) return;
  document.documentElement.setAttribute("data-theme", name);
  try { localStorage.setItem(KEY, name); } catch { /* private mode */ }
  if (bus) bus.emit("theme", { name });
}

/* Header switcher: icon button + role=menu list of menuitemradio entries.
   Fully keyboard operable (Enter/Space open, arrows cycle, Esc closes). */
export function mountThemeMenu(host, bus) {
  host.classList.add("theme-menu");
  const labelOf = (name) => (THEMES.find((t) => t.name === name) || THEMES[0]).label;
  host.innerHTML = `
    <button type="button" class="btn btn--sm theme-menu__btn" aria-haspopup="menu" aria-expanded="false"
            aria-label="Theme: ${labelOf(currentTheme())}">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1.6"/>
        <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>
      </svg>
    </button>
    <div class="theme-menu__list card card--strong" role="menu" aria-label="Theme" hidden>
      ${THEMES.map((t) => `
        <button type="button" class="theme-menu__item" role="menuitemradio"
                data-theme-choice="${t.name}" aria-checked="${t.name === currentTheme()}">
          <span>${t.label} <span class="dim" style="font-size:.8em;">— ${t.hint}</span></span>
          <span class="theme-menu__mark" aria-hidden="true"></span>
        </button>`).join("")}
    </div>`;

  const btn = host.querySelector(".theme-menu__btn");
  const list = host.querySelector(".theme-menu__list");
  const items = Array.from(list.querySelectorAll(".theme-menu__item"));

  const sync = (name) => {
    items.forEach((it) => it.setAttribute("aria-checked", String(it.dataset.themeChoice === name)));
    btn.setAttribute("aria-label", `Theme: ${labelOf(name)}`);
  };

  const open = () => {
    list.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    const checked = items.find((it) => it.getAttribute("aria-checked") === "true") || items[0];
    checked.focus();
  };
  const close = (refocus) => {
    if (list.hidden) return;
    list.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    if (refocus) btn.focus();
  };

  btn.addEventListener("click", () => (list.hidden ? open() : close(true)));

  list.addEventListener("keydown", (e) => {
    const idx = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? (idx + 1) % items.length : (idx - 1 + items.length) % items.length;
      items[next].focus();
    } else if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
    else if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); }
    else if (e.key === "Escape") { e.preventDefault(); close(true); }
    else if (e.key === "Tab") { close(false); }
  });

  items.forEach((it) => it.addEventListener("click", () => {
    setTheme(it.dataset.themeChoice, bus);
    close(true);
  }));

  document.addEventListener("pointerdown", (e) => {
    if (!host.contains(e.target)) close(false);
  });

  // Keep the switcher honest when the theme changes elsewhere (palette action).
  if (bus) bus.on("theme", ({ name }) => sync(name));
}
