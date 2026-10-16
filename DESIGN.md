# Provenance: design direction

Written by the agent on Fri Oct 16 because no direction was supplied. Achintya can override any line.

## Read
Reading this as: a working tool for a hospital pharmacist deciding on a substitute during a drug shortage, in the visual language of a printed public record (FDA notices, drug labels, chain-of-custody forms), dial ENERGY 1 / RHYTHM 2 / MOTION 2.

## The screen's job
One decision: "Is the substitute I am about to switch to going to fail for the same reason?" Everything on screen serves that question. No stat cards, no sidebar, no activity feed.

## Identity motif
The supply chain drawn as a three-column chain: drug in shortage, the plants that make it, the substitutes those plants also make. A shared plant is the knot in the chain. The same chain appears on every screen, so the product looks like itself.

## Type
- Public Sans for all text. Reason: it is the US Web Design System typeface; every record on screen is a US federal record, so the page reads as a public document.
- IBM Plex Mono only for identifiers (NDC, FEI, DUNS, dates). Reason: these are codes people copy into other systems; mono separates 0 from O and keeps digits aligned. Never used for headings.

## Colour
- Paper `#FBFAF7` (label stock), ink `#1B1F24`, secondary ink `#4A5560`, rule lines `#D9D6CF`.
- One accent: vermilion `#B3261E`, used only where a substitute shares a plant with the drug in shortage, and on the FDA import-alert flag. It always comes with a text label ("Same plant", "Import alert 06/01/2023"), never colour alone.
- No green. "Independent" is plain ink with a text label, so the only colour on screen means danger.
- Light theme only. Reason: a pharmacy workstation in a lit room, printed-record language. No toggle, so no broken dark mode.

## Shape and space
- Radius 2px on inputs and buttons, 0 on the chain diagram and tables. Reason: forms and records, not consumer cards.
- No shadows. Elevation is not a concept in a printed record; separation comes from rule lines and whitespace.
- Spacing scale 4, 8, 12, 20, 32, 52. Sections separated by 32 or 52, rows by 8 or 12.

## Motion (dial 2, one purpose)
- When an answer arrives, the chain draws once, left to right, in traversal order (drug, then plants, then substitutes), 600 ms total. Reason: it shows the multi-hop path the graph followed, which is the point of the product. It never loops.
- Everything else: hover and focus states only. `prefers-reduced-motion` turns the draw off.

## States (every data view)
- Empty: "Pick a drug from the FDA shortage list to check its substitutes."
- Loading: "Tracing plants for CISPLATIN..."
- Error: names what failed and what to do ("Graph not reachable at localhost:6379. Start FalkorDB with scripts/falkordb-wsl.sh.").
- No substitute: "No same-class substitute in the graph for this drug. The class may have one member, or labels list no plant."

## Banned here (antislop)
Gradients, glow, glass, pills, eyebrow badges, emoji, sparkle or robot icons, stat-card rows, fake numbers, arrows on every button, uppercase wide-tracked labels.

## Dial check
ENERGY 1: one focal point per screen (the chain). RHYTHM 2: the comparison row, the chain and the evidence table are three different compositions. MOTION 2: one draw per answer, nothing else moves.
