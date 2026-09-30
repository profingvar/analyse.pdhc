# #688 walkthrough — specs that force suppression

Paste either file into the box at `/analysis` and press run. Both are written
against the synthetic vocabulary (concept `x`, `demographics.sex`), so they
answer without any node configured — `ANALYSE_NODES` unset means the page runs
the real engine over `app/testing/synth`, 3 sources × 400 patients, seed 0.

They exist because every other example on the page returns comfortable numbers.
Nothing in the shipped example ever crosses `k_min`, so the walkthrough could
not judge the one thing the tool most needs to get right: what a person sees
when the answer is withheld.

## 01_suppression.json — one category withheld, one published

Cohort `x >= 18` selects 10 patients: 4 in cdr1, 4 in cdr2, 2 in cdr3. Every
source is on its own below `k_min = 5`; the pool is above it. That asymmetry is
the point of federating at all, so it is the case to look at first.

Engine output, verified 2026-09-30:

```
describe   pooled n=10, mean 19.94, ci95 [19.05, 20.83]
frequency  pooled {"male": 6, "female": "<k"}
```

`female` is 4 patients. It is withheld, `male` is not, and the pooled `describe`
still answers over all 10.

## 02_suppression_total.json — nothing survives

Cohort `x >= 20` selects 6 patients (2 / 3 / 1). Both categories fall under
`k_min`:

```
frequency  pooled {"male": "<k", "female": "<k"}
histogram  [("18–19", 0), ("19–20", 0), ("20–21+21–22+22–23+23–24", 6)]
```

The histogram is worth reading closely: the two empty bins are published as `0`
— a bin nobody is in discloses nobody — while the four bins that do hold
patients are merged into one label so the published count is 6 rather than
5 + 1. That is `merge_small_bins` doing its job, and #723 is where it was not.

## What these specs actually showed (2026-09-30)

The engine suppresses correctly. **The page does not show it.**

`_render_result` in `app/routes/analysis.py` handles two of the seven analysis
kinds — `describe` (sentence) and `histogram` (svg). A `frequency` result
produces a card with its heading, no counts, no sentence, and this line:

> Siffrorna är exakta även när flera källor kombineras.

So on the card where a category was withheld, the only sentence tells the reader
the figures are exact, and no figures are shown. That is worse than an empty
card — the reassurance is about numbers that are not on the page.

`frequency`, `correlation`, `compare_groups`, `over_time` and `completeness` all
render as that empty card. And `app/ui/sentences.py` already contains
`suppression_sentence(k_min, lang)`, written and tested for precisely this
moment, with no call site outside `app/ui/`. So do `comparison_sentence`,
`linkage_sentence`, `charts.data_table`, `charts.heatmap` and `charts.curve`.

This is #714's finding recurring one layer in: #722 gave `app/ui` a route, but
wired two kinds of seven, and the suppression sentence was not one of them.
Tracked as #724.
