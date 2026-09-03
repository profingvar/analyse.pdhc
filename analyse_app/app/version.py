"""Single source of the service version string reported by /healthz.

Bump on release, or wire to a git sha at build time. Kept as a module
constant so the scaffold has a deterministic, image-stable value.
"""
VERSION = "0.2.0-reform"
