# Android Material 3 Expressive Design Language

This document is the visual source of truth for the browser homepage. It adapts the current Android Material 3 direction to a desktop-first bookmark workspace. The interface keeps the product's quiet green identity while using Android's semantic color roles, tonal surfaces, rounded containers, clear focus states, and restrained motion.

## Source and interpretation

- Primary reference: [Material 3](https://m3.material.io/)
- Android implementation reference: [Material 3 in Jetpack Compose](https://developer.android.com/develop/ui/compose/designsystems/material3)
- AI document format reference: [Google Stitch DESIGN.md](https://stitch.withgoogle.com/docs/design-md/specification/)
- This is an implementation interpretation, not Google's private Android source code.
- Android dynamic color is represented by a seeded green palette on the web. Keep all components on semantic roles so the seed can be replaced later.

## Visual principles

1. Use tonal surfaces to create hierarchy; avoid glass blur and decorative gradients.
2. Keep the search action dominant and easy to reach.
3. Use rounded containers with purposeful shape roles: 28px for the primary search field, 20px for cards, 16px for compact controls, and 999px for pills.
4. Preserve generous touch and pointer targets. Interactive controls are at least 40px tall and have visible keyboard focus.
5. Let bookmark icons and labels carry the content. Chrome stays quiet and low contrast.
6. Motion is short and spatial: 160–220ms, ease-out, with small elevation or translation changes.

## Color roles

### Light seed

| Role | Value | Use |
| --- | --- | --- |
| `primary` | `#42664c` | Main actions and active emphasis |
| `on-primary` | `#ffffff` | Text and icons on primary |
| `primary-container` | `#c5edca` | Selected and highlighted regions |
| `on-primary-container` | `#00210b` | Content inside primary containers |
| `surface` | `#f8fbf5` | Page background |
| `surface-container` | `#edf1eb` | Collection and control surfaces |
| `surface-container-high` | `#e7ebe5` | Search field and elevated cards |
| `surface-container-highest` | `#e1e5df` | Menus and dialogs |
| `on-surface` | `#191c19` | Primary text |
| `on-surface-variant` | `#414941` | Supporting text and icons |
| `outline` | `#717970` | Borders and dividers |
| `outline-variant` | `#c1c9bf` | Quiet separators |
| `error` | `#ba1a1a` | Destructive and offline states |

### Dark seed

Dark mode uses the same roles with dark tonal surfaces. Do not invert the light palette mechanically. `surface` is the darkest canvas, while `surface-container` and `surface-container-high` step upward to carry grouping and elevation.

## Typography

- Font stack: `Google Sans Text`, `Roboto`, `Noto Sans SC`, `system-ui`, sans-serif.
- Page title: 28–36px, weight 500, tight line height.
- Collection title: 16px, weight 600.
- Bookmark label: 13px, weight 500, maximum two lines.
- Supporting text: 12px, weight 400, line height 1.45.
- Labels and status text: 11px, weight 500.
- Use sentence case. Avoid decorative all-caps except for short metadata labels.

## Component rules

### Search field

- Height 56px, radius 28px, `surface-container-high` background.
- Leading search icon uses `on-surface-variant`.
- The submit control is a tonal pill inside the field; it becomes `primary` on hover and keyboard focus.
- Focus uses a 2px `primary` ring and does not rely on color change alone.

### Collection section

- The collection heading is a low-contrast row with a small numbered tonal marker, a title, count pill, ordering controls, and a flexible divider.
- Collapsed sections preserve their heading and use `primary-container` as the drop target signal.

### Bookmark item

- Treat bookmarks like launcher icons on a wallpaper: the tile itself is transparent by default.
- Do not add a second container behind a website favicon. The favicon is rendered at its natural size; the fallback glyph uses the primary color directly.
- Center the favicon and label for launcher-like scanning.
- Hover and focus reveal a quiet primary-container tint around the tile and lift it by 1px.
- Descriptions are supporting content shown on hover or focus; they must not compete with the label.

### Dialogs and menus

- Use `surface-container-highest`, 28px dialog radius, and a soft shadow.
- Destructive actions use `error` only for text/icon and a faint error container on hover.
- Menus expose keyboard focus and close on Escape.

## Responsive behavior

- Desktop: centered content with a maximum width of 1240px and 5–7 bookmark columns depending on viewport width.
- Tablet: reduce horizontal padding and keep at least 4 columns when space allows.
- Mobile: use 3–4 compact columns, reduce icon size, and keep the search field full width.
- Never hide the only way to search or open a bookmark.

## Do and don't

- Do use semantic tokens and shared component rules.
- Do keep light and dark roles structurally equivalent.
- Do use `:focus-visible` for keyboard users.
- Do preserve the existing drag-and-drop and context-menu behavior.
- Don't add large decorative blur fields or unrelated gradients.
- Don't use hard-coded colors inside individual components when a role exists.
- Don't use hover as the only way to discover an action.

## Agent prompt

Build the homepage as a calm Android Material 3 bookmark workspace. Use semantic green dynamic-color roles, tonal surfaces, 20–28px rounded containers, Google Sans/Roboto-like typography, clear focus rings, and short ease-out motion. Keep search prominent, make collections easy to scan, and keep bookmark chrome quiet. Preserve all existing data, drag-and-drop, context-menu, settings, backup, and offline behavior. When a new component is needed, define its light and dark semantic roles before writing markup or CSS.
