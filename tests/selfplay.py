# AI self-play checks (not a game dependency): python3 tests/selfplay.py
# Needs Python Playwright + Chromium. Runs headless; total runtime is kept to a few minutes.
#   Part 1: legality / forbidden-point / stall checks on renju games (asserted).
#   Part 2: strength matchups with alternated colors (reported only, never asserted).
from collections import Counter
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BUDGET = {'low': 80, 'mid': 500, 'high': 1500}  # nominal ms per move
CAPS = {13: 130, 15: 160, 19: 200}  # max plies per game (including opening stones)
STRENGTH_GAMES = 12  # per matchup, half with each color
SOFT_LIMIT_S = 170  # optional high-vs-mid games start only before this many seconds
HARD_LIMIT_S = 240  # no optional game starts after this

RUN_GAME = r"""async (o) => {
  let seed = o.seed >>> 0;
  const rnd = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
  const size = o.size, rule = o.rule, mid = (size - 1) / 2;
  let s = rules.newGame(size, rule);
  const issues = [];
  const want = 2 + Math.floor(rnd() * 3);
  let tries = 0;
  while (s.history.length < want && tries++ < 300) {
    const r = mid + Math.floor(rnd() * 7) - 3, c = mid + Math.floor(rnd() * 7) - 3;
    if (rules.rejectReason(s, r, c) !== null) continue;
    const n = rules.place(s, r, c);
    if (!rules.ended(n)) s = n;
  }
  const opening = s.history.length;
  const maxMs = {};
  const t0 = performance.now();
  let status = 'ongoing';
  while (!rules.ended(s)) {
    if (s.history.length >= o.cap) { status = 'cap'; break; }
    if (performance.now() - t0 > o.wallMs) { status = 'wall'; break; }
    const color = s.turn, level = color === rules.BLACK ? o.black : o.white;
    const t = performance.now();
    const reply = await ai.chooseMove(s.board, color, null, level, rule);
    const dt = performance.now() - t;
    maxMs[level] = Math.max(maxMs[level] || 0, dt);
    const ply = s.history.length;
    if (dt > o.budget[level] + 2000) issues.push({kind: 'slow', ply, color, level, ms: Math.round(dt)});
    if (!reply || reply.cancelled || !reply.move) {
      issues.push({kind: 'nomove', ply, color, level, reply: JSON.stringify(reply)});
      status = 'stall';
      break;
    }
    const r = reply.move.r, c = reply.move.c;
    const why = rules.rejectReason(s, r, c);
    const forb = color === rules.BLACK && rule === 'renju' && rules.isForbidden(s, r, c);
    if (why !== null || forb) {
      issues.push({kind: forb ? 'forbidden' : 'rejected', why, r, c, ply, color, level});
      status = 'illegal';
      break;
    }
    const n = rules.place(s, r, c);
    if (n === s) {
      issues.push({kind: 'place-noop', r, c, ply, color, level});
      status = 'illegal';
      break;
    }
    s = n;
  }
  if (rules.ended(s)) status = s.winner ? 'win' : 'draw';
  const moves = s.history.map(m => [m.r, m.c]);
  return {status, winner: s.winner, plies: s.history.length, opening,
    ms: performance.now() - t0, maxMs, issues, moves: issues.length ? moves : null};
}"""


def play(page, size, black, white, seed, rule='renju'):
    return page.evaluate(RUN_GAME, {'size': size, 'rule': rule, 'black': black, 'white': white,
                                    'seed': seed, 'cap': CAPS[size], 'wallMs': 45000, 'budget': BUDGET})


def main():
    started = time.time()
    problems = []
    errors = []
    summary = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        page.goto((ROOT / 'index.html').as_uri())
        page.wait_for_function('typeof rules !== "undefined" && typeof ai !== "undefined"')

        def batch(label, size, black, white, count, seed0, optional=False):
            results = []
            for i in range(count):
                if optional and time.time() - started > HARD_LIMIT_S:
                    break
                result = play(page, size, black, white, seed0 + i)
                results.append(result)
                for issue in result['issues']:
                    problems.append((label, i, issue, result['moves']))
            stat = Counter(r['status'] for r in results)
            plies = [r['plies'] for r in results]
            worst = {}
            for r in results:
                for lv, ms in r['maxMs'].items():
                    worst[lv] = max(worst.get(lv, 0), ms)
            row = {'label': label, 'games': len(results), 'win': stat['win'], 'draw': stat['draw'],
                   'cap': stat['cap'], 'wall': stat['wall'], 'stall': stat['stall'],
                   'illegal': stat['illegal'], 'avg_plies': round(sum(plies) / max(1, len(plies)), 1),
                   'max_ms': ' '.join('%s=%d' % (k, v) for k, v in sorted(worst.items()))}
            by_level = Counter((black if r['winner'] == 1 else white) for r in results if r['status'] == 'win')
            row['by_level'] = ' '.join('%s=%d' % kv for kv in sorted(by_level.items()))
            summary.append(row)
            print('%-26s games=%d win=%d draw=%d cap=%d wall=%d stall=%d illegal=%d plies=%s %s' % (
                label, row['games'], row['win'], row['draw'], row['cap'], row['wall'], row['stall'],
                row['illegal'], row['avg_plies'], row['max_ms']), flush=True)
            return results

        print('== Part 1: renju legality and termination ==', flush=True)
        batch('15 low vs low', 15, 'low', 'low', 8, 1000)
        batch('15 mid vs mid', 15, 'mid', 'mid', 4, 2000)
        batch('13 low vs low', 13, 'low', 'low', 2, 3000)
        batch('13 mid vs mid', 13, 'mid', 'mid', 1, 3100)
        batch('19 low vs low', 19, 'low', 'low', 2, 4000)
        batch('19 mid vs mid', 19, 'mid', 'mid', 1, 4100)

        print('== Part 2: strength (renju 15x15, colors alternated, paired openings) ==', flush=True)
        strength = []
        for strong, weak, seed0 in (('high', 'low', 7000), ('mid', 'low', 8000)):
            wins = losses = draws = unfinished = 0
            for i in range(STRENGTH_GAMES):
                strong_black = i % 2 == 0
                black, white = (strong, weak) if strong_black else (weak, strong)
                result = play(page, 15, black, white, seed0 + i // 2)
                for issue in result['issues']:
                    problems.append(('strength %s vs %s' % (strong, weak), i, issue, result['moves']))
                if result['status'] == 'win':
                    if (result['winner'] == 1) == strong_black:
                        wins += 1
                    else:
                        losses += 1
                elif result['status'] == 'draw':
                    draws += 1
                else:
                    unfinished += 1
            strength.append((strong, weak, wins, losses, draws, unfinished))
            print('%s vs %s: W%d L%d D%d unfinished=%d' % (strong, weak, wins, losses, draws, unfinished), flush=True)

        print('== Optional: high vs mid (renju 15x15) ==', flush=True)
        if time.time() - started < SOFT_LIMIT_S:
            batch('15 high(B) vs mid(W)', 15, 'high', 'mid', 2, 9000, optional=True)
            batch('15 mid(B) vs high(W)', 15, 'mid', 'high', 2, 9100, optional=True)
        else:
            print('skipped: time budget used', flush=True)
        browser.close()

    print('')
    print('%-26s %5s %4s %4s %4s %4s %5s %7s %9s' % ('setting', 'games', 'win', 'draw', 'cap', 'wall', 'stall', 'illegal', 'avg_plies'))
    for r in summary:
        print('%-26s %5d %4d %4d %4d %4d %5d %7d %9s  wins: %s' % (r['label'], r['games'], r['win'], r['draw'], r['cap'],
                                                       r['wall'], r['stall'], r['illegal'], r['avg_plies'],
                                                       r['by_level'] or '-'))
    print('')
    print('%-14s %4s %4s %4s %10s' % ('strength', 'W', 'L', 'D', 'unfinished'))
    for strong, weak, w, l, d, u in strength:
        print('%-14s %4d %4d %4d %10d' % (strong + ' vs ' + weak, w, l, d, u))
    total_cap = sum(r['cap'] for r in summary)
    print('')
    print('games that hit the move cap (part 1 + optional): %d; elapsed %.1fs' % (total_cap, time.time() - started))

    for label, i, issue, moves in problems:
        print('PROBLEM', label, 'game', i, issue, 'moves', moves)
    for e in errors:
        print('PAGE ERROR', e)
    stalled = sum(r['stall'] + r['wall'] for r in summary)
    if problems or errors or stalled:
        print('FAILED')
        sys.exit(1)
    print('Part 1 assertions passed')


if __name__ == '__main__':
    main()
