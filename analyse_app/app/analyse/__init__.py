"""Analyse layer for analyse.pdhc.

Federation and aggregation over CDR2–6 (the analyse read-identity;
CDR1 is the care-delivery path owned by cd-assist). The researcher /
cohort engine and the gateway/monitor-facing federated endpoints
(observations/stats/canonical/openehr) consume this package.

3-way-drift note (decision D5): ``federation.py`` + ``aggregations.py`` are a
copy of the same code in dashboard.pdhc/cd-assist.pdhc — a bug fixed in one
must be mirrored to all. See docs/470_scoping.md §3.
"""
