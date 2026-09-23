"""Okabe-Ito, applied server-side (#655).

Colour-blind safe, and the same subcohort keeps the same colour in every
chart — which is only possible because the server draws them all and can
assign colour by group index rather than by whatever order a client happened
to render in.

Colour NEVER carries meaning alone. Every series also has a label and a
distinct dash pattern, because roughly one man in twelve cannot separate the
first two hues reliably and print is often greyscale.
"""
from __future__ import annotations

#: Okabe & Ito's eight-colour qualitative palette.
OKABE_ITO = [
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black
]

#: Paired with the colours, so a series is distinguishable without them.
DASHES = ["", "6 3", "2 3", "8 3 2 3", "1 4", "10 4", "4 2 1 2", "3 3"]

GRID = "#D9D9D9"
AXIS = "#444444"
TEXT = "#1A1A1A"
SUPPRESSED_FILL = "#EFEFEF"


def series_style(index: int) -> dict[str, str]:
    return {"colour": OKABE_ITO[index % len(OKABE_ITO)],
            "dash": DASHES[index % len(DASHES)]}
