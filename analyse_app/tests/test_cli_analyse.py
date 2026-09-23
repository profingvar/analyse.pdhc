"""#649 — the CLI. Thin by design: it produces specs and goes through the
coordinator, exactly as the UI will."""
from __future__ import annotations

import json

import pytest

from app.testing import synth
from tests.nodeserver import PROJECT, PROJECT_KEY, SECRET, Node

GOOD = """
spec_version: 1
title: Pain in the home phase
purpose: quality_registry
sources: [cdr1, cdr2]
cohort:
  include:
    - {observation: tbsa_percent, op: ">=", value: 5}
variables:
  - {name: pain, from: pain_nrs, agg: mean}
analyses:
  - {type: describe, vars: [pain]}
"""

BAD_PURPOSE = GOOD.replace("quality_registry", "quality_followup")
BAD_REF = GOOD.replace("vars: [pain]", "vars: [nope]")


@pytest.fixture
def spec_file(tmp_path):
    def _write(text, name="spec.yaml"):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return str(p)
    return _write


def _run(app, args):
    return app.test_cli_runner().invoke(args=args)


@pytest.fixture
def live_nodes(app, monkeypatch):
    """A coordinator app whose two sources are real nodes on real ports."""
    monkeypatch.setenv(f"ANALYSE_PROJECT_KEY_{PROJECT.upper()}", PROJECT_KEY)
    sources = synth.build(nodes=2, patients=200, seed=11)
    started = [Node(s) for s in sources]
    app.config["ANALYSE_NODES"] = {n.node_id: n.base_url for n in started}
    app.config["ANALYSE_TRANSPORT_SECRET"] = SECRET
    try:
        yield app
    finally:
        for n in started:
            n.close()


class TestValidate:

    def test_a_good_spec_validates_and_prints_its_hash(self, app, spec_file):
        r = _run(app, ["spec-validate", spec_file(GOOD)])
        assert r.exit_code == 0, r.output
        assert "spec_hash: sha256:" in r.output

    def test_the_briefs_purpose_value_is_rejected_with_a_readable_message(
            self, app, spec_file):
        r = _run(app, ["spec-validate", spec_file(BAD_PURPOSE)])
        assert r.exit_code == 2
        assert "purpose" in r.output.lower()

    def test_an_unknown_variable_reference_names_the_field(self, app, spec_file):
        r = _run(app, ["spec-validate", spec_file(BAD_REF)])
        assert r.exit_code == 2
        assert "unknown variable" in r.output

    def test_the_hash_is_stable_between_invocations(self, app, spec_file):
        p = spec_file(GOOD)
        a = _run(app, ["spec-validate", p]).output
        b = _run(app, ["spec-validate", p]).output
        assert a == b


class TestDryRun:

    def test_it_reads_nothing_and_says_so(self, app, spec_file):
        """The point of --dry-run: size a cohort without running an analysis
        over it."""
        r = _run(app, ["spec-run", spec_file(GOOD), "--dry-run"])
        assert r.exit_code == 0, r.output
        assert "no data was read" in r.output.lower()

    def test_counts_are_suppressed(self, app, spec_file):
        r = _run(app, ["spec-run", spec_file(GOOD), "--dry-run"])
        assert "<k" in r.output

    def test_it_carries_the_spec_hash(self, app, spec_file):
        r = _run(app, ["spec-run", spec_file(GOOD), "--dry-run"])
        assert "spec_hash: sha256:" in r.output


class TestRun:

    def test_an_unknown_source_is_refused(self, app, spec_file):
        r = _run(app, ["spec-run", spec_file(GOOD), "--sources", "cdr9"])
        assert r.exit_code == 2
        assert "cdr9" in r.output

    def test_a_project_is_required(self, app, spec_file):
        """The pseudonym key is per project and has no safe default: a run
        that silently picked one could be joined to another project's."""
        r = _run(app, ["spec-run", spec_file(GOOD)])
        assert r.exit_code == 2
        assert "--project is required" in r.output

    def test_a_result_is_written_when_asked(self, live_nodes, spec_file,
                                            tmp_path):
        out = tmp_path / "results"
        r = _run(live_nodes, ["spec-run", spec_file(GOOD), "--out", str(out),
                              "--project", PROJECT])
        assert r.exit_code == 0, r.output
        blob = json.loads((out / "result.json").read_text())
        assert blob["provenance"]["spec_hash"].startswith("sha256:")
        assert [s["source"] for s in blob["sources"] if s["ok"]] == ["cdr1",
                                                                    "cdr2"]

    def test_unreachable_sources_are_reported_not_hidden(self, app, spec_file):
        """Every source in the spec must appear in the output, with a reason.
        A source that simply vanished would make the run look like an answer
        about a population it never covered."""
        app.config["ANALYSE_NODES"] = {"cdr1": "http://127.0.0.1:1",
                                       "cdr2": "http://127.0.0.1:1"}
        app.config["ANALYSE_TRANSPORT_SECRET"] = SECRET
        r = _run(app, ["spec-run", spec_file(GOOD), "--project", PROJECT])
        # Nothing answered, so there is no result to print — and that is an
        # error, not an empty result that reads as a tiny cohort.
        assert r.exit_code == 4
        assert "cdr1" in r.output and "cdr2" in r.output

    def test_a_source_with_no_endpoint_is_named(self, live_nodes, spec_file):
        """Configured for cdr1 only: cdr2 must be reported as unconfigured,
        not dropped from the spec."""
        live_nodes.config["ANALYSE_NODES"] = {
            k: v for k, v in live_nodes.config["ANALYSE_NODES"].items()
            if k == "cdr1"}
        r = _run(live_nodes, ["spec-run", spec_file(GOOD), "--project",
                              PROJECT])
        assert r.exit_code == 0, r.output
        blob = json.loads(r.output)
        by_src = {s["source"]: s for s in blob["sources"]}
        assert by_src["cdr2"]["ok"] is False
        assert "no endpoint configured" in by_src["cdr2"]["reason"]

    def test_the_cli_never_prints_an_identifier(self, live_nodes, spec_file):
        from app.testing import scan_text
        r = _run(live_nodes, ["spec-run", spec_file(GOOD), "--project",
                              PROJECT])
        assert scan_text(r.output) == []


class TestCanonicalForm:

    def test_it_prints_what_the_hash_is_taken_over(self, app, spec_file):
        r = _run(app, ["spec-canonical", spec_file(GOOD)])
        assert r.exit_code == 0
        blob = json.loads(r.output)
        assert blob["purpose"] == "quality_registry"
