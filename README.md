# AI GEE Downloader

AI 原生的 **Google Earth Engine（GEE）遥感数据下载与任务管理工具**，以 **本地 MCP Server** 形态提供，可被 ChatGPT / Codex / Claude 等 AI Agent 调用。

用户只需告诉 AI：数据集 ID、时间范围、分辨率、边界（Boundary Asset / bbox 坐标矩形 / GeoJSON，三选一）、输出目录、坐标系 —— 系统自动完成登录检查、数据集识别、时间筛选、边界解析、下载策略规划（默认本地直下，必要时自动分片）、执行下载、GeoTIFF QA 与元数据生成。

> 设计文档见 [AI_GEE_下载器整体设计方案.md](./AI_GEE_下载器整体设计方案.md)。
>
> 🧭 **想直接让 AI 帮你下载数据？** 复制 [GEE_MCP_Prompt_Template.md](./GEE_MCP_Prompt_Template.md)
> 里的用户提示词模板，填上你的需求（数据 / 时间 / 区域 / 分辨率 / 输出目录）发给 AI 即可，全程无需接触代码。

## 架构

```text
AI Client (ChatGPT / Codex / Claude)
        │  MCP (stdio)
        ▼
   GEE MCP Server
   ├── Dataset Resolver    数据集类型识别 (Image / ImageCollection)
   ├── Boundary Resolver   Boundary Asset 解析
   ├── Time Resolver       时间筛选 / 分组 / 聚合
   ├── Grid Resolver       CRS / 分辨率处理
   ├── Download Planner    规模估算 + 策略选择
   ├── Task Manager        状态机 + 本地任务库
   └── QA Engine           GeoTIFF 检查 + 元数据
        │
        ├──► Google Earth Engine API（earthengine-api）
        └──► 本地文件系统（GeoTIFF + metadata.json）
```

## 安装

```bash
# Python 3.10+
pip install -e .[dev]
# Google Drive 本地回传（Export 结果从 Drive 拉回本地）：
pip install -e .[drive]
```

### 配置

编辑 `config.yaml`：

```yaml
gee:
  project: your-earth-engine-project   # 必填：你的 GEE 项目 ID

filesystem:
  default_output: "D:/GEE_Data"        # 默认输出目录
  allowed_roots:                       # AI 只能写这些目录（安全白名单）
    - "D:/GEE_Data"
```

## 首次登录（GEE OAuth）

```bash
python server.py 启动前，先执行一次 OAuth：
python -m earthengine authenticate
```

或在 MCP 客户端中调用工具 `gee_login`（浏览器自动打开 Google 登录页，授权 Earth Engine，凭据持久化到本地）。

## MCP 工具

| 工具 | 说明 |
|---|---|
| `gee_login` | 登录 / 检查认证状态 / 初始化 |
| `gee_dataset_info` | 数据集信息（类型 / 波段 / CRS / 分辨率 / 时间范围） |
| `gee_boundary_info` | 边界检查（Asset / bbox / GeoJSON 三选一：要素数 / 范围 / 面积） |
| `gee_search_datasets` | **搜索 GEE 数据集**（本地 Catalog：关键词 / Band / 分辨率 / 时间分辨率 / 时间范围 / 平台 / 区域，无需登录） |
| `gee_validate_dataset` | **验证数据集**（用当前账号：类型 / 真实 Band / 可访问性，结果缓存 1 小时） |
| `gee_catalog_update` | **更新本地数据集目录**（官方 GEE STAC 抓取 → SQLite + FTS5；支持 `seed=true` 离线演示） |
| `gee_download` | 核心下载（支持 `dry_run` 预览计划） |
| `gee_task_status` | 查询本地任务状态 |
| `gee_list_tasks` | 列出本地任务 / GEE Export Tasks |

## GEE 数据集发现（Dataset Discovery）

> 设计文档见 [AI_GEE_Dataset_Discovery_设计方案.md](./AI_GEE_Dataset_Discovery_设计方案.md)。

用户可以直接说“帮我找 EVI 数据 / 找一个 2017-2021 年覆盖中国、日尺度、1km 左右的 EVI 数据”，系统自动完成 **搜索 → 筛选 → 比较 → 推荐 → 确认 → 下载** 闭环：

```text
gee_catalog_update         更新本地 Catalog（官方 STAC 抓取，无需登录）
        ↓
gee_search_datasets        搜索候选数据集（无需登录）
        ↓                    关键词 / Band / 分辨率 / 时间频率 / 时间范围 / 平台 / 区域
        ↓                    规则 Ranking + match_reasons
gee_validate_dataset       用当前账号验证（需先 gee_login）
        ↓
gee_download               进入下载
```

### 首次使用

```text
1. gee_catalog_update            # 抓取全部官方数据集（约 1100+，几分钟；可先 limit=50 体验）
2. gee_search_datasets(query="EVI", bands=["EVI"], spatial_resolution=1000,
                       temporal_resolution="daily", start_date="2017-01-01",
                       end_date="2021-12-31", region="China", limit=10)
3. gee_validate_dataset("MODIS/061/MOD13A2")   # 确认类型 / Band / 可访问
4. gee_download(...)                            # 进入下载
```

- **搜索不需要登录**；验证 / 下载需要 `gee_login`。
- Catalog 存于 `data/gee_catalog.db`（SQLite + FTS5），搜索不访问 Google；怀疑过期时运行 `gee_catalog_update`。
- 离线 / 演示：`gee_catalog_update(seed=true)` 用内置 13 个示例数据集（不访问网络）。
- 空间分辨率按“接近度”打分（`1/(1+|log2(actual/preferred)|)`）；时间覆盖区分完整 / 部分 / 无覆盖；`bands` 参数是强过滤。
- MCP 另注册了 `gee_search` Prompt，引导 AI 完成“解析需求 → 搜索 → 比较 → 推荐 → 验证 → 下载”。

### 注册到 MCP 客户端

Claude Desktop `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "ai-gee-downloader": {
      "command": "python",
      "args": ["D:/Awsr/GEE-MCP/server.py"],
      "cwd": "D:/Awsr/GEE-MCP"
    }
  }
}
```

其他支持 stdio MCP 的客户端（Cursor、Windsurf、Cherry Studio 等）同理。

### 接入 DeepSeek Harness（DSH）

编辑 `$DSH_HOME/profiles/web/cordis.patch.yml`（本机为 `C:\Users\Lenovo\.dsh\profiles\web\cordis.patch.yml`），追加：

```yaml
- insert:
    - id: mcp-gee
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: gee
        transport: stdio
        command: 'C:\Users\Lenovo\AppData\Local\Programs\Python\Python310\python.exe'
        args: ['D:/Awsr/GEE-MCP/server.py']
        cwd: 'D:/Awsr/GEE-MCP'
        env:
          HTTP_PROXY: 'http://127.0.0.1:7897'
          HTTPS_PROXY: 'http://127.0.0.1:7897'
          ALL_PROXY: 'http://127.0.0.1:7897'
        toolCallTimeoutMs: 600000
```

- DSH 对 `cordis.patch.yml` 有配置热加载（HMR），保存后自动重连，无需重启；
- 模型侧工具名称为 `mcp__gee__gee_login`、`mcp__gee__gee_download` 等；
- `env` 中的代理变量是 GEE 访问所必需的（本机 127.0.0.1:7897），按需修改。

## 典型交互

> 直接可用的用户提示词模板见 [GEE_MCP_Prompt_Template.md](./GEE_MCP_Prompt_Template.md)
> （标准下载 / 长时间序列 / 单景快照 / 让 AI 推荐数据集，四种场景照抄填空即可）。

> 用户：下载 MODIS NDVI，2021 年全年，1 km，使用我的安徽边界资产，EPSG:3857，保存到 D:\GEE_Data。

AI 调用：

```text
gee_download(
  dataset="MODIS/061/MOD13Q1",
  start_date="2021-01-01",
  end_date="2021-12-31",
  boundary="projects/xxx/assets/Anhui",
  scale="1km", crs="EPSG:3857",
  output="D:/GEE_Data",
  dry_run=false
)
```

系统自动规划 → 提交任务 → 返回 `task_id` → AI 轮询 `gee_task_status` → 完成后返回文件清单、QA 报告与 metadata.json 路径。

> 边界不必是 Asset：也可以直接传坐标矩形或 GeoJSON（`boundary` / `bbox` / `geometry` 三选一）。

> 用户：下载北京周边 MODIS EVI，2021 年 7 月，1 km，范围 116°E–118°E、39°N–41°N，按月平均。

```text
gee_download(
  dataset="MODIS/061/MOD13A2",
  start_date="2021-07-01",
  end_date="2021-07-31",
  bbox=[116.0, 39.0, 118.0, 41.0],   # [west, south, east, north]，EPSG:4326
  scale="1km", bands=["EVI"],
  time_mode="monthly", aggregation="mean",
  output="D:/GEE_Data",
  dry_run=false
)
```

> 大数据量务必先用 `dry_run=true`：返回影像数、估算体积、推荐策略（逐景 / 按月 / 按年分组）与预计任务数，征询用户后再执行。

## 项目结构

```text
server.py                  MCP Server 入口（10 个工具 + gee_search prompt）
GEE_MCP_Prompt_Template.md 用户提示词模板（照抄填空即可让 AI 帮你下载）
config.py                  配置加载
config.yaml                配置文件（白名单 / 阈值 / 项目 ID / catalog 参数）
catalog/                   GEE 数据集发现：collector / normalizer / database / search / ranking / schema.sql / seed_data
gee/                       GEE 封装：auth / dataset / collection / boundary / export / task / validator
planner/                   下载规划：size_estimator / temporal_planner / download_planner
download/                  下载引擎：direct / export / drive / manager
raster/                    GeoTIFF 检查：inspect / validate / metadata
models/                    数据模型：request / task / result / dataset / search
prompts/                   MCP Prompt 内容：search（gee_search）
utils/                     工具：paths / logging / dates
data/                      Catalog 数据库（data/gee_catalog.db，自动生成）
tests/                     单元测试
```

## 下载策略（本地优先，默认永不远程导出）

| 策略 | 说明 |
|---|---|
| **auto（默认）** | **始终 Direct Download 本地直下**：`Image.getDownloadURL()` 逐 band 下载，外包矩形过大时自动分片拼接，**全程不经 Google Drive** |
| `strategy="export"` | 仅当你**显式指定**时才使用 `ee.batch.Export.image.toDrive()`（远程中转 → Drive → 本地回传） |

- 分块按**请求字节预算**切分（`planner.max_direct_request_bytes`，默认 44MiB）：GEE 按 float64 8B/px 计算请求大小（上限 48MiB），分块像素 ≈ 预算 ÷ 8，**不再写死 800 万像元**（8M×8B=64MB 会超限）。
- dry_run 的 `estimated_pixels` 按**目标 CRS 实际网格**估算（Web Mercator 纬度拉伸 ~1.4 倍），与真实下载一致；`warnings` 明确提示「planner 建议 vs 实际策略」的差异与选项。
- 网格维度 / 分片数超保护上限时：dry_run 返回 `direct_feasible=false` 并给出两个选项（缩小范围 / 显式 `strategy="export"`）；真实提交则 fail-fast，**不再静默执行必败路径**。
- 分块缓存断点续传：直下分块存到 `{输出}.chunks/`，失败重试只补缺失分块。
- `strategy="export"` 前置检查 Google Drive 空间，不足且 direct 可行时自动回退本地直下，否则明确报错。
- 多时相逐景导出有任务数保护（超过 30 景建议用 `time_mode=monthly/annual` 分组）。

## 时间分组与聚合

`gee_download` 支持：

```text
time_mode:  native | daily | monthly | annual
aggregation: mean | median | mosaic | first | best | min | max | sum
stack_periods: false（默认，每时间片一个文件） | true（多时间片合并为一个多波段 tif）
dtype: auto（保留 GEE 原始 float64） | float32 | float64 | int16 | uint16 | ...
```

- `native` + 影像较少 → 逐景下载（本地直下）；
- 影像较多 → 自动按月 / 按年分组（可配合 `aggregation=mean` 等输出 12 个月平均 / 5 个年文件），避免大量逐景请求。
- `stack_periods=true`：时间维堆叠——把多个时间片合并为一个多波段 GeoTIFF（波段数=时间片数，每个波段对应一天/一月），文件名如 `2021-01-01-2021-12-31.tif`，波段描述 = `{波段名}_{时间片}`。适合小 tif 合并；大 tif 建议保持默认单波段逐文件。堆叠过程有进度（`gee_task_status` 的 `progress` / `progress_note`）。
- `dtype=float32`（或 int16 等）：下载后把 float64 输出转换为目标 dtype 并 deflate 压缩，体积可减小数倍（如 1.6GB → ~718MB）。

## QA（内容级检查）

- 元数据检查：CRS / 分辨率 / 尺寸 / 波段数 / 全部波段 dtype / NoData / transform / bounds；
- **内容检查**：非零像元占比（低于 `qa.min_valid_fraction` 视为异常，如整景全 0 的失败请求）、min/max 值域、全 0 检测；大文件降采样读取，不整读内存。

## 安全设计

- 输出目录白名单：AI 只能写入 `filesystem.allowed_roots`；
- OAuth 凭据 / token 绝不进入工具输出与日志（日志自动打码）；
- Export Task 仅操作当前认证账号允许的资源。

## 测试

```bash
pytest -q          # 纯逻辑单元测试，无需 GEE 凭据
```

## Roadmap（见设计文档）

- 已实现：数据集发现（`gee_search_datasets` / `gee_validate_dataset` / `gee_catalog_update` + `gee_search` prompt）、时间维堆叠（`stack_periods=true` 多波段合并）、分块缓存断点续传（P1-5）、网格对齐拼接（P1-4）、QA 内容级检查（P1-6）、输出 dtype 转换（P2-10）、任务进度（P2-7）、dry_run 意图 vs 推荐提示（P2-8）、Export 前置 Drive 空间检查与回退（P2-9）
- 第二阶段：月度/年度聚合、自动任务分批、Cloud Storage、重试、数据集对比（`gee_compare_datasets`）、预览（`gee_preview_dataset`）
- 第三阶段：语义搜索（Embedding + 向量库）、AI Remote Sensing Data Acquisition Agent（多源数据同网格预处理 + dataset manifest）

## License

MIT
