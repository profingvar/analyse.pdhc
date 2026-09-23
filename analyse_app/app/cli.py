"""Flask CLI commands for analyse.pdhc."""


def register_spec_cli(app):
    """`flask spec-schema` — regenerate the JSON Schema artefact (#644).

    The schema under docs/analyse/ is GENERATED from the Pydantic models. A
    hand-edited copy drifts from the models it claims to describe, and the
    drift is invisible until a spec validates here and fails at the node.
    """
    import click

    @app.cli.command("spec-schema")
    @click.option("--out", default="../docs/analyse/analysis-spec-v1.schema.json",
                  show_default=True)
    def spec_schema(out):
        from pathlib import Path

        from app.spec import json_schema_text

        Path(out).write_text(json_schema_text(), encoding="utf-8")
        click.echo(f"wrote {out}")
