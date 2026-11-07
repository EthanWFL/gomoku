"""验证工具（非游戏依赖）：python3 tests/gomoku.py；需 Python Playwright + Chromium。"""
from pathlib import Path
from statistics import mean
import json
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
# 默认有界面（与此前一致）；GOMOKU_HEADLESS=1 或 --headless 切换为无界面。
HEADLESS = "--headless" in sys.argv[1:] or os.environ.get("GOMOKU_HEADLESS", "").lower() in ("1", "true", "yes")

# 计时断言的放宽倍数：默认 1（本地阈值不变）；共享 CI 机器可设 GOMOKU_TIMING_SLACK=3 等。
# 只放宽“耗时上限”类断言；功能性与可见性断言不受影响。
SLACK = float(os.environ.get("GOMOKU_TIMING_SLACK", "1"))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=HEADLESS)
    page = browser.new_page(viewport={"width": 1024, "height": 1100}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto((ROOT / "index.html").as_uri())
    page.bring_to_front()
    page.wait_for_function("document.getElementById('board').width === 1280")
    assert page.evaluate("document.visibilityState") == "visible"
    print("内联规则与AI自检：", page.evaluate("runSelfChecks()"), flush=True)

    # 取消：搜索进行中通过 signal 终止 Worker，必须很快以 cancelled 返回；另测预先中止与主线程回退。
    cancellation = page.evaluate("""async (slack) => {
      const board = rules.newGame().board;
      for (const [r, c, k] of [[7,7,1],[7,8,2],[8,8,1],[6,6,2],[8,7,1],[9,7,2],[6,8,1],[8,6,2],[9,9,1],[10,10,2]]) board[r][c] = k;
      const controller = new AbortController();
      const started = performance.now();
      const pending = ai.chooseMove(board, 1, controller.signal, 'high');
      setTimeout(() => controller.abort(), 80);
      const reply = await pending;
      const elapsed = performance.now() - started;
      if (!reply.cancelled || reply.move !== null || elapsed > 400 * slack) throw new Error('未在搜索中取消');
      const pre = new AbortController(); pre.abort();
      const early = await ai.chooseMove(board, 1, pre.signal, 'high');
      if (!early.cancelled || early.move !== null) throw new Error('预先取消无效');
      const saved = ai.settings.worker;
      ai.settings.worker = false;
      try {
        const fb = await ai.chooseMove(board, 1, null, 'mid');
        if (fb.cancelled || !fb.move || board[fb.move.r][fb.move.c]) throw new Error('主线程回退失败');
      } finally { ai.settings.worker = saved; }
      return { elapsedMs: elapsed };
    }""", SLACK)
    print("计算中取消：", cancellation, flush=True)

    # 固定种子、合法前缀、不同密度；不包含生成局面与断言耗时。
    performance_report = page.evaluate("""async () => {
      const check = (ok, message) => { if (!ok) throw new Error(message); };
      let seed = 20261029;
      const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
      const shuffle = points => {
        for (let i = points.length - 1; i > 0; i--) {
          const j = Math.floor(random() * (i + 1));
          [points[i], points[j]] = [points[j], points[i]];
        }
        return points;
      };
      const samples = [];
      const measure = async (state, category, level = 'mid') => {
        const before = JSON.stringify(state);
        const started = performance.now();
        const result = await ai.chooseMove(state.board, state.turn, null, level);
        const elapsedMs = performance.now() - started;
        check(!result.cancelled && result.move && !state.board[result.move.r][result.move.c], 'AI落点非法');
        check(JSON.stringify(state) === before, 'AI改变原局面');
        check(result.computeMs >= 0 && result.totalMs >= result.computeMs && elapsedMs >= result.totalMs, '计时错误');
        check(document.visibilityState === 'visible', '测试页面不在前台');
        samples.push({ category, level, stones: state.history.length, computeMs: result.computeMs,
          totalMs: result.totalMs, elapsedMs });
      };
      // 36个散布局面：空盘至中盘；每个都是合法且未结束的历史。
      for (let i = 0; i < 36; i++) {
        let state = rules.newGame();
        const points = shuffle(Array.from({length:225}, (_, n) => ({r:Math.floor(n / 15), c:n % 15})));
        for (const point of points) {
          if (state.history.length >= i * 3) break;
          const next = rules.place(state, point.r, point.c);
          if (!rules.ended(next)) state = next;
        }
        await measure(state, '随机合法前缀');
      }
      // 36个稠密局面：条纹满盘无五连，打乱同色顺序仍能得到合法前缀。
      for (let i = 0; i < 36; i++) {
        const pools = [[], [], []];
        for (let r = 0; r < 15; r++) for (let c = 0; c < 15; c++)
          pools[(r + 2 * c) % 4 < 2 ? 1 : 2].push({r, c});
        shuffle(pools[1]); shuffle(pools[2]);
        let state = rules.newGame();
        for (let j = 0; j < 119 + i * 3; j++) {
          const point = pools[state.turn].pop();
          state = rules.place(state, point.r, point.c);
        }
        check(!rules.ended(state), '稠密局面应仍可继续');
        await measure(state, '稠密合法前缀');
      }
      // 24个集中交锋局面，包含靠边和斜线威胁。
      for (let i = 0; i < 24; i++) {
        const points = shuffle(Array.from({length:81}, (_, n) => ({r:Math.floor(n / 9) + i % 7, c:n % 9 + i % 7})));
        let state = rules.newGame();
        for (const point of points) {
          if (state.history.length >= 8 + i * 2) break;
          const next = rules.place(state, point.r, point.c);
          if (!rules.ended(next)) state = next;
        }
        await measure(state, '局部交锋');
      }
      // 24个明确取胜或防守场景，验证它们也遵循同一计时流程。
      for (let i = 0; i < 24; i++) {
        let state = rules.newGame();
        for (let j = 0; j < 4; j++) {
          state = rules.place(state, 2 + i % 7, 3 + j);
          if (j < 3 || i % 2 === 0) state = rules.place(state, 14, j * 2);
        }
        await measure(state, i % 2 === 0 ? '必胜' : '防守');
      }
      // 19 路与高难度：候选更多、还多一层应手，仍须留足 100ms 余量。
      for (const [n, level] of [[19, 'mid'], [19, 'high'], [15, 'high']]) {
        for (let i = 0; i < 8; i++) {
          const pools = [[], [], []];
          for (let r = 0; r < n; r++) for (let c = 0; c < n; c++)
            pools[(r + 2 * c) % 4 < 2 ? 1 : 2].push({r, c});
          shuffle(pools[1]); shuffle(pools[2]);
          let state = rules.newGame(n);
          for (let j = 0; j < Math.floor(n * n / 2) - 12 + i; j++) {
            const point = pools[state.turn].pop();
            state = rules.place(state, point.r, point.c);
          }
          check(!rules.ended(state), '大变体局面应仍可继续');
          await measure(state, `${n}路 · ${level === 'high' ? '高难度' : '中难度'}`, level);
        }
      }
      return { samples, userAgent: navigator.userAgent, dpr: devicePixelRatio };
    }""")
    samples = performance_report["samples"]
    timing = {key: {"mean": round(mean(s[key] for s in samples), 3),
                    "max": round(max(s[key] for s in samples), 3)}
              for key in ("computeMs", "totalMs", "elapsedMs")}
    print("前台性能：", json.dumps({"samples": len(samples), **timing}, ensure_ascii=False), flush=True)
    budget = {"low": 80, "mid": 500, "high": 1500}
    over_budget = [s for s in samples if s["elapsedMs"] >= (budget[s["level"]] + 400) * SLACK]
    assert len(samples) >= 100 and not over_budget, (timing, over_budget[:3])
    by_level = {lv: round(max(s["elapsedMs"] for s in samples if s["level"] == lv), 1)
                for lv in budget if any(s["level"] == lv for s in samples)}
    print("各档最大耗时(ms)：", json.dumps(by_level), flush=True)

    # 只在验证页面观测状态与绘制；生产文件不需要测试接口。
    page.evaluate("""() => {
      window.test = { state: rules.newGame(), draws: 0, markers: [], signals: [], replies: [] };
      for (const name of ['place', 'undo', 'newGame']) {
        const original = rules[name];
        rules[name] = (...args) => { const state = original(...args); test.state = state; return state; };
      }
      const context = document.getElementById('board').getContext('2d');
      const clear = context.clearRect.bind(context), stroke = context.strokeRect.bind(context);
      context.clearRect = (...args) => { test.draws++; test.markers = []; return clear(...args); };
      context.strokeRect = (...args) => { test.markers.push(args); return stroke(...args); };
      const originalAI = ai.chooseMove;
      ai.chooseMove = (...args) => {
        test.signals.push(args[2]);
        return originalAI(...args).then(reply => { test.replies.push(reply); return reply; });
      };
      test.cell = (r, c) => {
        const canvas = document.getElementById('board'), rect = canvas.getBoundingClientRect();
        const gap = 560 / (test.state.board.length - 1);
        return {left: rect.left, top: rect.top, width: rect.width, height: rect.height,
          x: rect.left + (40 + c * gap) * rect.width / 640, y: rect.top + (40 + r * gap) * rect.height / 640};
      };
      test.click = (r, c) => {
        const canvas = document.getElementById('board'), at = test.cell(r, c);
        canvas.dispatchEvent(new MouseEvent('click', {clientX: at.x, clientY: at.y, button: 0, bubbles: true}));
      };
      test.move = (r, c, offset = 0) => {
        const canvas = document.getElementById('board'), at = test.cell(r, c);
        canvas.dispatchEvent(new PointerEvent('pointermove', {clientX: at.x + offset * at.width / 640, clientY: at.y}));
      };
      test.frames = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      test.select = (id, value) => {
        const element = document.getElementById(id); element.value = value; element.dispatchEvent(new Event('change'));
      };
    }""")

    def steps(number):
        page.wait_for_function(f"document.getElementById('steps').textContent === '{number}'")

    def frames():
        page.evaluate("test.frames()")

    def snapshot():
        return page.evaluate("test.state")

    def restart():
        page.locator("#restart").click()
        steps(0)

    def click(row, column):
        page.evaluate("([r, c]) => test.click(r, c)", [row, column])

    def actual_click(row, column):
        box = page.locator("#board").bounding_box()
        gap = 560 / (page.evaluate("test.state.board.length") - 1)
        page.mouse.click(box["x"] + (40 + column * gap) * box["width"] / 640,
                         box["y"] + (40 + row * gap) * box["height"] / 640)

    def assert_black():
        assert page.locator("#turn-text").inner_text() == "黑棋"
        assert page.evaluate("test.state.turn === rules.BLACK && !rules.ended(test.state) && !test.state.winLine.length")

    actual_click(7, 7)
    steps(1)
    actual_click(7, 7)
    steps(1)
    assert "第8行，第8列，黑棋" in page.locator("#board").get_attribute("aria-label")
    page.locator("#undo").click()
    steps(0)
    assert_black()
    assert "第8行，第8列，空位" in page.locator("#board").get_attribute("aria-label")
    assert page.evaluate("test.state.board.flat().every(cell => cell === 0)")

    # 键盘选中已占位置也有独立描边；悔棋后标签立即同步。
    page.locator("#board").focus()
    page.keyboard.press("Enter")
    steps(1)
    frames()
    assert page.evaluate("test.markers.length === 2 && test.markers[0][0] === 301 && test.markers[0][1] === 301")
    assert "黑棋" in page.locator("#board").get_attribute("aria-label")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Space")
    steps(2)
    assert "第8行，第9列，白棋" in page.locator("#board").get_attribute("aria-label")
    page.locator("#undo").click()
    steps(1)
    assert "第8行，第9列，空位" in page.locator("#board").get_attribute("aria-label")

    # 同格移动不重绘；离开/进入需要重绘，跨轮次也必须刷新。
    page.evaluate("test.move(6, 6)")
    frames()
    draws = page.evaluate("test.draws")
    page.evaluate("test.move(6, 6, 2)")
    frames()
    assert page.evaluate("test.draws") == draws
    page.evaluate("document.getElementById('board').dispatchEvent(new PointerEvent('pointerleave'))")
    frames()
    assert page.evaluate("test.draws") == draws + 1
    page.evaluate("test.move(6, 6)")
    frames()
    assert page.evaluate("test.draws") == draws + 2
    click(6, 6)
    frames()
    assert page.evaluate("test.draws") == draws + 3

    # 三档尺寸不得影响棋局、预览坐标；通过键盘将焦点移到尺寸选择器。
    page.locator("#board").focus()
    page.keyboard.press("ArrowUp")
    selected = page.locator("#board").get_attribute("aria-label")
    state_before = snapshot()
    page.locator("#zoom").focus()
    for size in (480, 560, 640):
        page.locator("#zoom").select_option(str(size))
        page.wait_for_function(f"document.getElementById('board').width === {size * 2}")
        frames()
        box = page.locator("#board").bounding_box()
        assert box["width"] == box["height"] == size
        assert snapshot() == state_before
        assert page.locator("#board").get_attribute("aria-label") == selected
        assert page.evaluate("test.markers.length === 2 && test.markers[0][0] === 261 && test.markers[0][1] === 221")
    page.locator("#zoom").select_option("560")
    restart()
    assert page.locator("#zoom").input_value() == "560"
    assert page.locator("#board").bounding_box()["width"] == 560
    page.evaluate("test.move(7, 7)")
    frames()
    preview = page.locator("#board").screenshot()
    page.evaluate("document.getElementById('board').dispatchEvent(new PointerEvent('pointerleave'))")
    frames()
    assert page.locator("#board").screenshot() != preview

    page.set_viewport_size({"width": 552, "height": 1000})
    page.wait_for_function("document.getElementById('board').width === 1040")
    assert page.locator("#board").bounding_box()["width"] == 520
    page.set_viewport_size({"width": 375, "height": 1000})
    # 去掉最小宽度后：375px 视口下 main 宽度为 min(640, 375 - 32) = 343，DPR 2 时画布内部宽度为 686
    page.wait_for_function("document.getElementById('board').width === 686")
    assert page.locator("#board").bounding_box()["width"] == 343
    page.set_viewport_size({"width": 1024, "height": 1100})
    page.locator("#zoom").select_option("640")
    print("双人悔棋、键盘与读屏、预览去重、三档显示尺寸保留状态、窄屏和DPR：通过", flush=True)

    # 终局只允许重开，红点与五子高亮仍绘制；不通过悔棋恢复终局。
    restart()
    for i in range(5):
        click(7, i)
        if i < 4:
            click(0, i * 2)
    steps(9)
    assert "黑棋获胜" in page.locator("#result").inner_text()
    assert page.evaluate("test.state.winLine.length === 5 && test.state.history[test.state.history.length - 1].c === 4")
    for selector in ("#undo", "#mode", "#size", "#level"):
        assert page.locator(selector).is_disabled()
    assert page.locator("#zoom").is_enabled()
    ended = snapshot()
    click(8, 8)
    page.locator("#board").focus()
    label = page.locator("#board").get_attribute("aria-label")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    assert snapshot() == ended and page.locator("#board").get_attribute("aria-label") == label
    frames()
    page.screenshot(path=str(Path(tempfile.gettempdir()) / "gomoku-optimized-win.png"))
    restart()
    assert_black()
    assert page.locator("#mode").is_enabled() and page.locator("#size").is_enabled()

    page.locator("#mode").select_option("ai")
    page.evaluate("() => { test.click(7,7); test.click(7,8); test.click(7,9); if(test.state.history.length !== 1) throw Error('AI回合重复落子'); }")
    steps(2)
    page.locator("#undo").click()
    steps(0)
    assert_black()
    page.evaluate("() => { test.click(7,7); document.getElementById('undo').click(); }")
    steps(0)
    assert_black()
    page.wait_for_function("test.replies.some(reply => reply.cancelled)")

    click(7, 7)
    steps(2)
    preceding_round = snapshot()
    page.evaluate("() => { test.click(0,0); document.getElementById('undo').click(); }")
    steps(2)
    assert snapshot() == preceding_round
    assert_black()

    # 实际AI任务取消（而非仅丢弃返回值）。
    for action in ("restart", "mode", "undo"):
        restart()
        page.locator("#mode").select_option("ai")
        page.evaluate("""action => {
          test.lastReplyCount = test.replies.length;
          test.click(7,7);
          if (action === 'mode') test.select('mode', 'pvp');
          else document.getElementById(action).click();
          if (!test.signals[test.signals.length - 1].aborted) throw Error('未取消旧任务');
        }""", action)
        page.wait_for_function("test.replies.length > test.lastReplyCount")
        assert page.evaluate("test.replies[test.replies.length - 1].cancelled")
        steps(0)
        assert_black()

    # 即使旧任务忽略取消、在新的AI回合内返回，版本守卫也必须拦截。
    page.evaluate("""() => {
      test.realAI = ai.chooseMove;
      ai.chooseMove = (board, color, signal) => new Promise(resolve => {
        test.pending.push({signal, resolve: () => resolve({move:{r:6,c:7}, cancelled:false, computeMs:0, totalMs:0})});
      });
    }""")
    for action in ("restart", "mode", "undo"):
        restart()
        page.locator("#mode").select_option("ai")
        page.evaluate("""action => {
          test.pending = [];
          test.click(7,7);
          if (action === 'mode') { test.select('mode', 'pvp'); test.select('mode', 'ai'); }
          else document.getElementById(action).click();
          test.click(8,8);
          if (!test.pending[0].signal.aborted) throw Error('旧信号未取消');
          test.pending[0].resolve();
        }""", action)
        steps(1)
        assert page.evaluate("test.state.board[8][8] === 1 && test.state.board[6][7] === 0")
        assert "电脑思考" in page.locator("#result").inner_text()
        page.evaluate("test.pending[1].resolve()")
        steps(2)
        assert page.evaluate("test.state.board[6][7] === 2")

    # 尺寸切换绝不能取消/重启当前AI任务，并保留选中坐标。
    restart()
    page.evaluate("test.pending = []; test.click(7,7)")
    before_size = snapshot()
    label = page.locator("#board").get_attribute("aria-label")
    for size in (480, 560, 640):
        page.locator("#zoom").select_option(str(size))
        assert snapshot() == before_size
        assert page.locator("#board").get_attribute("aria-label") == label
        assert page.evaluate("test.pending.length === 1 && !test.pending[0].signal.aborted")
    page.evaluate("test.pending[0].resolve()")
    steps(2)
    page.evaluate("() => { ai.chooseMove = test.realAI; }")
    print("人机回合撤销、防重复点击、三种取消入口、迟到结果拦截、显示尺寸切换保留AI：通过", flush=True)

    # 路数：切换即开新局，点击、判胜、终局锁定都按新路数生效。
    page.locator("#mode").select_option("pvp")
    page.locator("#size").select_option("19")
    steps(0)
    assert page.evaluate("test.state.board.length === 19 && test.state.board.every(row => row.length === 19)")
    assert page.evaluate("!test.state.history.length && test.state.turn === rules.BLACK")
    click(18, 18)
    steps(1)
    assert page.evaluate("test.state.board[18][18] === 1")
    assert "第19行，第19列，黑棋" in page.locator("#board").get_attribute("aria-label")
    restart()
    for i in range(5):
        click(9, 5 + i)
        if i < 4:
            click(0, i * 2)
    steps(9)
    assert page.evaluate("test.state.winner === 1 && test.state.winLine.length === 5 && test.state.board.length === 19")
    assert "黑棋获胜" in page.locator("#result").inner_text()
    for selector in ("#undo", "#mode", "#size", "#level"):
        assert page.locator(selector).is_disabled()
    assert page.locator("#zoom").is_enabled()
    page.locator("#zoom").select_option("480")
    page.wait_for_function("document.getElementById('board').width === 960")
    restart()
    assert page.locator("#size").is_enabled() and page.locator("#level").is_enabled()
    page.locator("#size").select_option("13")
    steps(0)
    assert page.evaluate("test.state.board.length === 13 && test.state.history.length === 0")
    click(12, 12)
    steps(1)
    assert page.evaluate("test.state.board[12][12] === 1")
    assert "第13行，第13列，黑棋" in page.locator("#board").get_attribute("aria-label")
    page.locator("#size").select_option("15")
    steps(0)
    page.locator("#zoom").select_option("640")
    print("13/19路切换、点击坐标、判胜与终局锁定：通过", flush=True)

    # 难度：低/中/高都要堵连五、都要抓自己的连五，且不得改写传入棋盘。
    tactics = page.evaluate("""async () => {
      const out = {};
      for (const level of ['low', 'mid', 'high']) {
        const block = rules.newGame(13).board;
        for (let c = 3; c < 7; c++) block[6][c] = rules.BLACK;
        const before = JSON.stringify(block);
        const reply = await ai.chooseMove(block, rules.WHITE, null, level);
        const own = rules.newGame(19).board;
        for (let c = 3; c < 7; c++) own[9][c] = rules.WHITE;
        for (let c = 3; c < 6; c++) own[1][c] = rules.BLACK;
        const win = await ai.chooseMove(own, rules.WHITE, null, level);
        out[level] = {block: [reply.move.r, reply.move.c], win: [win.move.r, win.move.c],
          clean: JSON.stringify(block) === before};
      }
      const jump = rules.newGame().board;
      for (const c of [3, 4, 6]) jump[10][c] = rules.BLACK;
      const high = await ai.chooseMove(jump, rules.WHITE, null, 'high');
      const mid = await ai.chooseMove(jump, rules.WHITE, null, 'mid');
      return {out, jumpHigh: [high.move.r, high.move.c], jumpMid: [mid.move.r, mid.move.c]};
    }""")
    for name in ("low", "mid", "high"):
        assert tactics["out"][name]["block"] == [6, 7], tactics
        assert tactics["out"][name]["win"] == [9, 7], tactics
        assert tactics["out"][name]["clean"]
    assert tactics["jumpHigh"] == [10, 5] and tactics["jumpMid"] == [10, 5], tactics
    print("三档难度：堵连五、自身连五、跳三识别、不改传入棋盘：通过", flush=True)

    # 刷新续局：只重放落子顺序，设置与棋盘一起恢复；重开后存档不再带回棋子。
    page.locator("#mode").select_option("ai")
    page.locator("#level").select_option("high")
    page.locator("#size").select_option("13")
    steps(0)
    click(6, 6)
    steps(2)
    page.locator("#zoom").select_option("560")
    page.wait_for_function("document.getElementById('board').width === 1120")
    page.reload()
    page.wait_for_function("document.getElementById('steps').textContent === '2'")
    assert page.locator("#mode").input_value() == "ai"
    assert page.locator("#level").input_value() == "high"
    assert page.locator("#size").input_value() == "13"
    assert page.locator("#zoom").input_value() == "560"
    assert page.locator("#board").bounding_box()["width"] == 560
    assert page.evaluate("""() => {
      const canvas = document.getElementById('board'), rect = canvas.getBoundingClientRect();
      const gap = 560 / 12;
      canvas.dispatchEvent(new PointerEvent('pointermove', {clientX: rect.left + (40 + 6 * gap) * rect.width / 640,
        clientY: rect.top + (40 + 6 * gap) * rect.height / 640}));
      return document.getElementById('board').getAttribute('aria-label');
    }""").endswith("黑棋")
    page.locator("#restart").click()
    steps(0)
    page.reload()
    page.wait_for_function("document.getElementById('steps').textContent === '0'")
    assert page.locator("#size").input_value() == "13" and page.locator("#zoom").input_value() == "560"
    print("刷新续局、重开后清空、设置保留：通过", flush=True)

    assert not errors, errors
    report = {"browser": browser.version, "userAgent": performance_report["userAgent"],
              "headless": HEADLESS, "visibility": "visible", "dpr": 2, "timing": timing,
              "samples": samples, "pageErrors": errors, "checks": "全部通过"}
    output = ROOT / "tests" / "gomoku-results.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("全部通过；报告：", output, flush=True)
    browser.close()
