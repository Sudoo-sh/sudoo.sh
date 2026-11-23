# Design

**Audience:** developers changing or restyling the web app.
**Covers:** the direction, the design tokens, type, materials, motion, charts, and how to make Sudoo.sh look like
your own product.

---

## Contents

1. [Direction](#1-direction)
2. [Where things are](#2-where-things-are)
3. [Tokens](#3-tokens)
4. [Type](#4-type)
5. [Materials and depth](#5-materials-and-depth)
6. [Motion](#6-motion)
7. [Charts](#7-charts)
8. [Copy](#8-copy)
9. [Restyling Sudoo.sh](#9-restyling-sudoosh)
10. [Checklist for a change](#10-checklist-for-a-change)
11. [Sources](#11-sources)

---

## 1. Direction

| | |
| :--- | :--- |
| **Product** | A panel that runs a live Odoo for every Git branch on your own server. |
| **Character** | Exact, calm, a little warm. |
| **World** | A server room on a night shift: terminals, status lights. Odoo's plum is the brand. |
| **Moments** | The live charts and the terminal log: the only places with rich colour. |
| **Signature** | The mark: a plum ring with a dot and a blinking text cursor on a white tile (light plum on graphite in dark mode), and a status dot that moves only while something is really running. |

The chrome (sidebar, buttons, borders, labels) stays quiet and graphite, so the content reads first. The accent
marks the primary action, the selection and the one figure that matters. Nothing glows, nothing is decorated for
its own sake, and the screen works without any animation.

**Familiar places, our own look.** People who come from Odoo.sh find things where they expect them: a project's
branches grouped by stage (production, staging, development) with a status dot and version on each, drag-and-drop
between the stages, a branch that opens on its history with its command toolbar beside its name, the instance's tabs
in the usual order, and a Builds page with each branch's recent builds side by side by stage. The structure is the
familiar one; the look, the layout details and the words are Sudoo.sh's own (for example **Passed** and **Replaced**
for a build, **Search branches**, **Open Odoo**), never copied.

---

## 2. Where things are

```
web/src/styles/
├── globals.css        Tailwind (theme and utilities) for the shadcn/ui components, shadcn's colour names read from the tokens
├── vaul.css           the drawer library's own rules (it adds them at run time, which the Content-Security-Policy refuses)
├── main.scss          the panel's styles below, in the "panel" cascade layer, then _look.scss
├── _tokens.scss       every colour, size, radius, shadow, material and motion value, light and dark
├── _mixins.scss       hover (only for real pointers) and press feedback
├── _base.scss         page, type, helpers
├── _motion.scss       page transitions, the motion system's utilities, keyframes, reduced motion
├── _components.scss   cards, status, forms, tabs, tables, toasts, logs, the shell, and the classes premium pages use
├── _layout.scss       sidebar, top bar, page header
├── _pages.scss        sign-in, figures, charts, instance cards, settings rows, help pages
└── _look.scss         the Sudoo.sh look on the shadcn components (buttons, fields, lists, menus, dialogs, sheets, tables)
web/src/components/ui/   shadcn/ui components, added with the shadcn CLI (components.json) and used unchanged
web/src/components/app/  the panel's pieces built on them (basics.tsx, overlay.tsx, select.tsx, OtpInput.tsx, Brand.tsx)
web/src/components/app/motion.tsx   the motion system's wrappers (Reveal, Stagger, Presence, AnimatedNumber, MorphIcon, TabInk, useHero)
web/src/lib/motion.ts   the same tokens for the Web Animations API, reduced motion, interruptible play() and FLIP
web/src/components/charts/LiveChart.tsx   the charts
```

Components take their look only from tokens. A component never names a raw colour, so a theme is one file.

The controls are [shadcn/ui](https://ui.shadcn.com) components (Radix, the "vega" preset, right to left on). The files
in `components/ui` stay as the CLI writes them (`npx shadcn@latest add <name>` adds one); the panel's look comes from
`_look.scss`, keyed on the `data-slot`, `data-variant`, `data-size` and `data-state` attributes the components carry.
The cascade layers keep the two apart: the panel's own styles sit below the components' utility classes, and
`_look.scss` sits outside the layers, above them. Tailwind's Preflight is left out; the components get the part of
it they need in `globals.css`, so the rest of the panel keeps its own base. Dialogs are shadcn's Dialog, and its
Drawer (from the bottom edge) below 640px; the sidebar is a Sheet up to 960px; every list to choose from is a Select.

---

## 3. Tokens

| Group | Tokens | Notes |
| :--- | :--- | :--- |
| Accent | `--brand`, `--brand-ink`, `--on-brand`, `--brand-soft`, `--brand-ring` | `--brand` follows `BRAND_COLOR` (Settings, General) |
| Neutrals | `--bg`, `--surface`, `--raised`, `--hairline`, `--hairline-strong`, `--text`, `--text-2`, `--text-3` | Graphite in both themes; three text roles only |
| Meaning | `--positive`, `--negative`, `--caution`, `--working`, `--idle` | Used for status only, never for decoration |
| Charts | `--series-1` (the accent), `--series-2`, `--series-grid` | The first series leads, others stay quiet |
| Type | `--font`, `--mono`, `--text-12` to `--text-32` | See [Type](#4-type) |
| Shape | `--radius-tag` 6, `--radius-control` 8, `--radius-card` 14, `--radius-panel` 16 | Inner radius = outer radius minus the padding |
| Space | `--s1` to `--s12` | A 4 px grid; equal things get equal sizes |
| Materials | `--material`, `--material-blur` | Translucent chrome, see [Materials](#5-materials-and-depth) |
| Motion | `--spring`, `--spring-bounce`, `--ease`, `--ease-move`, `--ease-drawer`, `--dur-micro`, `--dur-ui`, `--dur-enter`, `--dur-surface`, `--dur-exit`, `--stagger-step`, `--stagger-max`, `--lift`, `--rise`, `--shadow-lift` (and the older `--press`, `--quick`, `--menu`, `--enter`, `--sheet`) | See [Motion](#6-motion) |

Dark mode redefines the same tokens (the `dark` mixin in `_tokens.scss`): it follows the system, or the choice made
with the theme button at the bottom of the sidebar. A theme change fades over 300 ms instead of flashing.

`prefers-reduced-transparency` makes the materials solid, and `prefers-contrast: more` makes hairlines and muted
text stronger.

---

## 4. Type

- **The platform's own face first:** San Francisco on Apple devices (with its optical sizes), Inter elsewhere,
  shipped with the app. Figures, versions, commit hashes and logs use the monospace face.
- **Tracking follows size:** headings are tightened (`-0.022em` on page titles, `-0.025em` on large figures), body
  text stays near zero, the smallest text is opened slightly (`0.01em`).
- **Weights:** 400 for text, 500 for labels and emphasis, 600 for headings. Hierarchy comes from size and colour
  more than weight.
- **Numbers** always use tabular figures, so changing values do not jitter.
- **Sentence case** everywhere; no uppercase labels with wide tracking.

---

## 5. Materials and depth

- The **sidebar** and the **top bar** are translucent (`--material`, a blur of 20 px with raised saturation); content
  scrolls under the top bar, and a soft fade marks where they meet instead of a hard line.
- **Sheets** (dialogs) sit on a dimming scrim, because they are a task that needs attention. The **drawer** (a job's
  details) slides in from the right edge over a dimmed page.
- **Depth on light** comes from small neutral shadows; **on dark** from surface steps and hairlines, without shadows.
- No coloured shadows, glows or gradients on surfaces.

---

## 6. Motion

**Only as much motion as the action is rare.** Moving between pages happens all day, so it is short and small: only
the content moves, the frame stays still (see the table). Menus, sheets and toasts appear occasionally and get a
short, purposeful animation. The first-run and sign-in screens, seen rarely, bring their blocks in one after the other.

Every animation does one of three jobs: it **explains a change** (where something came from or went), it **answers**
(a press, a hover, a success, a mistake), or it **keeps continuity** (moving between pages, a list that changes).
Nothing moves for decoration, and nothing loops except something that is loading or running right now.

| Thing | Motion | Curve and duration |
| :--- | :--- | :--- |
| Any press (buttons, cards, rows, choices, segments) | Scales to 0.97 (0.985 for large surfaces) on press, before the click | `--ease`, 160 ms |
| Menus and lists to choose from | Grow from the button that opened them (scale 0.96 from `transform-origin` at the trigger, which follows the side they line up with, so Arabic mirrors it), shrink back into it; drawn above the page (a portal), below the button or above it when there is no room | `--spring`, 180 ms in; `--ease`, 120 ms out |
| Dialogs | Not anchored to a trigger: the backdrop fades while the panel pops in gently in the centre (scale 0.96, opacity, 4 px blur) | `--spring`, 300 ms in (backdrop 220 ms); `--ease`, 180 ms out |
| Drawer | In from the right edge and out the same way (spatial consistency) | `--ease-drawer` (the iOS sheet curve), 400 ms in, 280 ms out |
| Toasts | Slide in from the inline-end edge (the right; the left in Arabic) and out the same way; the others glide to their new places (FLIP) instead of jumping; a success draws its check | `--spring`, 300 ms in; `--ease`, 180 ms out |
| Moving between pages | The View Transitions API (`viewTransition` on links and `navigate`). One page at a time: the old view fades out, and only when it is gone does the new one fade in, rising 4 px, so two titles or two intros are never on screen together. Nothing else is shared or cross-faded between two pages (the one exception: an instance card's name moving into its page's title, below); only the frame stays still: the sidebar, the top bar (its breadcrumb fades out and in with the content) and the Help pages' list, where the highlight simply moves. Between the tabs of one page (an instance's tabs, Plugins), only the tab's content (`.tab-panel`) fades out and in; the title and the tabs stay (`lib/navigation.ts` sets `data-nav="tab"` or `"page"` on `<html>` before the old view is captured). The content is the viewport's snapshot, so a scrolled page does not jump. Browsers without the API change the page at once | 120 ms out (ease-out), then 190 ms in (`--ease`); reduced motion: 80 ms out, 120 ms in, no rise |
| Another branch of a project | As between tabs (`data-nav="branch"`), plus the branch's part of the project's header: its name and tags (`.ph-branch`) and its command (`.bc-panel`) cross-fade in place, the old one rising 4 px as it leaves and the new one rising 4 px into place. Both keep their box (full width, fixed height), so nothing around them moves. Without the API they fade in | 180 ms (`--branch-swap`, `--ease`); reduced motion: the same fade, no slide |
| Loading placeholders | Where the shape of what is coming is known, a skeleton in that shape (`Skeleton`, `SkeletonText`) with a soft light sweeping across it (a transform, in the reading direction) instead of a spinner. Skeletons and a page's spinner stay invisible for 150 ms, then fade in, so a quick load never flashes; a 2 px bar at the top appears after 150 ms while a page's code is still loading | `--ease`, `--quick` after 150 ms |
| Segmented control | The thumb slides to the chosen option | `--ease-move`, 280 ms |
| Switch | The thumb stretches while pressed, as on iOS | `--ease`, 160 ms |
| Tabs | The underline slides to the chosen tab (`TabInk`, a 1 px line scaled to the tab's width: transform only) | `--spring`, 300 ms |
| Lists and grids | On first load the items come in one after the other (fade and a 6 px rise), 25 ms apart and at most eight steps; an item added later comes in on its own; an item that stays is never animated again. `Presence collapse` opens and closes the room of an item added or removed | `--spring`, 220 ms; leaving 140 ms |
| Cards that open something | Lift 2 px under the pointer with a softer, wider shadow (dark mode: the hairline firms up); a press pushes them back down | 160 ms |
| An instance card opening its page | Its name moves and grows into the page's title while the rest of the page does its out-then-in fade (shared element: `useHero`); going back it returns to its card | `--spring`, 300 ms |
| Numbers | Tween to the new value in tabular figures; a dashboard's figure counts up from 0 when it first arrives | ease-out, 600 ms |
| Charts | Drawn at once (Chart.js draws nothing twice), then the chart fades in | `--ease`, 300 ms |
| Copying, and other icons that change | The icon turns into the next one in place (a copy icon into a check, the theme's sun into a moon): scale 0.5, fade and a 2 px blur | `--spring-bounce`, 180 ms in; 140 ms out |
| A field that turns invalid | Shakes once, 4 px each way; its message comes in under it | ease-out, 280 ms |
| Keyboard focus | The ring draws in from a little further out | `--ease`, 180 ms |
| Empty states | Settle in, the icon popping softly | `--spring`, 220 ms |
| Progress | Constant motion | linear |
| Status dot of something running (creating, starting, working, a running job) | The only looping motion besides loading: a soft ring breathes out of the dot; it says something is happening right now. Queued, online, paused and failed stay still | 1.6 s |

Implementation rules:

- **Springs** for things arriving and moving: `--spring` (a damping ratio of 0.8, about 1.5% overshoot) and, for
  small things that pop (an icon, a picked-up row), `--spring-bounce` (0.62, about 8%). They are CSS `linear()`
  curves; where `linear()` is not supported they fall back to `--ease`.
- **Ease-out** for things leaving, **ease-in-out** for things moving on screen, never ease-in.
- **Durations:** 120 to 220 ms for most of the interface (`--dur-micro`, `--dur-ui`, `--dur-enter`), up to 300 ms for
  large surfaces (`--dur-surface`); **exits are quicker than entrances** (`--dur-exit`, 140 ms).
- **Dialogs, sheets, menus and lists** come in and leave with the components' enter and exit animations, tuned in
  `_look.scss` to the values above; the element is removed once the exit animation has played
  (`components/app/overlay.tsx`). Toasts and the other pieces use transitions (`@starting-style`).
- **Only `transform` and `opacity`** (and a small `filter: blur`) are animated; never width, height or position,
  except the segmented thumb's width and the room `Presence collapse` opens (`grid-template-rows`). A focus ring's
  offset and a check's stroke are the other small exceptions.
- **Interruptible:** transitions rather than queued animations wherever something can change its mind; `play()` in
  `lib/motion.ts` cancels the animation it started on an element before it starts the next, so quick repeated
  presses never queue motion.
- **Nothing appears from `scale(0)`:** entries start at 0.96 to 0.97.
- **A card that opens something** (a build opens its commit on GitHub) is one link: its title is the `<a>`, and an
  `::after` stretches it over the card. What acts inside the card (a copy button, **Open Odoo**, a link to the history)
  sits above it (`position: relative; z-index: 1`), so it does only what it says; there is never an `<a>` inside
  another. Pointing at the card underlines its title and shows an external-link mark (a link that opens a new tab says
  so in its tooltip); the keyboard focus rings the whole card. Without a target (a repository not on GitHub) the card
  is plain text.
- **Commit hashes** are seven characters in the monospace face (`components/CommitHash.tsx`): a press copies the whole
  hash, the arrow beside it opens the commit on GitHub.
- **Hover styles only for real pointers** (`@media (hover: hover) and (pointer: fine)`), so taps on a phone do not
  leave things highlighted.
- **Reduced motion** keeps changes understandable: movement is removed and replaced by short fades (the page
  transition stays an out-then-in fade, quicker and without its rise; loading placeholders keep their 150 ms delay,
  which is not movement). No rise, lift, stagger, shake, pop, sliding underline, flying title or shimmer; numbers
  show their new value at once; icons that change cross-fade; the theme fades instead of growing in a circle.
- **A new page starts at its top,** and going back returns to where you were (`ScrollRestoration`, in
  `components/PageMotion.tsx`).
- **Status chrome only when it matters:** no "live" or "synced" badge; a small "Reconnecting" notice appears only
  when the connection to the server has been lost for more than three seconds.

### The motion system

The tokens live in `_tokens.scss`, the CSS in `_motion.scss`, the script side in `lib/motion.ts` and the wrappers in
`components/app/motion.tsx`. A page wraps what it already draws; the shared components (buttons, menus, dialogs,
toasts, tabs, lists to choose from, cards, tags, empty states, skeletons) already move this way.

| Token | Value | For |
| :--- | :--- | :--- |
| `--spring` | `linear()` spring, damping 0.8 | Things arriving and moving |
| `--spring-bounce` | `linear()` spring, damping 0.62 | Small things that pop |
| `--dur-micro` | 120 ms | Hover, focus, colour, a menu leaving |
| `--dur-ui` | 180 ms | Menus, popovers, icons that change, a toast leaving |
| `--dur-enter` | 220 ms | List items, cards, a toast arriving |
| `--dur-surface` | 300 ms | Dialogs and other large surfaces; the most anything takes |
| `--dur-exit` | 140 ms | Anything leaving |
| `--stagger-step`, `--stagger-max` | 25 ms, 7 | A list's items one after the other, eight steps at most |
| `--lift`, `--shadow-lift` | -2 px, a soft shadow | A card under the pointer |
| `--rise` | 6 px | How far something arriving travels |

| Wrapper | Use it for |
| :--- | :--- |
| `<Reveal index>` | One block that comes in when it is first drawn (the n-th of a list waits its turn) |
| `<Stagger>` (or the class `stagger-in`) | A list or grid whose items come in one after the other on first load; `.instance-grid`, `.project-grid` and the figure grids (`.grid-4.stagger`, `.grid-5.stagger`) already do |
| `<Presence show collapse>` | Something that appears and disappears: it stays while it leaves, and with `collapse` the room it takes opens and closes |
| `<AnimatedNumber value from format>` | A figure that changes (tabular figures; `from={0}` counts up the first time) |
| `<MorphIcon icons active>` | An icon that turns into another in place (copy into check) |
| `<SuccessCheck>` | A success that draws its check |
| `<TabInk>` | Inside a `.tabs` row: the underline slides to the chosen tab |
| `useHero(key)` and `startHero(key, element)` | One element that moves from one page into the next (a card's title into the page's title) |
| `play()`, `shake()`, `glide()` (`lib/motion.ts`) | Web Animations with the same tokens; interruptible; FLIP for a list that reflows |

Do:

- Give every animation a reason you could say in one sentence (where it came from, that it worked, what changed).
- Use the tokens, never a new duration or curve; if one is missing, add it to `_tokens.scss` and here.
- Make the leaving quicker than the arriving, and let a new action interrupt the one before.
- Check light, dark, Arabic (things that come from a side come from the other side) and reduced motion.

Don't:

- Animate width, height, top or left (use transform), or a shadow on many elements at once.
- Loop anything that is not loading or running; pulse a status that is only waiting.
- Stagger more than eight steps, or animate an item again because the list re-rendered.
- Add an animation library: CSS first, then the Web Animations API.

---

## 7. Charts

[Chart.js](https://www.chartjs.org/) draws every chart (`components/charts/`): `LiveChart` (lines over time),
`Sparkline` (the line inside a figure card), `Bars` (one bar per instance) and `Ring` (parts of a whole).

- **Each chart answers its own question;** no figure is drawn twice on a page. On the Instances page: the figure
  cards (processor, memory, disk, the panel's own memory, instances running of the capacity), then requests and errors of all instances, instances by state,
  response time, and which instances use the most (processor, memory or disk, with a switch).
- **Figure cards carry a line** of the last hour under the figure: no axes, no labels, no tooltip. The figure says
  the value; the line says the direction.
- **One accent per chart:** the first series is the accent with a soft gradient under it; a second series is quiet
  (`--series-2`, or `--negative` for errors). In bars only the largest is the accent. Rings use the status colours.
- **Muted grid and monospace labels;** the time axis shows seconds while the chart covers only a few minutes.
- **Live without redrawing:** each sample from the event stream is written into the chart, and the points glide to
  their new places (300 ms). Colours are read from the tokens (`charts/theme.ts`) and follow the theme.
- **Light to draw:** a chart is drawn at once with the hour it was given (no entrance animation of Chart.js,
  nothing drawn twice; only the finished chart fades in),
  draws an hour from at most 360 points (sparklines 120, LTTB decimation keeps the shape), and gives its points
  fixed colours, so a page with several charts stays responsive on a slow phone ([Performance](PERFORMANCE.md)).

---

## 8. Copy

- Titles name the place: "Instances", "Settings". No greetings, no exclamation marks.
- Short labels without a full stop: "Search", "Save", "Use .env". Sentences in descriptions keep their full stops.
- A figure says what it compares to: "6% average, last hour", "of 3".
- Errors say what is wrong and what to do, in the user's words.

---

## 9. Restyling Sudoo.sh

| To change | Do this |
| :--- | :--- |
| The accent colour | Settings, General, **Accent colour** (or `BRAND_COLOR` in `.env`); it also colours the e-mails |
| Neutrals, radii, spacing, motion | Edit `web/src/styles/_tokens.scss` (both the light block and the `dark` mixin), then rebuild: `docker compose build web` on the server, or `npm run build` |
| Fonts | `--font` and `--mono` in `_tokens.scss`; add the font files with `npm install @fontsource/<name>` and import them in `web/src/main.tsx` |
| One component | Its block in `_components.scss` or `_pages.scss`, named after the component |
| The e-mails | Their texts on the **E-mail templates** page ([Admin guide](ADMIN_GUIDE.md#17-e-mail-templates)); their frame is `src/sudoo/mail/templates/layout.html`, or your own `layout.html` in the folder named by `MAIL_TEMPLATES_DIR` ([Configuration](CONFIGURATION.md)) |
| The logo mark | `web/public/favicon.svg` is the drawing; `web/src/components/app/Brand.tsx`, `src/sudoo/instances/mark.svg` (status pages), `docs/images/brand/` and the e-mail header (`src/sudoo/mail/templates/layout.html`, drawn in HTML because Gmail removes SVG) repeat it. A test checks the copies match |

Working on the web app: `cd web && npm install && npm run dev` serves it with live reload on port 5173, using a
backend on `127.0.0.1:9002` (see `web/vite.config.ts`; set `SUDOO_API` to use another one, for example through an
SSH tunnel to a server).

---

## 10. Checklist for a change

- [ ] Every value comes from a token, on the 4 px grid; equal things have equal sizes.
- [ ] Icons from lucide at 16 px with a 1.5 stroke, coloured like their text.
- [ ] No gradients on surfaces, glows, coloured shadows or emoji.
- [ ] Sentence case, short labels without full stops, no exclamation marks; our own words, not another product's.
- [ ] Pressable things scale on press; hover styles are behind the hover mixin.
- [ ] Motion follows the table in [Motion](#6-motion); it works with reduced motion.
- [ ] Light and dark both checked; numbers tabular.
- [ ] The page makes sense at phone width.

---

## 11. Sources

- [Apple, *Designing Fluid Interfaces* (WWDC 2018) and *The Details of UI Typography* (WWDC 2020)](https://developer.apple.com/videos/),
  through the `apple-design` notes in [emilkowalski/skills](https://github.com/emilkowalski/skills) (MIT licence).
- Emil Kowalski's animation standards and design-engineering notes, in the same repository.
- [unslop](https://github.com/heydamir/unslop) by Damir Sokolovsky ([@heydamir](https://x.com/heydamir)), CC BY 4.0:
  the direction, the redline passes and the checklist behind the quiet chrome.
