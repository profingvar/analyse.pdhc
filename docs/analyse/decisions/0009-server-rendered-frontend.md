# ADR-0009 — server-rendered frontend, no JavaScript build chain

Status: accepted · Date: 2026-09-23 · Decided by: operator
Ticket: #643 q6 · Phase 3 (#655–#658)

## Context

The brief proposes React with TypeScript and Vega-Lite. The repo is
server-rendered Flask with Jinja templates and **no JavaScript build chain at
all** — no npm, no bundler, no node in CI or in the Dockerfile.

Adopting the brief's stack means adopting all of that: a second language, a
second dependency tree, a build step in the image, and a second thing to
patch when a CVE lands in it.

## Decision

**Server-rendered, for now.** Flask + Jinja, charts as inline SVG generated on
the server, and vanilla JavaScript only for progressive enhancement — the page
must work with it switched off.

"For now" is in the decision, not a hedge. This is a reversible choice about
the MVP, not a claim that a React frontend would be wrong at scale.

## Alternatives considered

- **React + TypeScript + Vega-Lite as the brief proposes.** Rejected for the
  MVP. It is a real commitment — build, CI, deploy and security patching all
  change — taken before anyone has used the tool and found out which screens
  matter.
- **Vega-Lite from a CDN without a build step.** Rejected: it trades the build
  chain for a runtime dependency on an external host, inside a tool that
  reads health data. The CSP argument alone settles it.

## Consequences

**Charts are inline SVG built on the server.** This turns out to suit the
brief rather than fight it:

- the brief bans raw scatter plots in favour of 2D binned heat maps, and
  binning happens on the server anyway
- suppressed cells must render as `<5`, which is easier when the renderer
  already knows what was suppressed
- WCAG 2.1 AA is simpler when the chart is markup with real text in it rather
  than a canvas
- nothing can leak client-side, because the client never receives the data —
  it receives the picture

**Accessibility is structural.** Every chart carries a `<title>`, a `<desc>`
and a table of the same figures, so a screen reader gets the numbers rather
than a shrug. That also covers the "how to read this" requirement for free.

**The Okabe-Ito palette is applied server-side**, so a subcohort keeps its
colour across every chart in a report without client state.

**What is given up.** No client-side interactivity beyond links and forms: no
hover tooltips, no zoom, no re-binning without a round trip. For a tool whose
target is "question to correct answer in under two minutes, without choosing
a statistical test", that is an acceptable trade — the interactions that
matter are choosing a cohort and a question, both of which are forms.

### What would change this decision

A screen that genuinely needs continuous interaction — a cohort builder where
the count must update per keystroke rather than per submit, or a chart people
need to explore rather than read. Revisit then, with the screen in hand rather
than in the abstract.
