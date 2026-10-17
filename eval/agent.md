# Agent eval

Live agent at https://provenance-imzk.onrender.com, model gpt-4.1-mini, one question per drug on today's shortage list:

> <drug> is on the shortage list. What can we switch to, and is anything risky?

Each answer is checked against the graph.

| Check | Passed |
|---|---|
| Cites no FEI or import-alert date that is not in the graph | 18 of 18 |
| Names every flagged plant that makes the drug, with its alert date | 17 of 18 |
| Names a same-class option with no shared plant, when one exists | 18 of 18 |

Median time per answer: 2.253 s.

| Drug | Tools | Invented | Flagged missed | Safe option named |
|---|---|---|---|---|
| BUPIVACAINE HYDROCHLORIDE | check_substitute | none | none | yes |
| CARBOPLATIN | check_substitute | none | none | yes |
| CLONAZEPAM | check_substitute | none | 3003157498, 3004011473 | yes |
| DEXMEDETOMIDINE HYDROCHLORIDE | check_substitute | none | none | yes |
| DEXTROSE MONOHYDRATE | check_substitute | none | none | yes |
| DOPAMINE HYDROCHLORIDE | check_substitute | none | none | yes |
| FUROSEMIDE | check_substitute | none | none | yes |
| HYDROMORPHONE HYDROCHLORIDE | check_substitute | none | none | yes |
| KETOROLAC TROMETHAMINE | check_substitute | none | none | yes |
| LIDOCAINE HYDROCHLORIDE | check_substitute | none | none | yes |
| LIDOCAINE HYDROCHLORIDE ANHYDROUS | check_substitute | none | none | yes |
| LISDEXAMFETAMINE DIMESYLATE | check_substitute | none | none | yes |
| LORAZEPAM | check_substitute | none | none | yes |
| METHYLPHENIDATE HYDROCHLORIDE | check_substitute | none | none | yes |
| MIDAZOLAM HYDROCHLORIDE | check_substitute | none | none | yes |
| MORPHINE SULFATE | check_substitute | none | none | yes |
| ROCURONIUM BROMIDE | check_substitute | none | none | yes |
| ROPIVACAINE HYDROCHLORIDE | check_substitute | none | none | yes |
