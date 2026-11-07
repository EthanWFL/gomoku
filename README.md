# 五子棋 Gomoku

零依赖的纯前端五子棋，整个游戏就是一个 `index.html`，没有框架、没有构建步骤，用浏览器直接打开即可游玩。

在线试玩：https://ethanwfl.github.io/gomoku/

## 功能

- **规则**：自由规则（五子及以上即胜）和连珠（Renju）禁手规则可选。连珠规则下黑棋禁走三三、四四、长连，恰好五连不受禁手限制，白棋不受限制。棋盘支持 13、15、19 路。
- **人机对战**：低、中、高三档难度，可选执黑或执白。AI 使用迭代加深的 alpha-beta 搜索、Zobrist 置换表，并带有 VCF、VCT 威胁搜索，能识别和防守四三等组合杀。搜索放在 Web Worker 中运行，界面不会卡住，Worker 不可用时退回主线程。
- **双人对战**：同一台设备上轮流落子。
- **对局辅助**：悔棋、重做、提示、显示手数、可开关的落子音效。
- **复盘与棋谱**：逐步回放、从任意一步继续对局、导出和导入 JSON 棋谱、复制分享链接。
- **移动端**：窄屏自适应，触屏采用先预览再确认的两步落子，避免误触。
- **其他**：跟随系统的深色模式、键盘操作和读屏标注、刷新后自动续局。

## 本地运行

```bash
git clone https://github.com/EthanWFL/gomoku.git
cd gomoku
open index.html
```

也可以直接用任意浏览器打开 `index.html`。

## 测试

在页面地址后加 `#self-test`，会在控制台运行规则、棋型和 AI 的内联回归自检。

完整回归使用 Python Playwright，需要先安装 Playwright 和 Chromium：

```bash
pip install playwright
playwright install chromium
python3 tests/gomoku.py
```

默认会启动有界面的 Chromium。设置环境变量 `GOMOKU_HEADLESS=1`（或加 `--headless` 参数）可改为无界面运行：

```bash
GOMOKU_HEADLESS=1 python3 tests/gomoku.py
```

耗时类断言（取消响应、各档 AI 用时上限）可用 `GOMOKU_TIMING_SLACK` 按倍数放宽，默认 1；性能较弱或负载较高的机器可设为 2、3。

报告写入 `tests/gomoku-results.json`，该文件已被 `.gitignore` 忽略。

仓库带有 GitHub Actions 工作流（`.github/workflows/test.yml`）：推送或向 `main` 提 PR 时，会在 ubuntu-latest 上以无界面模式运行这套回归，并将 `GOMOKU_TIMING_SLACK` 设为 3。

## 代码结构

`index.html` 内的脚本分三层：

1. `rules`：规则层，所有状态变更都返回新对象，包含落子、悔棋、判胜和连珠禁手。
2. `ai`：电脑层，只读取棋盘，不访问界面。
3. 界面层：画布绘制、交互、存档、复盘。

注意：AI 的 Worker 里有一份棋型判断的拷贝，修改 `rules.shape` 时需要同步修改 `ai` 中对应的 `shape`，目前没有自动一致性检查。
