# GoldAlert — 现货黄金实时监控与提醒

盯住国际现货黄金（XAU），主源走 TradingView，失败时自动回退到新浪/腾讯；
**代理断开、数据源切换、每小时播报（含 AI 盘面小结）、关键价位、异常波动**
都会发邮件提醒你。
这是"提醒你自己去看盘"的工具，不是自动下单的量化系统。

## 当前能力

- 按固定间隔（默认 5 秒）轮询金价，终端实时面板
- 主源 TradingView（`OANDA:XAUUSD`，经代理）+ 国内源自动兜底
- 代理健康检查：断开 / 恢复即时通知
- 数据源切换、全部数据源失效的通知
- **定时价格播报**：默认每小时整点发一封邮件，含最新价、较昨收涨跌、
  较上一时间窗涨跌、今日高低、买卖价
- **AI 盘面小结**：由 DeepSeek 把最近若干小时的价格序列写成一段简短解读，
  附在播报邮件末尾
- **关键价位提醒**：价位自己设（网页上点点就行），或让 AI 每小时给一组；
  价格接近或突破时立刻发邮件
- **异常波动提醒**：最近 N 分钟（默认 5 分钟）内最高价与最低价的差达到阈值
  （默认 10 美元）就发邮件，同一波行情只提醒一次
- 行情落盘到 JSONL，为后续其它提醒提供数据基础

## 换到新电脑 / 首次安装

把整个文件夹拷到新电脑即可。下面这些**不用带**：

- `.venv`（绑定本机路径，拷过去一定用不了，新的安装会自己重建）
- `logs`、`work`、`__pycache__`、`.pytest_cache`
- `data\state.json`、`data\agent.pid`、`data\ui.json`（运行状态，会重新生成）

**建议带上**：`data\levels.json`（你设的关键价位和灵敏度参数）。

然后在新电脑上：

1. 装 **Python 3.10 或更高**（python.org 下载，安装时勾选 `Add python.exe to PATH`）
2. 双击 **`首次安装.bat`**
3. 按提示回答 5 个问题：发件邮箱、邮箱授权码、收件邮箱、DeepSeek Key、这台电脑有没有代理
4. 装完双击 **`启动(后台常驻).bat`** 开始盯盘

`首次安装.bat` 会自动建虚拟环境、装依赖、跑自检，全程不用敲命令。
配置写在 `config.json`，密钥写在 `.env`，两个文件都只在你本机，不会被上传。

> 换机器最常见的问题就是**代理**：旧机器上 TradingView 走代理，新机器上如果没开代理，
> 安装向导里选「没有代理」即可，程序会自动改用新浪 / 腾讯的国内数据源。

## 手动安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
Copy-Item .env.example .env
.\.venv\Scripts\python.exe run.py doctor
.\.venv\Scripts\python.exe run.py
```

## 邮件提醒配置（QQ 邮箱）

不需要短信那样的资质审核，几分钟就能用。三步：

1. **拿授权码**：QQ 邮箱网页版 → 设置 → 账户 → 找到"IMAP/SMTP 服务" → 开启并**生成授权码**（16 位）。
   这是独立的一串码，**不是你的 QQ 密码**。
2. **填收件地址**：编辑 `config.json` 的 `email` 段，填 `username` / `sender` / `recipients`。
3. **填授权码**：编辑 `.env`：

   ```
   GOLDALERT_SMTP_PASSWORD=你的16位授权码
   ```

## AI 盘面解读配置（DeepSeek）

DeepSeek 是 OpenAI 兼容接口、**国内直连**（不走代理），价格便宜。

1. 到 platform.deepseek.com 注册并创建 **API Key**。
2. 编辑 `.env`：

   ```
   GOLDALERT_DEEPSEEK_API_KEY=sk-你的key
   ```

3. 跑 `run.py doctor`，AI 那一段会**真的试跑一次**并把结果打印出来。

**它做什么**：把最近 `lookback_hours` 小时的价格序列（压缩成 `max_points` 个采样点）
连同当前报价交给模型，让它写一段说明运行区间、方向节奏、关键价位的短评。

**它不做什么**：不预测涨跌、不给买卖建议、不涉及消息面。
因为工具只采集了价格，没有新闻/宏观数据；让模型凭空预测是误导。
提示词里已明确约束了这几点。

**失败会怎样**：AI 请求超时、额度用尽、Key 失效等任何情况，都只记一条日志，
播报照常发送（只有数字，没有小结），**不会影响监控**。

## 关键价位提醒

**怎么设**：双击项目里的 `设置目标价位.bat`（桌面也有快捷方式），浏览器会自动打开
一个本地设置页（只监听本机）：

- 填「价位 + 说明」→ 添加；表格里可以修改、停用、删除
- 「填入现价 / 今日高 / 今日低」一键带出常用价位
- 「让 AI 给一组关键价位」→ 预览 → 「全部采纳为我的价位」
  （已经同名的价位会被直接覆盖，不会多出一条重复的）
- 页面上还能调：接近距离、冷却时间、重新武装距离、各提醒开关
- 「AI 提问」里可以用一句话问当前盘面（例如「现在是震荡还是单边？」），
  AI 只根据监控采到的行情数据回答，超出数据范围的问题会直说答不了

**AI 自己给**：监控每 `levels.ai_refresh_minutes` 分钟（默认 60）让 DeepSeek 结合最近
`ai.lookback_hours` 小时（默认 4 小时）的数据给一组关键价位，写回 `data/levels.json`。
这段时间的数据会压缩成 `ai.max_points` 个采样点（默认 48，约每 5 分钟一个点）。
它只替换 AI 自己那条，**你手动加的和采纳进来的一个都不会动**。

**同名只保留一条**：不管是手动填的、还是采纳 AI 的，只要名称和已有价位一样，
就直接把那条的价格改成新的，不会再多出一条重复的；名称留空时不合并，填几条就是几条。
反过来，AI 刷新时如果给出的价位和你某个手动价位同名，AI 那条会被直接跳过
（你的价位优先），也不会制造重名条目。

另外，AI 不会把**当前报价本身**当成关键价位——现价一直在动，没有参考价值。
提示词里已经交代过；万一模型还是报了「当前报价」「现价」这类名字，
程序在解析时会直接丢掉。
AI 换价位时会给你的邮箱发一封「AI 已更新关键价位」（上面写着上次和本次的价位）；
如果这一轮 AI 给出的价位和上一轮完全一样，就不会重复发邮件吵你。

**什么时候提醒**：

| 情况 | 说明 |
| --- | --- |
| 接近 | 价格进入价位 `proximity_usd` 美元以内，提醒一次 |
| 突破 | 价格从一侧穿到另一侧时提醒一次（标题里写清是「上破」还是「跌破」） |
| 各提醒一次 | 先靠近、再突破时，两封都会发（接近和突破各自计冷却，互不占用） |
| 防刷屏 | 同一价位、同一类提醒在 `cooldown_minutes` 分钟内不重复；价格离开 `proximity_usd + rearm_usd` 之后才重新武装 |
| 不补发 | 刚启动、或价位刚添加时只记录基准，不会一次补发一堆提醒 |
| AI 换价位 | AI 刷新后如果价位和上一轮不同，单独发一封邮件；完全一样就不发 |

提醒里会写清现价、触发条件、现价在该价位的哪一侧、价位来源（手动 / AI）。

**命令行也能改**（不想开网页时）：

```powershell
run.py levels                    # 看当前价位和设置
run.py levels add 4250 上破看多  # 加一个
run.py levels rm 4250            # 按价位删（也可以按编号删）
run.py levels ai                 # 立刻让 AI 给一组
run.py levels ui                 # 打开网页设置页
```

**开关**：`config.json` 里 `levels.enabled` 关掉整套功能，`levels.ai_enabled` 只关
AI 自动更新；灵敏度和提醒类型在网页上改即可。

## 异常波动提醒

**看什么**：最近 `volatility.window_minutes` 分钟（默认 5）里采集到的价格，取其中的
最高价与最低价，两者相差 ≥ `volatility.threshold_usd` 美元（默认 10）就发一封邮件：

```
⚡ 异常波动：5 分钟内 11.00 美元
窗口最高   4,191.00
窗口最低   4,180.00
振幅       11.00 美元（阈值 10.00）
形态       震荡上行（较窗口起点 +10.00）
```

**什么时候不提醒**：同一波行情只发一次。要等波动收窄到
`threshold_usd × rearm_ratio`（默认 6 美元）以内、并且距上次提醒超过
`cooldown_minutes`（默认 10 分钟），才会重新开始盯下一波，避免连发。

**怎么调**：改 `config.json` 里的 `volatility` 段（见下表），改完重启监控生效。

## 命令

| 命令 | 作用 |
| --- | --- |
| `run.py` | 启动实时监控（默认） |
| `run.py once` | 只取一次当前金价 |
| `run.py report` | 立即发一封播报（含 AI 小结），验证内容用 |
| `run.py doctor` | 自检代理 / 数据源 / 邮件 / AI（会真发测试邮件、真跑一次 AI） |
| `run.py levels [...]` | 管理关键价位：`add` / `rm` / `ai` / `ui`，详见上文 |
| `run.py --cycles 3 --no-display` | 只跑 3 轮、逐行日志输出（调试用） |
| `run.py -i 2` | 指定刷新间隔（秒） |

## 配置说明（`config.json`）

### `report` — 定时播报

| 字段 | 说明 |
| --- | --- |
| `enabled` | 是否开启 |
| `every_minutes` | 每隔多少分钟播报一次，默认 60 |
| `align_to_clock` | `true` 对齐整点；`false` 从启动时刻顺延 |
| `compare_window_minutes` | 与多久之前的价格做对比，默认 60 |

下次播报时间记在 `data/state.json`，**重启不会漏发或重复发**。

### `ai` — AI 盘面解读

| 字段 | 说明 |
| --- | --- |
| `enabled` | 是否开启 |
| `provider` / `base_url` / `model` | 默认 DeepSeek + `deepseek-chat` |
| `api_key_env` | 读取 Key 的环境变量名，默认 `GOLDALERT_DEEPSEEK_API_KEY` |
| `lookback_hours` | 交给模型的历史窗口，默认 4 小时 |
| `max_points` | 压缩成多少个采样点，默认 48（4 小时内约每 5 分钟一个点） |
| `max_words` | 小结字数上限，默认 150 |
| `temperature` / `max_tokens` / `timeout` | 生成参数 |
| `proxy` | 留空=直连；填了才走代理（国内直连即可，一般留空） |

### `levels` — 关键价位提醒

| 字段 | 说明 |
| --- | --- |
| `enabled` | 整套关键价位提醒的开关 |
| `file` | 价位文件位置，默认 `data/levels.json`（网页界面改的就是它） |
| `ai_enabled` | 是否让 AI 自动给关键价位 |
| `ai_refresh_minutes` | AI 重新给价位的间隔，默认 60 分钟 |
| `ai_max_levels` | AI 一次最多给几个，默认 4 |

价位本身、接近距离、冷却时间等存在 `data/levels.json` 里，由网页界面维护，不用手改。

### `volatility` — 异常波动提醒

| 字段 | 说明 |
| --- | --- |
| `enabled` | 是否开启 |
| `window_minutes` | 观察窗口，默认 5 分钟 |
| `threshold_usd` | 窗口内最高价与最低价相差多少美元就提醒，默认 10 |
| `cooldown_minutes` | 两次提醒之间至少隔多久，默认 10 分钟 |
| `rearm_ratio` | 波动收窄到阈值的这个比例以内才重新武装，默认 0.6（即 6 美元） |

### 其它

| 字段 | 说明 |
| --- | --- |
| `symbol` | 监控品种，目前为 `XAU` |
| `sources` | 数据源优先级，越靠前越先用 |
| `proxy` | 代理地址，TradingView 必须走它 |
| `tradingview_symbol` | TradingView 代码，默认 `OANDA:XAUUSD` |
| `proxy_modes` | 每个源的代理模式：`always` / `never` / `auto` |
| `interval_seconds` | 轮询间隔 |
| `failure_threshold` | 连续失败几次才判定异常，防止抖动误报 |
| `proxy_check_interval` | 代理健康检查间隔（秒） |
| `email.*` | 邮件通知配置 |
| `history_file` / `state_file` / `log_file` | 行情、状态、日志落盘位置 |

## 已知限制

- **TradingView 的 REST 报价约 10–30 秒粒度**，不是逐笔实时（用 BTC 实测验证过）。
  想换成秒级，把 `sources` 里的 `sina`/`tencent` 提到 `tradingview` 前面即可。
- **TradingView 通道依赖代理**。代理一关，该源立刻失效并触发提醒，同时自动切到国内源。
- 邮件与 AI 走**直连**（国内服务），不经过代理；`ai.proxy` 留空即可。
- 数据源之间报价天生有细微差异，所以同一时刻只启用一个主源，避免价格来回跳。
- AI 小结只是对已有价格数据的复述，**不构成投资建议**；AI 给的关键价位同理，
  它是对已采集数据里高低点的归纳，没有消息面输入，用不用由你决定。
- 关键价位提醒靠监控进程触发：监控没在跑就不会提醒（设置页只负责改价位）。

## 代码结构

```
run.py                  命令行入口（monitor / once / report / doctor / levels）
安装向导.py           首次安装向导：问几个问题，写好 config.json 与 .env
首次安装.bat            一键安装：找 Python、建环境、装依赖、跑自检
启动(后台常驻).bat      后台静默启动监控
启动(前台显示).bat      前台启动，能看到实时面板
停止.bat                停止后台监控
查看状态.bat            看监控还在不在、最近日志
设置目标价位.bat        打开关键价位设置页
安装开机自启.bat        / 取消开机自启.bat
goldalert/
  config.py             配置加载与校验
  sources.py            行情源：tradingview / sina / tencent / gold_api
  router.py             数据源调度：自动回退 + 事件通知
  monitor.py            监控主循环
  reports.py            定时价格播报
  levels.py             关键价位：存取、接近/突破触发、AI 刷新
  levels_ui.py          关键价位设置页（本地网页 + 接口）
  ai.py                 AI 盘面解读（DeepSeek）与关键价位建议
  display.py            终端实时面板（rich）
  notify.py             通知渠道：console / log / 仪表盘 / email / webhook
  store.py              行情历史（JSONL）与状态（JSON）
  util.py               北京时间、日志、代理探测
tests/                  pytest 单元测试
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```