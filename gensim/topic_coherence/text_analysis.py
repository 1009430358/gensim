#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2013 Radim Rehurek <radimrehurek@seznam.cz>
# Licensed under the GNU LGPL v2.1 - http://www.gnu.org/licenses/lgpl.html

"""
This module contains classes for analyzing the texts of a corpus to accumulate
statistical information about word occurrences.
"""

import sys
import itertools
import logging
import multiprocessing as mp

import numpy as np
import scipy.sparse as sps

from gensim import utils

logger = logging.getLogger(__name__)


def _ids_to_words(ids, dictionary):
    """Convert an iterable of ids to their corresponding words using a dictionary.
    This function abstracts away the differences between the HashDictionary and the standard one.

    Args:
    ----
    ids: list of list of tuples, where each tuple contains (token_id, iterable of token_ids).
         This is the format returned by the topic_coherence.segmentation functions.
    """
    if not dictionary.id2token:  # may not be initialized in the standard gensim.corpora.Dictionary
        setattr(dictionary, 'id2token', {v: k for k, v in dictionary.token2id.items()})

    top_words = set()
    for word_id in ids:
        word = dictionary.id2token[word_id]
        if isinstance(word, set):
            top_words = top_words.union(word)
        else:
            top_words.add(word)

    return top_words


class BaseAnalyzer(object):
    """Base class for corpus and text analyzers."""

    def __init__(self, relevant_ids):
        self.relevant_ids = relevant_ids
        self.id2contiguous = {word_id: n for n, word_id in enumerate(self.relevant_ids)}
        self.log_every = 1000
        self._num_docs = 0

    @property
    def num_docs(self):
        return self._num_docs

    @num_docs.setter
    def num_docs(self, num):
        self._num_docs = num
        if self._num_docs % self.log_every == 0:
            logger.info("%s accumulated stats from %d documents" % (
                self.__class__.__name__, self._num_docs))

    def analyze_text(self, text):
        raise NotImplementedError("Base classes should implement analyze_text.")

    def __getitem__(self, word_or_words):
        if hasattr(word_or_words, '__iter__'):
            return self.get_co_occurrences(*word_or_words)
        else:
            return self.get_occurrences(word_or_words)

    def get_occurrences(self, word_id):
        """Return number of docs the word occurs in, once `accumulate` has been called."""
        return self._get_occurrences(self.id2contiguous[word_id])

    def _get_occurrences(self, word_id):
        raise NotImplementedError("Base classes should implement occurrences")

    def get_co_occurrences(self, word_id1, word_id2):
        """Return number of docs the words co-occur in, once `accumulate` has been called."""
        return self._get_co_occurrences(self.id2contiguous[word_id1], self.id2contiguous[word_id2])

    def _get_co_occurrences(self, word_id1, word_id2):
        raise NotImplementedError("Base classes should implement co_occurrences")


class UsesDictionary(BaseAnalyzer):
    """A BaseAnalyzer that uses a Dictionary, hence can translate tokens to counts.
    The standard BaseAnalyzer can only deal with token ids since it does not have access to the token2id mapping.
    """

    def __init__(self, relevant_ids, dictionary):
        super(UsesDictionary, self).__init__(relevant_ids)
        self.relevant_words = _ids_to_words(self.relevant_ids, dictionary)
        self.dictionary = dictionary
        self.token2id = dictionary.token2id

    def analyze_text(self, text):
        raise NotImplementedError("Base classes should implement analyze_text.")

    def get_occurrences(self, word):
        """Return number of docs the word occurs in, once `accumulate` has been called."""
        try:
            word_id = self.token2id[word]
        except KeyError:
            word_id = word
        return self._get_occurrences(self.id2contiguous[word_id])

    def _word2_contiguous_id(self, word):
        try:
            word_id = self.token2id[word]
        except KeyError:
            word_id = word
        return self.id2contiguous[word_id]

    def get_co_occurrences(self, word1, word2):
        """Return number of docs the words co-occur in, once `accumulate` has been called."""
        word_id1 = self._word2_contiguous_id(word1)
        word_id2 = self._word2_contiguous_id(word2)
        return self._get_co_occurrences(word_id1, word_id2)


class InvertedIndexBased(BaseAnalyzer):
    """Analyzer that builds up an inverted index to accumulate stats."""

    def __init__(self, *args):
        super(InvertedIndexBased, self).__init__(*args)
        vocab_size = len(self.relevant_ids)
        self._inverted_index = np.array([set() for _ in range(vocab_size)])

    def _get_occurrences(self, word_id):
        return len(self._inverted_index[word_id])

    def _get_co_occurrences(self, word_id1, word_id2):
        s1 = self._inverted_index[word_id1]
        s2 = self._inverted_index[word_id2]
        return len(s1.intersection(s2))

    def index_to_dict(self):
        contiguous2id = {n: word_id for word_id, n in self.id2contiguous.iteritems()}
        return {contiguous2id[n]: doc_id_list for n, doc_id_list in enumerate(self._inverted_index)}


class CorpusAccumulator(InvertedIndexBased):
    """Gather word occurrence stats from a corpus by iterating over its BoW representation."""

    def analyze_text(self, text):
        doc_words = frozenset(x[0] for x in text)
        top_ids_in_doc = self.relevant_ids.intersection(doc_words)
        if len(top_ids_in_doc) > 0:
            for word_id in top_ids_in_doc:
                self._inverted_index[self.id2contiguous[word_id]].add(self._num_docs)

    def accumulate(self, corpus):
        for document in corpus:
            self.analyze_text(document)
            self.num_docs += 1
        return self


class WindowedTextsAnalyzer(UsesDictionary):
    """Gather some statistics about relevant terms of a corpus by iterating over windows of texts."""

    def __init__(self, relevant_ids, dictionary):
        """
        Args:
        ----
        relevant_ids: the set of words that occurrences should be accumulated for.
        dictionary: gensim.corpora.dictionary.Dictionary instance with mappings for the relevant_ids.
        """
        super(WindowedTextsAnalyzer, self).__init__(relevant_ids, dictionary)

    def filter_to_relevant_words(self, text):
        """Lazily filter the text to only those words which are relevant."""
        relevant_words = (word for word in text if word in self.relevant_words)
        relevant_ids = (self.token2id[word] for word in relevant_words)
        return (self.id2contiguous[word_id] for word_id in relevant_ids)

    def text_is_relevant(self, text):
        """Return True if the text has any relevant words, else False."""
        for word in text:
            if word in self.relevant_words:
                return True
        return False

    def accumulate(self, texts, window_size):
        relevant_texts = (text for text in texts if self.text_is_relevant(text))
        for virtual_document in utils.iter_windows(relevant_texts, window_size, ignore_below_size=False):
            self.analyze_text(virtual_document)
            self.num_docs += 1
        return self


class InvertedIndexAccumulator(WindowedTextsAnalyzer, InvertedIndexBased):
    """Build an inverted index from a sequence of corpus texts."""

    def analyze_text(self, window):
        for word_id in self.filter_to_relevant_words(window):
            self._inverted_index[word_id].add(self._num_docs)


class WordOccurrenceAccumulator(WindowedTextsAnalyzer):
    """Accumulate word occurrences and co-occurrences from a sequence of corpus texts."""

    def __init__(self, *args):
        super(WordOccurrenceAccumulator, self).__init__(*args)
        vocab_size = len(self.relevant_words)
        self._occurrences = np.zeros(vocab_size, dtype='uint32')
        self._co_occurrences = sps.lil_matrix((vocab_size, vocab_size), dtype='uint32')

    def __str__(self):
        return self.__class__.__name__

    def analyze_text(self, window):
        relevant_words = list(self.filter_to_relevant_words(window))
        if relevant_words:
            uniq_words = np.array(relevant_words)
            self._occurrences[uniq_words] += 1

            for combo in itertools.combinations(relevant_words, 2):
                self._co_occurrences[combo] += 1

    def accumulate(self, texts, window_size):
        self._co_occurrences = self._co_occurrences.tolil()
        self.partial_accumulate(texts, window_size)
        self._symmetrize()
        return self

    def partial_accumulate(self, texts, window_size):
        """Meant to be called several times to accumulate partial results. The final
        accumulation should be performed with the `accumulate` method as opposed to this one.
        This method does not ensure the co-occurrence matrix is in lil format and does not
        symmetrize it after accumulation.
        """
        super(WordOccurrenceAccumulator, self).accumulate(texts, window_size)

    def _symmetrize(self):
        """Word pairs may have been encountered in (i, j) and (j, i) order.
        Rather than enforcing a particular ordering during the update process,
        we choose to symmetrize the co-occurrence matrix after accumulation has completed.
        """
        co_occ = self._co_occurrences
        co_occ.setdiag(self._occurrences)  # diagonal should be equal to occurrence counts
        self._co_occurrences = co_occ + co_occ.T - sps.diags(co_occ.diagonal(), dtype='uint32')

    def _get_occurrences(self, word_id):
        return self._occurrences[word_id]

    def _get_co_occurrences(self, word_id1, word_id2):
        return self._co_occurrences[word_id1, word_id2]

    def merge(self, other):
        self._occurrences += other._occurrences
        self._co_occurrences += other._co_occurrences
        self._num_docs += other._num_docs


class ParallelWordOccurrenceAccumulator(WindowedTextsAnalyzer):
    """Accumulate word occurrences in parallel."""

    def __init__(self, processes, *args, **kwargs):
        super(ParallelWordOccurrenceAccumulator, self).__init__(*args)
        if processes < 2:
            raise ValueError("Must have at least 2 processes to run in parallel; got %d" % processes)
        self.processes = processes
        self.batch_size = kwargs.get('batch_size', 16)

    def __str__(self):
        return "%s(processes=%s, batch_size=%s)" % (
            self.__class__.__name__, self.processes, self.batch_size)

    def accumulate(self, texts, window_size):
        workers, input_q, output_q = self.start_workers(window_size)
        try:
            self.queue_all_texts(input_q, texts, window_size)
            interrupted = False
        except KeyboardInterrupt:
            logger.warn("stats accumulation interrupted; <= %d documents processed" % self._num_docs)
            interrupted = True

        accumulators = self.terminate_workers(input_q, output_q, workers, interrupted)
        return self.merge_accumulators(accumulators)

    def start_workers(self, window_size):
        """Set up an input and output queue and start processes for each worker.
        
        The input queue is used to transmit batches of documents to the workers.
        The output queue is used by workers to transmit the WordOccurrenceAccumulator instances.
        Returns: tuple of (list of workers, input queue, output queue).
        """
        input_q = mp.Queue(maxsize=self.processes)
        output_q = mp.Queue()
        workers = []
        for _ in range(self.processes):
            accumulator = WordOccurrenceAccumulator(self.relevant_ids, self.dictionary)
            worker = AccumulatingWorker(input_q, output_q, accumulator, window_size)
            worker.start()
            workers.append(worker)

        return workers, input_q, output_q

    def yield_batches(self, texts):
        """Return a generator over the given texts that yields batches of
        `batch_size` texts at a time.
        """
        batch = []
        for text in texts:
            batch.append(text)
            if len(batch) == self.batch_size:
                yield batch
                batch = []

        if batch:
            yield batch

    def queue_all_texts(self, q, texts, window_size):
        """Sequentially place batches of texts on the given queue until `texts` is consumed.
        The texts are filtered so that only those with at least one relevant token are queued.
        """
        relevant_texts = (text for text in texts if self.text_is_relevant(text))
        for batch_num, batch in enumerate(self.yield_batches(relevant_texts)):
            q.put(batch, block=True)
            before = self._num_docs / self.log_every
            self._num_docs += sum(len(doc) - window_size + 1 for doc in batch)
            if before < (self._num_docs / self.log_every):
                logger.info("submitted %d batches to accumulate stats from %d documents (%d virtual)" % (
                    batch_num, batch_num * self.batch_size, self._num_docs))

    def terminate_workers(self, input_q, output_q, workers, interrupted=False):
        """Wait until all workers have transmitted their WordOccurrenceAccumulator instances,
        then terminate each. We do not use join here because it has been shown to have some issues
        in Python 2.7 (and even in later versions). This method also closes both the input and output
        queue.
        
        If `interrupted` is False (normal execution), a None value is placed on the input queue for
        each worker. The workers are looking for this sentinel value and interpret it as a signal to
        terminate themselves. If `interrupted` is True, a KeyboardInterrupt occurred. The workers are
        programmed to recover from this and continue on to transmit their results before terminating.
        So in this instance, the sentinel values are not queued, but the rest of the execution
        continues as usual.
        """
        if not interrupted:
            for _ in workers:
                input_q.put(None, block=True)

        accumulators = []
        while len(accumulators) != len(workers):
            accumulators.append(output_q.get())
        logger.info("%d accumulators retrieved from output queue" % len(accumulators))

        for worker in workers:
            if worker.is_alive():
                worker.terminate()

        input_q.close()
        output_q.close()
        return accumulators

    def merge_accumulators(self, accumulators):
        """Merge the list of accumulators into a single `WordOccurrenceAccumulator` with all
        occurrence and co-occurrence counts, and a `num_docs` that reflects the total observed
        by all the individual accumulators.
        """
        accumulator = accumulators[0]
        for other_accumulator in accumulators[1:]:
            accumulator.merge(other_accumulator)
        # Workers perform partial accumulation, so none of the co-occurrence matrices are symmetrized.
        # This is by design, to avoid unnecessary matrix additions during accumulation.
        accumulator._symmetrize()
        return accumulator


class AccumulatingWorker(mp.Process):
    """Accumulate stats from texts fed in from queue."""

    def __init__(self, input_q, output_q, accumulator, window_size):
        super(AccumulatingWorker, self).__init__()
        self.input_q = input_q
        self.output_q = output_q
        self.accumulator = accumulator
        self.accumulator.log_every = sys.maxint  # avoid logging in workers
        self.window_size = window_size

    def run(self):
        try:
            self._run()
        except KeyboardInterrupt:
            logger.info("%s interrupted after processing %d documents" % (
                self.__class__.__name__, self.accumulator.num_docs))
        finally:
            self.reply_to_master()

    def _run(self):
        batch_num = 0
        n_docs = 0
        while True:
            docs = self.input_q.get(block=True)
            if docs is None:  # sentinel value
                break

            self.accumulator.partial_accumulate(docs, self.window_size)
            n_docs += len(docs)
            logger.debug("completed batch %d; %d documents processed (%d virtual)" % (
                batch_num, n_docs, self.accumulator.num_docs))
            batch_num += 1

    def reply_to_master(self):
        logger.info("serializing accumulator to return to master...")
        self.output_q.put(self.accumulator, block=False)
        logger.info("accumulator serialized")
