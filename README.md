# xhs-selection-system

复刻并改进一套小红书选品系统。

参考文章：《小红书虚拟电商选品实战：从找商品、盯销量，到发现正在起量的品，我做了两套选品系统》

- 系统 1：APP 商品采集器（ADB 控制手机，按关键词、店铺批量找商品 ID）：`src/xhs_collector`
- 系统 2：销量监控（定时记销量，算增量，异动推送，Web 界面）：`src/xhs_monitor`

## 文档

- [docs/使用教程.md](docs/使用教程.md)（另有 [PDF 版](docs/使用教程.pdf)）：面向非技术人员的图文教程；双击 `安装.command`、`启动.command`、`停止.command` 即可使用；重新生成 PDF：`uv run --with markdown python docs/build_pdf.py`

- [docs/requirements.md](docs/requirements.md)：两套系统的完整需求清单，标注来源（文章、推测、建议）
- [docs/data-source-feasibility.md](docs/data-source-feasibility.md)：销量数据源和手机界面的实测结果

## 安装和测试

依赖：Python 3.11+、uv、本机已安装 Google Chrome（系统 2 用它加载商品页，不需要登录）。

```bash
uv sync
uv run pytest
```

## 系统 2：销量监控

```bash
uv run uvicorn xhs_monitor.app:app --host 127.0.0.1 --port 8765
```

打开 http://127.0.0.1:8765 。七个标签页：

| 标签 | 内容 |
|---|---|
| 工作台 | 商品、店铺、分类、待抓取数量；今日增量 Top 10；最近提醒；最近抓取 |
| 采集 | 系统 1 的网页入口：三种模式（关键词、整店、关键词→前几个店铺），实时日志、结果表、暂停/继续/停止、CSV 和 Excel 下载、手机截图预览、手机常亮开关 |
| 商品 | 粘贴商品链接、分享短链或商品 ID 添加；搜索、分类筛选；按今日、昨日、上小时、7 天、30 天增量排序；批量改分类、删除、暂停/恢复监控、复制链接、抓取选中、强制抓取全部；点"曲线"看历史折线和快照 |
| 店铺 | 由商品页自动得到店铺，每个店铺每天记一次总销量，显示日、7 天、30 天增量；复制店铺账号；"采集该店商品"直接转到采集页 |
| 统计 | 每天全部商品的销量增量合计、分类汇总 |
| 日志 | 抓取记录、已发出的提醒、失败记录 |
| 设置 | 抓取时段、预警规则（hour 和 streak 两种）、push+ 和钉钉配置、发送测试消息、分类的改名和删除、MCP 配置示例 |

访问密码：设置环境变量 `XHS_API_TOKEN` 后，所有接口要求 `Authorization: Bearer <token>` 或 `X-API-Token`，网页首次打开会提示输入。只在本机使用可以不设。`XHS_COLLECTOR_DIR` 指定网页采集的输出目录（默认 `data/collector`）。

MCP（给 AI 助手用）：`uv run python -m xhs_monitor.mcp_server`，通过环境变量 `XHS_MONITOR_URL`（默认 http://127.0.0.1:8765）和 `XHS_API_TOKEN` 连接已运行的系统 2。工具：`list_items`、`get_item_history`、`list_shops`、`get_dashboard`、`get_stats`、`get_recent_alerts`、`add_items`、`run_fetch`。

行为：
- 抓取按"本地整点小时槽位"判断：每个商品每个小时槽位内抓一次，每 5 分钟轮询；电脑休眠或服务重启后错过的整点，下一次轮询会补上。时段默认 10:00-23:59，时段外只抓还没有任何记录的新商品。
- 同一商品在一个小时槽位内失败最多重试 2 次，间隔至少 10 分钟。
- 销量被取整（如"1.8万+"）的商品每 20 小时才抓一次；增量只给下界，标 `?`，预警只用下界，不会误报；从精确值变成取整值时，会发一次"进入取整区"提醒。
- 遇到验证或限流（HTTP 403/429/461/471 或页面出现验证提示）立即暂停 6 小时并通知，页面上可手动恢复。
- 同一商品同一规则的冷却：hour 规则 3 小时，streak 规则 20 小时。
- 默认规则：近 1 小时新增 >= 5；价格 >= 29 且近 1 小时新增 > 1。可在设置页增加"连续 N 天每天新增 >= M"。
- 推送配置保存在 SQLite（`data/monitor.db`，明文），也可用环境变量 `PUSHPLUS_TOKEN`、`DINGTALK_WEBHOOK`、`DINGTALK_SECRET` 作为默认值。`XHS_DB` 指定数据库路径，`XHS_NO_SCHEDULER=1` 关闭定时抓取。

开机自启和防休眠（macOS，launchd + caffeinate）：

```bash
deploy/launchd.sh install    # 安装并启动；uninstall、status、print 同理
```

合盖仍会休眠，需要接电并保持开盖，或自行设置合盖不休眠。

## 系统 1：APP 商品采集器

前提：安卓手机用 USB 连接并开启 USB 调试，保持亮屏并解锁（建议开发者选项里打开"充电时保持唤醒"），已登录小红书。本机同时连 Cisco VPN 时无线调试不可用，请用 USB。

```bash
# 关键词（可多个，或 --keywords-file）
uv run python -m xhs_collector search "教师资格证真题" --limit 20 --min-sales 100 --push http://127.0.0.1:8765 --category 教资
# 整店采集（店铺 ID 或店铺名），按销量排序
uv run python -m xhs_collector shop --id <24位店铺ID> --sort 销量 --all --xlsx
uv run python -m xhs_collector shop --name "某某的店" --limit 30
# 只采最近 7 天上新的商品
uv run python -m xhs_collector shop --id <店铺ID> --new-days 7
# 先按关键词找店铺，再采这些店铺的商品（每个关键词前 3 家店，每家 10 个）
uv run python -m xhs_collector keyword-shops "教资" --shops 3 --per-shop 10
# 排查和设备
uv run python -m xhs_collector dump          # 导出当前界面控件和截图
uv run python -m xhs_collector devices
uv run python -m xhs_collector wifi-on       # 通过 USB 开启 TCP 调试，打印地址
uv run python -m xhs_collector connect 192.168.1.8:5555
```

不写子命令时按 `search` 处理。

| 参数 | 说明 |
|---|---|
| `--limit` / `--all` | 本次最多采集多少个；`--all` 忽略数量（仍受每日上限限制） |
| `--daily-cap` | 每天最多点开多少个商品详情，默认 100 |
| `--min-sales`、`--only-with-sales`、`--min-price`、`--max-price` | 点开详情前用列表上的值过滤，节省次数 |
| `--sort-sales`（search）、`--sort 综合/销量/新品/价格`（shop） | 列表排序 |
| `--new-days N`（shop） | 使用"上新"标签，只采最近 N 天上新 |
| `--xlsx` | 同时输出 Excel（CSV 始终输出，表头为中文） |
| `--push URL` / `--category` | 采集后调用系统 2 入池 |
| `--instance` | 应用分身选择框里要点的名称，默认 `Ⅱ·小红书`（vivo 分身） |
| `--max-swipes` | 每个列表最多下滑多少屏 |
| `--serial` | 设备序列号，只有一台设备时可省略 |

输出字段：来源、商品ID、商品标题、价格、销量、店铺名称、店铺销量、店铺标签（如"3年老店""上新12件"）、加购信息、上新日期、店铺ID、采集时间、备注。粉丝数由系统 2 从网页取得。

控制：输出目录下放一个 `STOP` 文件会停止并保存已采集的结果，放 `PAUSE` 文件会暂停，删除后继续；Ctrl+C 同样会保存结果。看过的商品卡片记录在 `state.json`，重复运行同一命令会跳过它们，相当于断点续跑。

保护：遇到验证或风控文字、页面等待超时、找不到标签或应用选择项时，立即停止并截图到输出目录；每次点开之间随机等待 2 到 4 秒。

保持常亮：采集期间默认自动让手机常亮，结束后恢复原设置（`--no-keep-awake` 可关闭）。做法是保存原值后，把 `screen_off_timeout` 调到最大，并把 `stay_on_while_plugged_in` 设为 7（AC、USB、无线都保持唤醒）。开发者选项里的"充电时保持唤醒"在这台 vivo 上只勾了 USB（值 2），而手机把电脑的供电识别成 AC，所以仍会熄屏。也可手动 `awake on|off|status`；原值保存在输出目录的 `awake.json`，进程被强杀后用 `awake off` 恢复。常亮只防熄屏，不会解锁；如果手机已锁屏，采集会停止并提示。

真机验证（2026-10-30，vivo V2547A，小红书 9.51.0 分身）：关键词、店铺 ID、店铺名（销量排序）、上新标签四种方式都采到了商品；用 `--push` 推到系统 2 后，系统 2 抓到了销量、价格和店铺；App 里的店铺 ID 与网页接口里的完全一致。

## 已知限制

- 系统 1 在网页里只有手机截图预览，没有实时投屏；系统 1 不采店铺粉丝数（系统 2 能取到）。
- 网页没有登录体系，只有可选的访问令牌，默认只监听 127.0.0.1。
- 系统 1 只按这台 vivo 手机和小红书 9.51.0 调试，改版后控件文字可能变化；没有测过连续采集几百个的稳定性和是否触发验证；没有投屏。
- 上新日期按分组标题推断，屏幕顶部没有标题时沿用上一屏的，首组默认"今天"；只验证了"今天"这一组。
- 采集用的账号目前有"禁止发笔记"的提示，建议换小号。
- 每小时数千个商品的规模没有测过；建议从少量商品开始，逐步放量。
- 店铺销量在 1 万以下是精确值（实测"已售9486"），1 万以上取整到 0.1 万。
- 8000-9999 区间按精确值处理，未经样本确认。
- push+ 和钉钉真实发送、launchd 安装后的整点触发，没有在真实环境里跑过。
- 网页采集和命令行采集共用同一套代码；真机上网页路径成功过一次，另有一次因手机前台被其他 App 占用而超时（现在超时提示会写明手机当前前台应用）。
