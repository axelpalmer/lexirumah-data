#!/usr/bin/env python

"""Similarity code tentative cognates in a word list and align them"""

import sys
from pycldf.util import Path
import hashlib
import argparse

import lingpy
import lingpy.compare.partial

from pylexirumah import get_dataset
from segments import Tokenizer

tokenizer = Tokenizer()


def sha1(path):
    return hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:12]


def clean_segments(row):
    """Reduce the row's segments to not contain empty morphemes.

    This function removes all unknown sound segments (/0/) from the "Segments"
    list of the `row` dict it is passed, and removes empty morphemes by
    collapsing subsequent morpheme boundaries (_#◦+→←) into one. The `row` is
    modified in-place, the resulting cleaned segment list is returned.

    >>> row = {"tokens": list("+_ta+0+at")
    >>> clean_segments(row)
    ['t', 'a', '+', 'a', 't']
    >>> row
    {'tokens': ['t', 'a', '+', 'a', 't']}

    """
    try:
        segments = row["tokens"]
    except KeyError:
        segments = [str(bipa[x])
                    for part in row["ipa"].split(".")
                    for x in tokenizer(part, ipa=True).split()]
    segments.insert(0, "#")
    segments.append("#")
    for s in range(len(segments) - 1, 0, -1):
        if not segments[s - 1]:
            del segments[s - 1]
            continue
        if segments[s - 1] == "0":
            del segments[s - 1]
            continue
        if segments[s - 1] in "_#◦+→←" and segments[s] in "_#◦+→←":
            del segments[s - 1]
            continue
    row["tokens"] = segments[1:-1]
    return row["tokens"]


def clean_segments_and_rename(new_column_names):
    def filter(row):
        for old, new in new_column_names.items():
            row[new] = row.pop(old)
        result = clean_segments(row)
        return result
    return filter


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("input", default=Path("Wordlist-metadata.json"),
                        nargs="?", type=Path,
                        help="Input file containing the CLDF word list."
                        " (default: ./Wordlist-metadata.json")
    parser.add_argument("output", nargs="?",
                        # type=argparse.FileType('w'),
                        default="aligned",
                        help="Output file to write segmented data to,"
                        " without extension .tsv (automatically added)")
    parser.add_argument("--soundclass", default="sca",
                        choices=["sca", "dolgo", "asjp", "art"],
                        help="Sound class model to use. (default: sca)")
    parser.add_argument("--threshold", default=0.55,
                        type=float,
                        help="Cognate clustering threshold value. (default:"
                        " 0.55)")
    parser.add_argument("--cluster-method", default="infomap",
                        help="Cognate clustering method name. Valid options"
                        " are, dependent on your LingPy version, {'upgma',"
                        " 'single', 'complete', 'mcl', 'infomap'}."
                        " (default: infomap)")
    parser.add_argument("--gop", default=-2,
                        type=float,
                        help="Gap opening penalty for the clustering"
                        "procedure. (default: -2)")
    parser.add_argument("--mode", default="overlap",
                        choices=['global', 'local', 'overlap', 'dialign'],
                        help="Select the mode for the alignment analysis."
                        "(default: overlap)")
    parser.add_argument("--ratio", default=1.5,
                        type=float,
                        help="Ratio of language-pair specific vs. general"
                        " scores in the LexStat algorithm. (default: 1.5)")
    parser.add_argument("--initial-threshold", default=0.7,
                        type=float,
                        help="Threshold value for the initial pairs used to"
                        "bootstrap the calculation. (default: 0.7)")
    args = parser.parse_args()

    dataset = get_dataset(args.input)

    # Explicit is better than implicit, so from_cldf does not contain renaming
    # functionality, but it does contain a callback hook that can modify each
    # row an filter it.
    rename_columns = {
         dataset["FormTable", "parameterReference"].name: "concept",
         dataset["FormTable", "languageReference"].name: "doculect",
         dataset["FormTable", "segments"].name: "tokens",
         dataset["FormTable", "form"].name: "ipa",
         dataset["FormTable", "id"].name: "reference",
    }

    lex = lingpy.compare.partial.Partial.from_cldf(
        args.input, filter=clean_segments_and_rename(rename_columns),
        model=lingpy.data.model.Model(args.soundclass),
        check=True)

    if args.ratio != 1.5:
        if args.ratio == float("inf"):
            ratio_pair = (1, 0)
            ratio_str = "-inf"
        if args.ratio == int(args.ratio) >= 0:
            r = int(args.ratio)
            ratio_pair = (r, 1)
            ratio_str = "-{:d}".format(r)
        elif args.ratio > 0:
            ratio_pair = (args.ratio, 1)
            ratio_str = "-" + str(args.ratio)
        else:
            raise ValueError("LexStat ratio must be in [0, ∞]")
    else:
        ratio_pair = (3, 2)
        ratio_str = ""
    if args.initial_threshold != 0.7:
        ratio_str += "-t{:02d}".format(int(args.initial_threshold * 100))
    try:
        scorers_etc = lingpy.compare.lexstat.LexStat(
            filename='lexstats-{:}-{:s}{:s}.tsv'.format(
                sha1(args.input),
                args.soundclass, ratio_str))
        lex.scorer = scorers_etc.scorer
        lex.cscorer = scorers_etc.cscorer
        lex.bscorer = scorers_etc.bscorer
    except (OSError, ValueError):
        lex.get_scorer(runs=10000, ratio=ratio_pair, threshold=args.initial_threshold)
        lex.output(
            'tsv',
            filename='lexstats-{:}-{:s}{:s}'.format(
                sha1(args.input),
                args.soundclass, ratio_str),
            ignore=[])
    # For some purposes it is useful to have monolithic cognate classes.
    lex.cluster(method='lexstat', threshold=args.threshold, ref='cogid',
                cluster_method=args.cluster_method, verbose=True, override=True,
                gop=args.gop, mode=args.mode)
    # But actually, in most cases partial cognates are much more useful.
    lex.partial_cluster(method='lexstat', threshold=args.threshold,
                        cluster_method=args.cluster_method, ref='partialcognateids',
                        override=True, verbose=True, gop=args.gop,
                        mode=args.mode)
    lex.output("tsv", filename="auto-clusters")
    alm = lingpy.Alignments(lex, ref="partialcognateids", fuzzy=True)
    alm.align(method='progressive')
    alm.output('tsv', filename=args.output, ignore='all', prettify=False)
