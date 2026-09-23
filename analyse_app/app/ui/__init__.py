"""Server-rendered UI: charts, question cards, sentences, recipes, i18n.

See ADR-0009 for why this is server-rendered and has no JavaScript build
chain.
"""
from . import charts, i18n, palette, questions, recipes, sentences

__all__ = ["charts", "i18n", "palette", "questions", "recipes", "sentences"]
