# UI styles

`app/globals.css` contains Tailwind directives and shared design tokens. Import global feature styles once in `app/layout.tsx`.

- `controls.css`: shared controls and surfaces (`btn`, `input`, `card`, `chip`).
- `chat.css`: chat layout and bubbles.
- `markdown.css`: message content typography, scoped under `.markdown-message`.
- `motion.css`: reusable entrance animations and interaction feedback.

Keep Tailwind utilities alongside markup for simple layout. Put reusable component-specific behavior in colocated CSS modules, as in `components/ui/AnimatedDialog.module.css`. Keep feature selectors scoped to their feature; avoid growing a single global stylesheet.

Use the shared `--motion-*` duration and easing tokens for new animations. Durations shorten automatically for reduced motion; continuous animations need an explicit reduced-motion override. Keep exiting dialogs mounted until their animation finishes.
