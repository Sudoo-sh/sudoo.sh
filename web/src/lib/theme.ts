// Light, dark or the system's choice, remembered in this browser; and the brand colour from the panel's settings.

export type ThemeChoice = "system" | "light" | "dark";

export function storedTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem("sudoo.theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice: ThemeChoice) {
  const root = document.documentElement;
  // fade between light and dark instead of a sudden flash (styles/_base.scss, .theme-changing)
  root.classList.add("theme-changing");
  setTimeout(() => root.classList.remove("theme-changing"), 350);
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  try {
    localStorage.setItem("sudoo.theme", choice);
  } catch {
    /* private window: the choice lasts for this page only */
  }
}

export function applyBrandColor(color: string | undefined) {
  if (color && /^#[0-9a-f]{6}$/i.test(color) && color.toLowerCase() !== "#714b67") {
    document.documentElement.style.setProperty("--primary", color);
  } else {
    document.documentElement.style.removeProperty("--primary");
  }
}
