"""`analyse-pdhc` command line (#649).

The CLI and the UI both produce SPECS and both go through the coordinator;
neither sends a free-form query to a node. So this is a thin thing on purpose
— everything it does, the UI will do the same way.

It never prints identifiers. `--dry-run` exists precisely so an analyst can
size a cohort without running an analysis over it, and it returns suppressed
counts per source and nothing else.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
import yaml

from app.privacy.disclosure import SUPPRESSED, DisclosurePolicy
from app.spec import AnalysisSpec, canonical_json, spec_hash
from app.testing import scan_object


def load_spec(path: str) -> AnalysisSpec:
    text = Path(path).read_text(encoding="utf-8")
    blob = yaml.safe_load(text) if path.endswith((".yaml", ".yml")) \
        else json.loads(text)
    return AnalysisSpec.model_validate(blob)


def _echo_validation_error(e: Exception) -> None:
    """Field-level, in the order a reader would fix them."""
    click.echo("This spec cannot run:", err=True)
    errors = getattr(e, "errors", None)
    if callable(errors):
        for err in e.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "(spec)"
            click.echo(f"  {loc}: {err.get('msg')}", err=True)
    else:
        click.echo(f"  {e}", err=True)


def register(app):
    @app.cli.command("spec-validate")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    def spec_validate(path):
        """Check a spec and print its hash. Runs nothing."""
        try:
            spec = load_spec(path)
        except Exception as e:
            _echo_validation_error(e)
            raise SystemExit(2)
        click.echo(f"valid: {spec.title}")
        click.echo(f"purpose: {spec.purpose.value}")
        click.echo(f"sources: {', '.join(spec.sources)}")
        click.echo(f"analyses: {', '.join(a.type for a in spec.analyses)}")
        click.echo(f"spec_hash: {spec_hash(spec)}")

    @app.cli.command("spec-canonical")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    def spec_canonical(path):
        """Print the canonical form the hash and signature are taken over."""
        click.echo(canonical_json(load_spec(path)))

    @app.cli.command("spec-run")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--sources", default=None,
                  help="Comma-separated subset of the spec's sources.")
    @click.option("--out", type=click.Path(file_okay=False), default=None)
    @click.option("--dry-run", is_flag=True,
                  help="Suppressed counts per source only. Runs no analysis.")
    @click.option("--project", default=None,
                  help="Analysis project id. Selects the per-project "
                       "pseudonym key each node loads from its own secret "
                       "store; runs in different projects cannot be joined.")
    def spec_run(path, sources, out, dry_run, project):
        """Run a spec through the coordinator, across the configured nodes."""
        from app.coordinator import NoSourcesAnswered, SourceStatus, run_distributed
        from app.transport.client import endpoints_from_config
        from app.transport.envelope import secret_from_config
        from app.version import VERSION

        try:
            spec = load_spec(path)
        except Exception as e:
            _echo_validation_error(e)
            raise SystemExit(2)

        wanted = [s.strip() for s in sources.split(",")] if sources else list(spec.sources)
        unknown = set(wanted) - set(spec.sources)
        if unknown:
            click.echo(f"not in this spec's sources: {', '.join(sorted(unknown))}",
                       err=True)
            raise SystemExit(2)

        if not dry_run and not project:
            click.echo(
                "--project is required: the pseudonym key is per project and "
                "has no safe default. Two runs under different projects "
                "deliberately cannot be joined.", err=True)
            raise SystemExit(2)

        if dry_run:
            click.echo(f"dry run — {spec.title}")
            click.echo(f"spec_hash: {spec_hash(spec)}")
            for s in wanted:
                # Nothing is read in a dry run; the count is what the
                # coordinator would ask for, shown suppressed by default.
                click.echo(f"  {s}: eligible patients {SUPPRESSED} "
                           f"(not read — dry run)")
            click.echo("No analysis was run and no data was read.")
            return

        # #684: the fan-out is real now. Sources named in the spec but not
        # configured as endpoints are reported as unconfigured rather than
        # silently dropped — a spec that asks about five regions and runs
        # against three must not look like an answer about five.
        endpoints = [e for e in endpoints_from_config(app.config)
                     if e.node_id in wanted]
        missing = sorted(set(wanted) - {e.node_id for e in endpoints})

        try:
            secret = secret_from_config(app.config)
        except Exception as e:
            click.echo(str(e), err=True)
            raise SystemExit(2)

        if not endpoints:
            click.echo("no configured node for: " + ", ".join(missing),
                       err=True)
            raise SystemExit(2)

        try:
            result = run_distributed(
                spec, endpoints, secret, project_id=project,
                coordinator_version=VERSION,
                timeout=float(app.config.get("ANALYSE_NODE_TIMEOUT", 60.0)))
        except NoSourcesAnswered as e:
            click.echo(str(e), err=True)
            raise SystemExit(4)

        for node_id in missing:
            result.sources.append(
                SourceStatus(source=node_id, ok=False,
                             reason="no endpoint configured for this source"))
        if missing:
            result.notes.append(
                f"{len(missing)} source(s) in this spec have no configured "
                f"endpoint and contributed nothing: {', '.join(missing)}.")

        payload = result.to_json()

        leaks = scan_object(payload, where="cli output")
        if leaks:
            # Refuse to print rather than emit something the gate would have
            # caught later, in a file somebody had already sent on.
            click.echo(f"refusing to print: {len(leaks)} identifier-like "
                       f"values in the result", err=True)
            raise SystemExit(3)

        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            dest = Path(out) / "result.json"
            dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                            encoding="utf-8")
            click.echo(f"wrote {dest}")
        else:
            click.echo(json.dumps(payload, indent=2, ensure_ascii=False))

    @app.cli.command("synth")
    @click.option("--nodes", default=3, show_default=True)
    @click.option("--patients", default=2000, show_default=True)
    @click.option("--seed", default=0, show_default=True)
    @click.option("--spec", "spec_path", default=None,
                  type=click.Path(exists=True, dir_okay=False),
                  help="Run this spec against the synthetic sources.")
    def synth_cmd(nodes, patients, seed, spec_path):
        """Build a synthetic multi-source environment and optionally run a spec.

        This drives the REAL node and coordinator code with synthetic data.
        It does not stand up containerised CDRs — see app/testing/synth.py for
        what that additionally requires.
        """
        from app.testing import synth

        sources = synth.build(nodes=nodes, patients=patients, seed=seed)
        click.echo(f"{len(sources)} synthetic sources, seed {seed}:")
        for s in sources:
            click.echo(f"  {s.node_id}: {len(s.rows)} observations, "
                       f"{len(s.blocked)} patients blocked")
        if not spec_path:
            return
        result = synth.run(load_spec(spec_path), sources)
        for s in result.sources:
            click.echo(f"  {s.source}: {'ok' if s.ok else 'FAILED'} "
                       f"({s.n_patients} patients)")
        for r in result.results:
            click.echo(f"  {r['kind']}: {r['exactness']}")
        click.echo(f"spec_hash: {result.provenance['spec_hash']}")

    @app.cli.command("sources-list")
    def sources_list():
        """Configured nodes and their status."""
        from app.transport.client import endpoints_from_config
        endpoints = endpoints_from_config(app.config)
        if not endpoints:
            click.echo("no nodes configured (ANALYSE_NODES is empty)")
            return
        role = app.config.get("ANALYSE_ROLE", "both")
        click.echo(f"role: {role}")
        for ep in sorted(endpoints, key=lambda e: e.node_id):
            click.echo(f"{ep.node_id}\t{ep.run_url}")
