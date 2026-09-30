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

The engine suppressed correctly and the page did not show it. `_render_result`
handled two of the seven analysis kinds, so a `frequency` result rendered as a
heading, no counts, and this line:

> Siffrorna är exakta även när flera källor kombineras.

On the card where a category had been withheld, the only sentence told the
reader the figures were exact and showed none.

Fixed in #724, #725 and #726. `01_suppression.json` now renders:

```
frequency
  Grupper med färre än 5 patienter visas som <5. Ibland döljs även en grupp
  till, så att den första inte ska gå att räkna ut från summorna.
  Grupp    Patienter
  male     6
  female   <5
```

Three things changed to get there, and two of them were found by these specs
rather than by the rendering work:

* **#724** — all seven kinds render. `frequency`, `compare_groups` and
  `completeness` get tables, `correlation` gets a non-causal sentence,
  `over_time` gets a curve, and a histogram now gets the data table its own
  docstring calls "always rendered with the chart". An exactness line is only
  attached to a card that is showing something.
* **#725** — the coordinator was discarding every node note. `combine()` read
  `node_id`, `n_patients`, `may_pool` and `partials` from each run and never
  `notes`, so a node that explained why it returned nothing was never heard.
  One line per distinct note, attributed to the sources that raised it.
* **#726** — `Group.where` used a different operator vocabulary than cohort
  criteria (`lt` vs `<`) and validated neither. A group written with the symbol
  — the spelling the rest of the spec uses — passed validation, was rejected at
  every node, and took `compare_groups` out of the results with it. Both
  spellings now normalise; an unknown one is refused before any data is read.

The reader also now gets the linkage warning (`linkage: none` means a patient
may be counted twice), which was written, tested, and never called.

**Still true:** `over_time` cannot be exercised on synthetic data. `synth.build`
writes `effective_at: None` on every row, so there are no dated observations to
plot. The node says so and, since #725, the page repeats it.
