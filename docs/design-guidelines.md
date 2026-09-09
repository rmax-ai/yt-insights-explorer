# YT Insights Explorer: UI Design Guidelines

## 1. Design Principles

*   **Provenance is Paramount:** Every claim, quote, and idea must visibly anchor to its source video. There are no orphaned insights; provenance is treated as primary metadata, not a footer footnote.
*   **Density over Paging:** Optimize for scanning large volumes of text. Use tables, compact cards, and multi-column layouts on desktop. Expose metadata (tags, counts, flags) inline rather than hiding it behind clicks.
*   **Text as Interface:** Rely on typography, indentation, and alignment to create structure. Avoid unnecessary boxes, heavy backgrounds, or decorative borders that compete with the content.
*   **Calm, Analytical Aesthetic:** The visual language should evoke a technical journal or internal developer tool (e.g., Stripe Docs, ACM Queue). No gradients, no "AI sparkles," no motion.
*   **Static-Native:** Embrace the static architecture. Navigation relies on pre-generated hard links (`<a href="../tags/architecture.html">`), and data visualizations use precomputed inline SVGs or CSS grids, requiring zero client-side rendering overhead.

## 2. Information Architecture & Navigation Model

**Global Navigation (Fixed Left Sidebar)**
*   **Home:** Corpus overview, recent videos, high-level summary stats.
*   **Trends:** Temporal analytics (tag frequency, insight types over time).
*   **Concepts:** A-Z tag vocabulary and static concept-connection matrices.
*   **Ideas:** Filterable inventory of Deep Dives, Article Ideas, and Project Ideas.
*   **Claims:** Verification inbox (flags `verification_needed: true` vs `false`).
*   **Videos:** Chronological index of the 73 source videos.

**Page Archetypes & Cross-linking Strategy**
*   **Video Detail Page (`/videos/id.html`):** The canonical source of truth. Contains all insights, claims, and ideas extracted from one video. *Cross-links to:* Tag pages (via tag pills), Concept pages.
*   **Tag/Concept Page (`/tags/id.html`):** Aggregates all insights across the corpus tagged with this concept. *Cross-links to:* Source Video pages (via provenance links on every insight card).
*   **Breadcrumbs:** Mandatory on all detail pages. Format: `Section / Parent / Current` (e.g., `Videos / Building LLM Pipelines / Insight`).

## 3. Layout System

*   **Page Grid:** 2-column layout on desktop. Fixed left sidebar (240px) + scrollable main content area.
*   **Content Width:** Main content area is fluid but capped at `max-width: 960px` to maintain optimal reading line lengths (65-75 characters). Center the content column within the remaining viewport space.
*   **Density Guidance:**
    *   *Aggregations (Claims, Ideas):* Use dense data tables.
    *   *Insights:* Use bordered cards with distinct internal hierarchy.
    *   *Connections:* Use adjacency lists (multi-column text lists) rather than node-link diagrams.
*   **Breakpoints:**
    *   `> 1024px` (Desktop): Sidebar left, 960px content right.
    *   `< 1024px` (Tablet/Mobile): Sidebar collapses to a top sticky header with a hamburger menu. Content takes 100% width with 16px padding.

## 4. Design Tokens

Use these exact values as CSS Custom Properties (`:root`).

| Token Name | Hex Value | Usage |
| :--- | :--- | :--- |
| `--bg-base` | `#0D0E12` | Main application background |
| `--bg-surface` | `#16181D` | Cards, sidebar, tables |
| `--bg-elevated` | `#20232A` | Hover states, search dropdown, floating menus |
| `--text-primary` | `#EDEDF0` | Body text, headings, primary data |
| `--text-secondary` | `#9CA3AF` | Metadata, timestamps, table headers |
| `--text-tertiary` | `#6B7280` | Disabled states, placeholder text, empty states |
| `--border-subtle` | `#2A2D35` | Dividers, standard card borders |
| `--border-focus` | `#60A5FA` | Keyboard focus rings (2px solid) |
| `--accent-primary` | `#E5E7EB` | Primary buttons, active nav items |

**Semantic & Insight Type Colors (Foreground text/icons on dark backgrounds):**
*   `--type-architecture`: `#60A5FA` (Blue)
*   `--type-mechanism`: `#2DD4BF` (Teal)
*   `--type-mental-model`: `#A78BFA` (Purple)
*   `--type-practice`: `#4ADE80` (Green)
*   `--type-empirical`: `#FBBF24` (Amber)
*   `--type-failure-mode`: `#F87171` (Red)
*   `--type-prediction`: `#22D3EE` (Cyan)
*   `--type-tradeoff`: `#FB923C` (Orange)
*   `--status-verified`: `#34D399` (Muted Green)
*   `--status-unverified`: `#F87171` (Red)

**Typography & Spacing:**
*   `--font-sans`: `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`
*   `--font-mono`: `"JetBrains Mono", "Geist Mono", SFMono-Regular, monospace`
*   `--space-1` to `--space-8`: `4px`, `8px`, `12px`, `16px`, `24px`, `32px`, `48px`, `64px`
*   `--radius-sm`: `4px` (Tags, badges)
*   `--radius-md`: `8px` (Cards, search bar)

## 5. Component Inventory

| Component | Anatomy & Content | Rules & States |
| :--- | :--- | :--- |
| **Global Nav** | Vertical list. Icon + Label. Active state indicator (left border). | Hover: `--bg-elevated`. Active: `--text-primary`, left border `--accent-primary`. |
| **Insight Card** | **Header:** `[Glyph] Type` (colored) • Novelty • Strength.<br>**Body:** Title, `why_it_matters` text.<br>**Footer:** Tag pills, Provenance link (Video title). | Border: 1px solid `--border-subtle`. Bg: `--bg-surface`. No drop shadows. |
| **Evidence Quote** | Left border (2px solid `--border-subtle`), monospace font, slightly indented. | Font size: 0.9em. Color: `--text-secondary`. |
| **Verification Badge** | Pill shape. Text: `Verified` or `Verification Needed`. | Unverified uses `--status-unverified` with 10% opacity bg. |
| **Project Fit Pill** | Small badge for `gatehouse`, `movement-lab`, `beyond-evals`, `new`. | Monospace, uppercase, 10px font. Outline only. |
| **Tag Pill** | Text + Count (e.g., `LLM-ops 12`). | Bg: `--bg-elevated`. Hover: `--border-subtle` changes to `--text-secondary`. |
| **Video Card** | Title, Channel, Date, Insight Count, External YT Link icon. | Used in grids on the Home/Videos pages. |
| **Data Table** | Columns for Ideas/Claims. Sticky header. | Bottom border on rows. Zebra striping disabled. Hover row: `--bg-elevated`. |
| **Connections Graph** | **Static Adjacency List:** Concept name on left, comma-separated linked concepts on right. | Do not use force-directed SVG graphs. Use CSS grid lists for scannability. |
| **Provenance Footer** | Present on every card/insight. Right-aligned link to source video. | Icon: Arrow-up-right. Color: `--text-tertiary`. |

## 6. Trends & Data-Viz Guidance

*   **No JavaScript Libraries:** Do not use Chart.js, D3, or Recharts.
*   **Bar Charts (Tag Frequencies):** Implement using standard HTML/CSS. Use a definition list (`<dl>`) where the `<dd>` contains a `<div>` with an inline style `width: X%` and background color `--bg-elevated`.
*   **Trend Lines (Timeline):** Precompute inline `<svg>` sparklines during the Python build step. Inject the raw `<path d="...">` directly into the HTML. Stroke color: `--border-subtle`.
*   **Chart-junk Rule:** No grid lines, no tooltips (since JS is restricted), no legends if the axis is self-explanatory. Label the start and end of axes explicitly.

## 7. Search & Filtering UX

*   **Architecture:** Client-side vanilla JS that fetches a precomputed `search_index.json` containing the entire corpus text and metadata.
*   **UI Pattern:** Command Palette style. A wide search input at the top of the page. Results appear in a dropdown or take over the main content area.
*   **Result Grouping:** Group search results by type (Videos, Insights, Claims, Ideas) using sticky sub-headers.
*   **Filtering:** Use static HTML `<select>` dropdowns for "Verification State" and "Project Fits" on the Ideas/Claims pages. Use vanilla JS to toggle `display: none` on table rows based on data-attributes (e.g., `<tr data-fit="gatehouse">`).
*   **Environment Note:** Because fetching `search_index.json` via `file://` triggers CORS/security restrictions in modern browsers, explicitly document that the site must be viewed via a local server (e.g., `python -m http.server`).

## 8. Typography & Content Style

*   **Insight Type Glyphs:** Do not rely on color alone to distinguish the 8 insight types. Prepend a bracketed monospace glyph to the type label:
    *   `[A]` Architecture
    *   `[M]` Mechanism
    *   `[O]` Mental Model
    *   `[P]` Practice
    *   `[E]` Empirical Result
    *   `[!]` Failure Mode
    *   `[>]` Prediction
    *   `[~]` Tradeoff
*   **Reading Rhythm:** Use `line-height: 1.6` for standard body text. Add `margin-bottom: 1.5em` to paragraphs.
*   **Data vs. Prose:** Use `--font-sans` for prose (`why_it_matters`, `generalization`). Use `--font-mono` for structured metadata, quotes, tags, and project fits.

## 9. Accessibility & Performance

*   **Contrast:** All text colors specified in the token table meet WCAG AA (4.5:1) contrast ratios against `--bg-base` and `--bg-surface`.
*   **Focus States:** Every interactive element (links, buttons, search input) must have a visible focus state: `outline: 2px solid var(--border-focus); outline-offset: 2px;`.
*   **Page Weight:** Target `< 50KB` of CSS and `< 20KB` of JS. The heaviest asset should be `search_index.json`.
*   **Reduced Motion:** As a static, calm interface, there are zero transitions or animations by default.

## 10. Anti-Patterns (What to Avoid)

*   **No "AI" Aesthetics:** Avoid purple/pink gradients, glowing borders, or sparkle emojis. Treat the LLM output as raw data, not magic.
*   **No Pure Black:** Never use `#000000` for backgrounds. It causes eye strain and smearing on OLED screens. Stick to the `--bg-base` (`#0D0E12`).
*   **No Hidden Navigation:** Do not use hamburger menus on desktop. The left sidebar must remain permanently visible.
*   **No Horizontal Scrolling (Carousels):** All lists of videos or cards must wrap to the next line (CSS Grid/Flexbox wrap) or stack vertically.
*   **No Client-Side Routing:** Do not use React Router or History API hijacking. Let the browser handle standard `<a>` tag navigation between the static HTML pages to ensure instantaneous, native page loads.