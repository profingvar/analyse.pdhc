# Usability walkthrough — analyse.pdhc

Ticket #658 (AN-15). Phase 3's acceptance is not a code review: the brief sets
a usability bar, and this is where it is checked.

## The bar

> A clinician or project coordinator **without statistical training** gets from
> a question to a correct, readable answer in **under two minutes**, without
> choosing a statistical test.

## How to run it

Five tasks. Five participants who match the target — a clinician, a project
coordinator, a quality-registry coordinator. **Not** a statistician, and not
anyone who has seen the tool being built.

Rules for whoever facilitates:

- **Say nothing while they work.** The temptation to help is the thing that
  invalidates the test. If they are stuck, that is the finding.
- Ask them to think aloud. Record *where* they hesitate, not just whether they
  finish.
- Time each task from reading it to the participant saying they are done.
- A task where they produce a **wrong** answer confidently is a worse result
  than one they fail to finish. Note it as such.

## The five tasks

### 1. Describe a cohort (target: under 2 minutes)
> "Find out how many patients had a burn injury covering at least 5% of the
> body, and what their average age band is."

Checks: the cohort builder reads as a sentence; the live count is noticed; the
summary table is legible without explanation.

### 2. A distribution (target: under 2 minutes)
> "Show how pain scores in the first four weeks are spread out."

Checks: the right question card is found without reading all eight; the
histogram is understood; **the participant does not ask which test was used.**

### 3. Compare two groups (target: under 3 minutes)
> "Compare pain in patients with burns under 20% of the body against those
> with 20% or more."

Checks: groups are built with the same sentence builder as the cohort; the
Table 1 view is preferred to the p-value; the participant describes the
difference **without saying one thing caused the other**.

### 4. Read a suppressed result (target: under 1 minute)
> "Here is a result with some cells hidden. What does `<5` mean, and why are
> some cells hidden that look big enough?"

Checks — and this is the one most likely to fail: does the participant
understand **secondary suppression**? A cell hidden purely so its neighbour
cannot be worked out from the totals looks arbitrary unless the explanation
lands. If four of five participants cannot explain it, the wording is wrong,
not the participants.

### 5. Share and rerun (target: under 2 minutes)
> "Save this analysis so a colleague can run it next month on newer data, and
> tell them where to find it."

Checks: recipes are findable; the participant understands that the link
carries an **id and not the data**; they can say what the spec hash on the
report is for.

## What to record

For each task: completion (yes / no / wrong-but-confident), time, where they
hesitated, what they said aloud at the moment of hesitation.

Then, across all five participants:

- Any task failed by **three or more** participants is a Phase 3 defect, not a
  training issue.
- Any instance of a participant **stating a causal claim** from a comparison
  is a wording defect in the summary sentence templates, and a serious one.
- Any instance of a participant **choosing a statistical test** means a card
  is exposing a method where it should expose a question.

## Status

**Not yet run.** The flow, cards, charts, sentences, recipes and both
languages are built and unit-tested, but no participant has used them. Phase 3
is **not accepted** until this walkthrough has been run with five people and
the findings recorded below.

Unit tests can show that a sentence never contains the word "causes". They
cannot show that a reader does not infer causation anyway. That is what this
document is for.

### Findings

_(to be filled in when the walkthrough is run)_
