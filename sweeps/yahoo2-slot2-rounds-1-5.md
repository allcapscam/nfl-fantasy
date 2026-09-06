# Chunk 1 — Yahoo superflex, slot 2, rounds 1–5

Run 2026-09-06. 992 opening sequences × 120 runs = **119,040 simulated drafts**.

```
uv run python scripts/pull_sleeper.py --league yahoo2 --scoring half_ppr \
    --adp 2qb --score-with yahoo2
uv run python scripts/simulate.py --league yahoo2 --slot 2 --openings 5 \
    --runs 120 --jobs 4 --dst-round 10 --k-round 14
```

The number every sequence is judged on is the projected points of the best legal
starting lineup at the end of a full sixteen-round draft — not the value of the
five picks themselves. A pick that looks strong in isolation and then sits on the
bench scores nothing here.

## The answer

**Open RB. Take exactly one tight end. One or two quarterbacks. At most one
receiver.**

All 25 of the top 25 sequences open RB. All 25 contain exactly one TE — not
none, not two. All 25 contain two or three RB and one or two QB. The five worst
sequences all open TE.

The best sequences are the four arrangements of **2 RB / 1 WR / 1 TE / 1 QB**:

| opening | mean | +/- |
| --- | ---: | ---: |
| RB RB WR TE QB | 2209 | 3 |
| RB RB WR QB TE | 2208 | 3 |
| RB WR RB TE QB | 2207 | 3 |
| RB WR RB QB TE | 2207 | 3 |
| … | | |
| TE TE TE WR WR | 1930 | 4 |

Only one other sequence sits within one paired standard error of the best
(`RB RB WR QB TE`), so the ordering below the top pair is real signal, not
noise — unlike the round 7–11 sweep in HANDOFF, where everything tied. The
spread from best to worst is **279 points**, which is roughly a starter.

## Where the value actually sits

Value of each position by round, averaged over every opening that puts it there.
This washes out the other four picks, so it is far more stable than any single
sequence.

| round | QB | RB | TE | WR |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2088 | **2138** | 2058 | 2085 |
| 2 | 2085 | **2116** | 2082 | 2086 |
| 3 | 2086 | **2117** | 2079 | 2087 |
| 4 | **2103** | 2097 | 2089 | 2082 |
| 5 | **2103** | 2096 | 2090 | 2082 |

Two things fall out.

**Round 1 is not a close call.** RB is 50 points clear of the next position,
the largest gap anywhere in the sweep. From pick 2 the elite backs are on the
board — Gibbs at 1.4 ADP, Robinson at 2.6 — and taking one is worth more than
the QB1 who goes at 3.7 on this board.

**Rounds 4 and 5 are nearly flat.** A 21-point range across four positions, with
QB nominally ahead. By your second turn the decision has mostly been made by
rounds 1–3; do not read the QB edge there as an instruction.

How many of each position belong in the first five picks:

| count | QB | RB | TE | WR |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 2071 | 2047 | 2086 | 2097 |
| 1 | **2108** | 2091 | **2123** | **2102** |
| 2 | 2100 | 2118 | 2079 | 2087 |
| 3 | 2056 | **2133** | 2016 | 2064 |
| 4 | — | 2125 | — | 2039 |
| 5 | — | 2101 | — | 2014 |

TE has the sharpest peak on the board: one is worth 37 points over none and 44
over two. One tight end starts, and the second one never enters the lineup.

## The receiver result, which is worth arguing with

This league starts **three** receivers, and the sweep still makes WR the weakest
of the four positions in the first five picks — 1 is barely better than 0, and it
declines monotonically from there.

The mechanism is replacement, not demand. Receiver is the deepest position on
the board, so the WR you take at pick 23 and the one you take at pick 71 are
close in projected points; back and quarterback are not. The `FLEX` and
`SUPER_FLEX` seats then absorb whatever is scarce. Spending an early pick on the
deepest position buys the smallest edge over what that seat would have held
anyway.

That is a real effect, but it is the finding here most sensitive to the
projections being right about receiver depth. Worth re-running against a fresh
pull before draft day.

## What this run assumed

- **Scoring** — the league's own `scoring_table`, applied to Sleeper's raw
  projected stat lines. QB, RB, WR, TE and K are scored under it; DST keeps
  Sleeper's precomputed half-PPR total because Sleeper's defensive projection
  carries no points-allowed bucket. The two tables agree to +0.0 at every
  position on this feed, so nothing here turns on the choice.
- **ADP** — the `adp_2qb` board, 436 players. The QB1 goes at 3.7 with five
  quarterbacks inside the top 24, which is a superflex room. The single-QB board
  would have had the QB1 in round three and every number downstream wrong.
- **Board** — 629 players, 565 with byes, pulled 2026-09-06.
- **The modelled room takes defences from round 10 and kickers from round 14**,
  per HANDOFF. These do not touch rounds 1–5 directly, but they shape the back
  half of every draft the lineup total is scored on. Count them on the live
  board before trusting this.
- **Slot 2 of 12**, picks 2, 23, 26, 47, 50. Rounds 2–3 and rounds 4–5 are
  near-back-to-back turns, so those pairs are chosen together far more than they
  look on paper.

## Raw output

```
yahoo2: slot 2 of 12, 16 rounds, picks [2, 23, 26, 47, 50, 71]...

  opening 5 picks, 120 runs each, 992 sequences, 119040 drafts on 4 core(s)

  top 25 openings

  opening                        mean    +/-
  RB RB WR TE QB                 2209      3
  RB RB WR QB TE                 2208      3
  RB WR RB TE QB                 2207      3
  RB WR RB QB TE                 2207      3
  RB TE RB QB WR                 2203      3
  RB TE RB WR QB                 2200      3
  RB RB TE QB WR                 2198      3
  RB RB TE WR QB                 2197      3
  RB TE RB QB QB                 2195      4
  RB RB RB TE QB                 2194      4
  RB RB RB QB TE                 2193      4
  RB TE RB QB RB                 2191      4
  RB RB TE QB QB                 2190      3
  RB QB RB QB TE                 2189      3
  RB TE RB RB QB                 2189      4
  RB RB QB QB TE                 2189      3
  RB RB TE QB RB                 2189      4
  RB QB RB TE QB                 2188      3
  RB RB QB TE QB                 2188      3
  RB RB TE RB QB                 2188      4
  RB RB QB TE WR                 2187      3
  RB QB RB TE WR                 2186      3
  RB QB RB WR TE                 2186      3
  RB RB QB WR TE                 2185      3
  RB TE WR QB RB                 2184      3

  worst 5
  TE TE TE RB RB                 1962      4
  TE TE WR WR TE                 1958      4
  TE WR TE TE WR                 1956      4
  TE WR TE WR TE                 1955      4
  TE TE TE WR WR                 1930      4

  best: RB RB WR TE QB -- 2 sequence(s) within one paired standard error, not separable at this sample.
  tied with it: RB RB WR QB TE

  value of each position by round, averaged over every opening

   round       QB       RB       TE       WR
       1     2088     2138     2058     2085
       2     2085     2116     2082     2086
       3     2086     2117     2079     2087
       4     2103     2097     2089     2082
       5     2103     2096     2090     2082

  how many of each position belong in the first 5 picks

   count       QB       RB       TE       WR
       0     2071     2047     2086     2097
       1     2108     2091     2123     2102
       2     2100     2118     2079     2087
       3     2056     2133     2016     2064
       4        -     2125        -     2039
       5        -     2101        -     2014
```
