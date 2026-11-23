// The theme button: system, light, dark in turn; the new theme grows in a circle from the button.

import { Monitor, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { flushSync } from "react-dom";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import { applyTheme, storedTheme, type ThemeChoice } from "@/lib/theme";
import { revealTheme } from "@/lib/theme-reveal";
import { Button } from "./app/basics";
import { MorphIcon } from "./app/motion";

export function ThemeButton() {
  const [theme, setTheme] = useState<ThemeChoice>(storedTheme);
  const next: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
  // the icon turns into the next one in place
  const icon = <MorphIcon icons={{ system: <Monitor />, light: <Sun />, dark: <Moon /> }} active={theme} />;
  const label = useT(common).theme[theme];
  return (
    <Button
      variant="ghost"
      size="sm"
      icon={icon}
      title={label}
      aria-label={label}
      onClick={(event) => {
        const choice = next[theme];
        // the new theme grows in a circle from this button; with reduced motion or an older browser it fades
        void revealTheme(event.currentTarget, (revealed) => {
          flushSync(() => setTheme(choice));
          applyTheme(choice, !revealed);
        });
      }}
    />
  );
}
