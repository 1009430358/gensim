#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2013 Radim Rehurek <radimrehurek@seznam.cz>
# Licensed under the GNU LGPL v2.1 - http://www.gnu.org/licenses/lgpl.html

"""
This module contains functions to perform segmentation on a list of topics.
"""

import logging
import itertools

import numpy as np

from gensim.topic_coherence.text_analysis import InvertedIndexAccumulator

logger = logging.getLogger(__name__)


def _ret_top_ids(segmented_topics):
    """
    Helper function to return a set of all the unique topic ids in segmented topics.
    """
    top_ids = set()  # is a set of all the unique ids contained in topics.
    for s_i in segmented_topics:
        for word_id in itertools.chain.from_iterable(s_i):
            if isinstance(word_id, np.ndarray):
                for i in word_id:
                    top_ids.add(i)
            else:
                top_ids.add(word_id)

    return top_ids


def _ids_to_words(ids, dictionary):
    """Convert an iterable of ids to their corresponding words using a dictionary.
    This function abstracts away the differences between the HashDictionary and the standard one.
    """
    top_words = set()
    for word_id in ids:
        word = dictionary[word_id]
        if isinstance(word, set):
            top_words = top_words.union(word)
        else:
            top_words.add(word)

    return top_words


def p_boolean_document(corpus, segmented_topics):
    """
    This function performs the boolean document probability estimation. Boolean document estimates the probability
    of a single word as the number of documents in which the word occurs divided by the total number of documents.

    Args:
    ----
    corpus : The corpus of documents.
    segmented_topics : Output from the segmentation of topics. Could be simply topics too.

    Returns:
    -------
    per_topic_postings : Boolean document posting list for each unique topic id.
    num_docs : Total number of documents in corpus.
    """
    top_ids = _ret_top_ids(segmented_topics)
    # Instantiate the dictionary with empty sets for each top_id
    per_topic_postings = {word_id: set() for word_id in top_ids}

    # Iterate through the documents, appending the document number to the set for each top_id it contains
    for n, document in enumerate(corpus):
        doc_words = frozenset(x[0] for x in document)
        top_ids_in_doc = top_ids.intersection(doc_words)
        if len(top_ids_in_doc) > 0:
            for word_id in top_ids_in_doc:
                per_topic_postings[word_id].add(n)

    return per_topic_postings, len(corpus)


def p_boolean_sliding_window(texts, segmented_topics, dictionary, window_size):
    """
    This function performs the boolean sliding window probability estimation. Boolean sliding window
    determines word counts using a sliding window. The window moves over the documents one word token per step.
    Each step defines a new virtual document by copying the window content. Boolean document is applied to
    these virtual documents to compute word probabilities.

    Args:
    ----
    texts : List of string sentences.
    segmented_topics : Output from the segmentation of topics. Could be simply topics too.
    dictionary : Gensim dictionary mapping of the tokens and ids.
    window_size : Size of the sliding window. 110 found out to be the ideal size for large corpora.

    Returns:
    -------
    per_topic_postings : Boolean sliding window postings list of all the unique topic ids.
    window_id[0] : Total no of windows
    """
    top_ids = _ret_top_ids(segmented_topics)
    top_words = _ids_to_words(top_ids, dictionary)
    occurrence_accumulator = InvertedIndexAccumulator(top_words, dictionary.token2id)\
        .accumulate(texts, window_size)

    per_topic_postings = occurrence_accumulator.index_to_dict()
    return per_topic_postings, occurrence_accumulator.window_id
