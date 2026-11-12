#!/usr/bin/env python3
"""
Find the fixed XOR key of a slowly-changing byte.

Feed it one decoded byte (e.g. dec[14] of every CE frame, in time order) while the
underlying quantity changes slowly. For every candidate key K it counts how many
value changes are NOT a single +-1 step after XOR-ing with K. The right key turns
the byte back into a plain counter, so it scores (close to) zero.

Low bits are what a short capture pins down: with a small change you'll see a tie
between keys that differ only in their top bits. A wrap of the low byte (watch the
companion high byte change) or a known clear-text copy (CF frames) breaks the tie.

    usage:  python3 keyscan.py < values.txt        # one integer per line
"""
import collections
import sys


def scan(values):
    runs = [values[0]]
    for v in values[1:]:
        if v != runs[-1]:
            runs.append(v)
    steps = collections.Counter(zip(runs, runs[1:]))
    scores = []
    for k in range(256):
        bad = sum(n for (a, b), n in steps.items() if abs((a ^ k) - (b ^ k)) > 1)
        scores.append((bad, k))
    scores.sort()
    return runs, sum(steps.values()), scores


if __name__ == "__main__":
    vals = [int(x) for x in sys.stdin.read().split()]
    runs, n, scores = scan(vals)
    print(f"{len(vals)} samples, {n} value changes")
    print("first runs:", runs[:20])
    for bad, k in scores[:8]:
        print(f"K=0x{k:02X}  non-unit steps: {bad}")
