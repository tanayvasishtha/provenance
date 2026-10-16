# Provenance: design direction

Written by the agent on Fri Oct 16 because no direction was supplied. Achintya can override any line.

## Read (revised Sat 17 Oct after feedback that the first version looked too plain)
Reading this as: a working tool for a hospital pharmacist during a drug shortage, in the visual language of a pharmacy dispensing label, dial ENERGY 2 / RHYTHM 2 / MOTION 2.

## Identity
- Tall Man lettering for look-alike drug names (CISplatin, CARBOplatin), the convention pharmacies use to stop mix-ups. The demo's two drugs are a real look-alike pair, so the type carries the story.
- The trap verdict is drawn as an FDA boxed warning: heavy black frame, red heading, import alerts as small rotated stamps.
- The one loud surface is the dark teal tray holding the chain the graph walked. Everything else stays quiet.

## Colour (each colour has one meaning)
- Teal `#0B5E5E`: structure (label band, buttons, the graph's side). Tray `#083F42`.
- Red `#B8231A`: same plant or an FDA action. Always paired with text.
- Amber `#F2B441`: on the FDA shortage list now, and the drug being checked.
- Ink `#13212B` on paper `#F6F7F5`; steel `#4E5D68` for secondary text.
- All text pairs pass WCAG AA (lowest 5.24:1); tray edge lines 3.71:1 against the tray.

## Type
- Archivo (variable width) for the wordmark (wide, 900), drug names (condensed, 850) and headings. Chosen because condensed grotesques are what drug cartons and labels are set in.
- Public Sans for reading text: every record shown is a US federal record.
- IBM Plex Mono only inside Cypher blocks.

## Motion
The chain draws once per answer in traversal order; nothing loops; reduced motion turns it off.

## Delivery gate (Fri 16 Oct, local, replay mode)
- R-02 PASS: no em or en dash in any page text, README, or copy (grep returns 0).
- R-03 PASS: at 375px, page scrollWidth equals viewport width; tabs wrap to two rows; comparison stacks with the graph answer first; chain switches to the vertical layout and every label fits its box.
- R-17, R-36, R-38 PASS: every number on screen comes from `/api/meta`, the graph, or `eval/results.json`. Hospitals are labelled "Demo" and the README says they are synthetic tenants.
- R-24, R-26 PASS: four tabs are real anchors with matching panels; no other navigation.
- R-25 PASS: ink on paper 15.87:1, secondary 7.29:1, vermilion 6.26:1, white on vermilion 6.54:1 (contrast-check.py).
- R-27 PASS: every data view has empty, loading and error text that names the cause and the next step.
- R-32 PASS: all controls are native links, selects, buttons, textarea or details; `:focus-visible` draws a 3px ink outline; a skip link leads to main.
- R-35 PASS, click-through: each tab shows its panel and sets aria-current; empty drug submit is blocked by the required field; Rocuronium check shows the verdict, chain and evidence; "Show the Cypher" opens with the query; plant view for Intas lists 6 drugs and 3 hospital cards; risk view lists 25 plants; hospital change reloads 6 stocks; example button fills the question; Ask returns mode and a 2-step trace; Clear empties decisions. Console: no errors.
