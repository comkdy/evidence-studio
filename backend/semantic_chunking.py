"""Embedding-based topic boundaries; original text offsets remain authoritative."""
from __future__ import annotations

import math
import re

import pysbd

from .embedding_client import EmbeddingError, validate_vectors

VERSION = "sentence-window-cosine-v1"


def sentence_units(text, splitter):
    # pySBD supplies original character spans (no destructive text cleaning).
    spans = pysbd.Segmenter(language="en", clean=False, char_span=True).segment(text)
    starts = sorted({0, *(s.start for s in spans)})
    starts.append(len(text))
    units = []
    forced = 0
    for start, end in zip(starts, starts[1:]):
        pieces = splitter.chunk_indices(text[start:end])
        forced += max(0, len(pieces) - 1)
        offsets = [start + offset for offset, _ in pieces]
        if not offsets:
            continue
        offsets[0] = start
        for low, high in zip(offsets, offsets[1:] + [end]):
            if text[low:high].strip():
                units.append((low, high))
    return units, forced


def percentile(values, percent):
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def semantic_ranges(text, policy, embedder, heading=""):
    config = policy.embedding
    buffer = policy.semantic_buffer_sentences
    unit_budget = max(16, (config.max_input_tokens - 64) // (2 * buffer + 1))
    units, forced = sentence_units(text, policy.splitter(unit_budget))
    if heading and len(units) > 1 and text[units[0][0]:units[0][1]].strip() == heading:
        # Keep a section label with its first sentence instead of inventing a label-only topic.
        units = [(units[0][0], units[1][1]), *units[2:]]
    if len(units) < 2:
        return [(0, len(text))], {"units": len(units), "forced_unit_splits": forced, "candidates": [], "threshold": None}
    # In a two-sentence section, full neighboring windows would be identical.
    buffer = min(buffer, (len(units) - 2) // 2)
    inputs = []
    for index in range(len(units)):
        left, right = max(0, index - buffer), min(len(units) - 1, index + buffer)
        # Whitespace normalization affects only API input, never the stored source text.
        window = re.sub(r"\s+", " ", text[units[left][0]:units[right][1]]).strip()
        value = config.input_prefix + window
        if len(policy.splitter(config.max_input_tokens).chunks(value)) != 1:
            raise EmbeddingError("Sentence window exceeds EMBEDDING_MAX_INPUT_TOKENS; adjust the tokenizer, prefix or input budget")
        inputs.append(value)
    vectors = validate_vectors(embedder.embed(inputs), len(inputs))
    distances = [max(0.0, min(2.0, 1 - math.fsum(a * b for a, b in zip(left, right)))) for left, right in zip(vectors, vectors[1:])]
    threshold = max(policy.semantic_min_distance, percentile(distances, policy.semantic_percentile))
    candidates, cuts = [], [0]
    for index, distance in enumerate(distances):
        # A floor prevents a homogeneous section from being split merely by its percentile.
        selected = distance > policy.semantic_min_distance and distance >= threshold
        position = units[index + 1][0]
        candidates.append({"offset": position, "cosine_distance": distance, "selected": selected})
        if selected:
            cuts.append(position)
    cuts.append(len(text))
    return list(zip(cuts, cuts[1:])), {"units": len(units), "forced_unit_splits": forced,
                                      "threshold": threshold, "buffer_sentences": buffer, "candidates": candidates}
