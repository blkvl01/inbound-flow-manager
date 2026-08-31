# Flow Manager — PRODUCT.md

## Register
product

## Target users
HGL Group Hungary Ecommerce operations staff: warehouse supervisors and dispatchers who monitor inbound cargo receipt and outbound truck loading. Also used in TV/wall-display mode for passive monitoring on shared screens.

## Product purpose
A portable Python/Dash dashboard for real-time tracking of inbound E_COMM air freight items awaiting warehouse receipt, and outbound BUD-Pallets truck loading status. Runs locally as a portable `.exe` shared across a team, with a shared JSON state file for collaborative status updates.

## Brand personality
Operational, dark-mode, high-density. Glass/liquid aesthetic with strong visual hierarchy. Every pixel earns its place — this is a tool used under time pressure, not a demo. Fast scan at a glance, drill-down on demand.

## Anti-references
- Consumer SaaS dashboards (cream backgrounds, rounded cards, SaaS clichés)
- Enterprise BI tools (gray, corporate, low contrast)
- Any design requiring color-mix() CSS (portable renderer limitation)
- Continuous idle animations that degrade scroll/frame performance

## Strategic design principles
1. **Operational clarity first**: status and priority must be readable in < 1 second
2. **Glass, not blur-heavy**: use backdrop-filter sparingly (renderer perf)
3. **No emoji** — inline SVG mask icons only
4. **TV mode is a different surface**: full-screen, passive, read from 3+ meters — bigger text, fewer details, high contrast
5. **Portable renderer constraints**: no color-mix(), limited CSS features
