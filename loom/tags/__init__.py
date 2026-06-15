"""Danbooru tag vocabulary: validate / autocomplete / snap free text to real tags."""
from .booru import TagIndex, get_index
from .cooccur import CooccurIndex, get_cooccur

__all__ = ["TagIndex", "get_index", "CooccurIndex", "get_cooccur"]
