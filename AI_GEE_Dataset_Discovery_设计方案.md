# AI GEE Downloader — GEE 数据集发现与搜索设计方案

> 版本：v0.2  
> 模块：GEE Dataset Discovery / Search  
> 定位：为 AI GEE Downloader 增加“自然语言查找 GEE 数据集”的能力。

## 1. 功能目标

用户可以直接告诉 AI：

> 帮我找 EVI 数据。

或者：

> 找一个 2017-2021 年覆盖中国、日尺度、1 km 左右的 EVI 数据。

系统自动完成：

```text
自然语言需求
    ↓
AI 解析需求
    ↓
gee_search_datasets
    ↓
GEE Dataset Catalog
    ↓
关键词 / Band / 时间 / 空间 / 时间分辨率过滤
    ↓
候选数据集
    ↓
AI Ranking
    ↓
Dataset Cards
    ↓
AI 推荐
    ↓
用户确认
    ↓
gee_dataset_info / gee_validate_dataset
    ↓
gee_download
```

最终形成：

```text
搜索 → 筛选 → 比较 → 推荐 → 确认 → 下载
```

## 2. 总体架构

```text
                    AI Client
                ChatGPT / Codex
                       │
                       │ MCP
                       ▼
              ┌───────────────────┐
              │  GEE MCP Server   │
              └─────────┬─────────┘
                        │
        ┌───────────────┼────────────────┐
        │               │                │
        ▼               ▼                ▼
     Prompts          Tools          Resources
        │               │                │
        ▼               ▼                ▼
 Search Prompt     Search Tool       Catalog DB
 Download Prompt   Dataset Tool      Dataset Metadata
                   Download Tool
                        │
                        ▼
                Earth Engine API
```

核心职责分离：

- **Catalog**：负责发现
- **GEE API**：负责验证
- **AI**：负责理解、比较、推荐
- **Downloader**：负责执行

## 3. MCP Tool

原有工具：

```text
gee_login
gee_dataset_info
gee_boundary_info
gee_download
gee_task_status
gee_list_tasks
```

增加：

```text
gee_search_datasets
gee_validate_dataset
```

最终：

```text
GEE MCP
│
├── Authentication
│   └── gee_login
│
├── Catalog
│   ├── gee_search_datasets
│   ├── gee_dataset_info
│   └── gee_validate_dataset
│
├── Boundary
│   └── gee_boundary_info
│
├── Download
│   └── gee_download
│
└── Tasks
    ├── gee_task_status
    └── gee_list_tasks
```

## 4. `gee_search_datasets`

支持：

- 关键词
- 数据类型
- Band
- 空间分辨率
- 时间分辨率
- 起止时间
- 传感器
- 平台
- Provider
- 区域
- 标签
- 排序
- 结果数量限制

建议输入 Schema：

```json
{
  "query": "EVI",
  "dataset_type": "ImageCollection",
  "bands": ["EVI"],
  "spatial_resolution": 1000,
  "resolution_tolerance": 2.0,
  "temporal_resolution": "daily",
  "start_date": "2017-01-01",
  "end_date": "2021-12-31",
  "platform": "MODIS",
  "sensor": "MODIS",
  "provider": null,
  "region": "China",
  "limit": 10
}
```

最小调用：

```json
{
  "query": "EVI",
  "limit": 10
}
```

## 5. Dataset Card

搜索结果必须结构化，推荐：

```json
{
  "id": "MODIS/061/MOD13Q1",
  "name": "MOD13Q1.061 Terra Vegetation Indices 16-Day Global 250m",
  "type": "ImageCollection",
  "description": "...",
  "provider": "NASA LP DAAC",
  "platform": "Terra",
  "sensor": "MODIS",
  "spatial": {
    "resolution": 250,
    "unit": "meter",
    "coverage": "global"
  },
  "temporal": {
    "start": "2000-02-18",
    "end": null,
    "cadence": "16-day"
  },
  "bands": [
    {
      "name": "NDVI",
      "description": "Normalized Difference Vegetation Index",
      "scale": 0.0001,
      "offset": 0
    },
    {
      "name": "EVI",
      "description": "Enhanced Vegetation Index",
      "scale": 0.0001,
      "offset": 0
    }
  ],
  "tags": ["evi", "ndvi", "modis", "vegetation"],
  "gee_snippet": "ee.ImageCollection('MODIS/061/MOD13Q1')",
  "catalog_url": "...",
  "updated_at": "..."
}
```

至少包含：

### 基础信息

```text
id
name
type
description
```

### 数据来源

```text
provider
platform
sensor
mission
```

### 空间信息

```text
resolution
native_crs
coverage
spatial_extent
```

### 时间信息

```text
start_date
end_date
cadence
temporal_frequency
```

### Band

```text
name
description
units
dtype
scale
offset
valid_min
valid_max
```

### GEE 信息

```text
gee_snippet
catalog_url
asset_type
```

### 搜索信息

```text
tags
keywords
updated_at
```

## 6. Catalog 数据库

MVP 推荐 SQLite：

```text
data/gee_catalog.db
```

### datasets

```sql
CREATE TABLE datasets (
    id TEXT PRIMARY KEY,
    name TEXT,
    type TEXT,
    description TEXT,

    provider TEXT,
    platform TEXT,
    sensor TEXT,
    mission TEXT,

    start_date TEXT,
    end_date TEXT,
    cadence TEXT,

    spatial_resolution REAL,
    spatial_resolution_unit TEXT,

    native_crs TEXT,
    coverage TEXT,

    catalog_url TEXT,
    gee_snippet TEXT,

    updated_at TEXT
);
```

### bands

```sql
CREATE TABLE bands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    dataset_id TEXT,
    name TEXT,
    description TEXT,
    units TEXT,

    dtype TEXT,
    scale REAL,
    offset REAL,

    valid_min REAL,
    valid_max REAL,

    FOREIGN KEY(dataset_id)
        REFERENCES datasets(id)
);
```

### tags

```sql
CREATE TABLE tags (
    dataset_id TEXT,
    tag TEXT,

    PRIMARY KEY(dataset_id, tag),

    FOREIGN KEY(dataset_id)
        REFERENCES datasets(id)
);
```

推荐 SQLite FTS5：

```sql
CREATE VIRTUAL TABLE dataset_fts
USING fts5(
    dataset_id,
    name,
    description,
    tags,
    bands
);
```

## 7. Catalog 更新

不要每次搜索都访问 Google。

推荐：

```text
GEE Data Catalog
       ↓
Catalog Collector
       ↓
Metadata Normalizer
       ↓
SQLite
       ↓
FTS5
       ↓
MCP Search
```

建议提供：

```text
gee_catalog_update
```

未来可以：

- 定时更新
- 手动更新
- 增量更新
- 检测新增 / 删除 / 修改数据集

## 8. Query Normalization

例如：

```text
用户：EVI
```

转换：

```yaml
original: EVI

keywords:
  - EVI
  - Enhanced Vegetation Index

bands:
  - EVI

aliases:
  - enhanced vegetation index
  - enhanced vegetation
```

同义词表：

```yaml
EVI:
  - EVI
  - Enhanced Vegetation Index
  - enhanced vegetation

NDVI:
  - NDVI
  - Normalized Difference Vegetation Index

LST:
  - LST
  - Land Surface Temperature
  - land surface temperature

soil moisture:
  - soil moisture
  - SM
  - surface soil moisture
  - volumetric soil moisture

PET:
  - PET
  - potential evapotranspiration
```

## 9. 搜索流程

```text
User
 ↓
"找 EVI"
 ↓
AI
 ↓
gee_search_datasets
 ↓
Normalize Query
 ↓
Keyword Search
 ↓
Band Search
 ↓
Metadata Filter
 ↓
Scoring
 ↓
Top N
 ↓
Dataset Cards
```

## 10. Ranking

推荐：

```text
Score =
    keyword_score
  + band_score
  + resolution_score
  + temporal_score
  + date_coverage_score
  + platform_score
  + region_score
```

### Keyword Score

优先级：

```text
Band name == query
    >
Dataset name contains query
    >
Description contains query
    >
Tag contains query
```

### Band Score

如果用户明确要求：

```json
{"bands": ["EVI"]}
```

则 EVI Band 应作为强过滤条件。

### Resolution Score

推荐：

```text
resolution_score =
    1 / (1 + abs(log2(actual / preferred)))
```

例如用户要求 1000 m：

```text
1000m → 最优
500m  → 较优
2000m → 较优
250m  → 可接受
5000m → 较差
```

### Temporal Score

如果用户明确要求 daily：

```text
daily
    >
8-day
    >
16-day
    >
monthly
```

如果 `temporal_resolution` 是 hard filter，则不满足直接排除。

### Date Coverage

完整覆盖：

```text
dataset.start <= requested.start
AND
dataset.end >= requested.end
```

无重叠：

```text
dataset.start > requested.end
OR
dataset.end < requested.start
```

部分覆盖可以标记：

```text
PARTIAL COVERAGE
```

## 11. Region

MVP 支持：

```text
global
China
Asia
Europe
North America
```

未来支持 Boundary Asset：

```json
{
  "region": "projects/xxx/assets/Anhui"
}
```

此时可通过 GEE API 验证空间覆盖。

## 12. Catalog Search 与 GEE Validation

必须分离：

```text
gee_search_datasets
        ↓
Candidate Dataset
        ↓
gee_validate_dataset
        ↓
gee_dataset_info
        ↓
gee_download
```

搜索回答：

> 官方目录中有哪些？

验证回答：

> 当前 GEE 账号能否访问？对象类型是什么？Band 是否真实存在？

不要对所有搜索结果逐个调用 GEE API。

推荐：

```text
SQLite
 ↓
Top 20
 ↓
Ranking
 ↓
Top 5
 ↓
GEE Validation
```

## 13. `gee_validate_dataset`

建议：

```json
{
  "name": "gee_validate_dataset",
  "description": "Validate a GEE dataset ID using the current Earth Engine account.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "dataset_id": {
        "type": "string"
      }
    },
    "required": ["dataset_id"]
  }
}
```

返回：

```json
{
  "valid": true,
  "accessible": true,
  "type": "ImageCollection",
  "bands": [
    "NDVI",
    "EVI",
    "DetailedQA"
  ]
}
```

## 14. Search Result Schema

推荐：

```json
{
  "query": "EVI",
  "filters": {
    "spatial_resolution": 1000,
    "temporal_resolution": "daily",
    "start_date": "2017-01-01",
    "end_date": "2021-12-31"
  },
  "total": 12,
  "results": [
    {
      "rank": 1,
      "score": 0.97,
      "id": "MODIS/...",
      "name": "...",
      "type": "ImageCollection",
      "spatial_resolution": 463.313,
      "temporal_resolution": "daily",
      "start_date": "2000-...",
      "end_date": "2023-...",
      "bands": ["EVI"],
      "match_reasons": [
        "EVI band available",
        "Daily",
        "Full date coverage",
        "Spatial resolution close to 1 km"
      ]
    }
  ]
}
```

## 15. 推荐系统

搜索结果返回后，AI 负责推荐。

例如：

> 我要 2017-2021 年中国区域 EVI，最好日尺度，1km 左右。

AI 可以：

```text
推荐：

① MOD09GA EVI
   Daily
   约 463 m
   覆盖 2000-2023
   Global

推荐理由：
✓ EVI
✓ Daily
✓ 覆盖 2017-2021
✓ 分辨率接近 1km
✓ Global
```

备选：

```text
② MOD13Q1
   250m
   16-day
   2000-
```

重要原则：

> MCP Search 不负责替 AI 做最终研究判断，但必须提供足够完整、结构化的 metadata，让 AI 可以进行解释性推荐。

## 16. Match Reasons

每个结果增加：

```json
{
  "match_reasons": [
    "EVI band available",
    "Daily temporal resolution",
    "Full requested date coverage",
    "Spatial resolution close to 1 km",
    "Global coverage"
  ]
}
```

## 17. MCP Prompt

建议增加：

```text
gee_search
```

用途：

> 引导用户寻找 GEE 数据集。

Prompt 建议：

```text
你可以帮助用户寻找 Google Earth Engine 数据集。

用户可以提供：
- 变量
- Band
- 时间范围
- 空间分辨率
- 时间分辨率
- 传感器
- 平台
- 区域

你应该：
1. 解析用户需求。
2. 调用 gee_search_datasets。
3. 比较候选数据集。
4. 给出推荐及理由。
5. 如果用户选择数据集，调用 gee_validate_dataset。
6. 如果用户要求下载，再进入 gee_download。
```

## 18. Search → Download 闭环

```text
用户：
"找 EVI"
        ↓
gee_search_datasets
        ↓
Dataset Cards
        ↓
AI 推荐
        ↓
用户：
"我要第一个"
        ↓
gee_validate_dataset
        ↓
用户：
"下载 2017-2021"
        ↓
gee_download
        ↓
Download Planner
        ↓
Export Task
        ↓
GeoTIFF
```

## 19. 与 Downloader 的最终融合

```text
                 AI GEE Downloader
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
        Dataset Discovery       Data Download
              │                     │
              ▼                     ▼
          Search                  Plan
              │                     │
              ▼                     ▼
         Recommend               Export
              │                     │
              └──────────┬──────────┘
                         ▼
                    Local Dataset
                         │
                         ▼
                  Metadata / QA
```

## 20. 未来语义搜索

最终支持：

> 我需要一个 2017-2021 年的 EVI 数据，最好日尺度，1km 左右，中国区域，后面我要和 GLDAS 土壤湿度融合。

AI 解析：

```yaml
variable: EVI

time:
  start: 2017-01-01
  end: 2021-12-31

temporal:
  preferred: daily

spatial:
  preferred_resolution: 1000
  tolerance: 2

region:
  China

application:
  soil_moisture_downscaling
```

然后进行：

```text
Catalog
 ↓
Metadata Filter
 ↓
Ranking
 ↓
AI Recommendation
```

这使系统从普通的：

> GEE Dataset Search

升级成：

> AI Remote Sensing Dataset Discovery

## 21. 未来 Semantic Search

第三阶段可以加入：

```text
Embedding
Vector Database
Semantic Search
Hybrid Search
AI Dataset Recommendation
```

但 MVP 不建议一开始就加入向量数据库。

推荐先：

```text
SQLite
+
FTS5
+
结构化过滤
+
规则 Ranking
```

等真实搜索日志积累后，再引入 Embedding。

## 22. Dataset Comparison

未来增加：

```text
gee_compare_datasets
```

例如：

> 比较 MOD13Q1、MYD13Q1 和 MOD09GA EVI。

返回：

| 属性 | MOD13Q1 | MYD13Q1 | MOD09GA EVI |
|---|---|---|---|
| 平台 | Terra | Aqua | Terra |
| 分辨率 | 250m | 250m | ~463m |
| 时间频率 | 16-day | 16-day | Daily |
| EVI | ✓ | ✓ | ✓ |
| 时间范围 | 2000- | 2002- | 2000-2023 |

这样用户可以在选择下载数据前进行比较。

## 23. Dataset Preview

未来增加：

```text
gee_preview_dataset
```

可返回：

```text
Dataset metadata
Band list
时间范围
空间范围
缩略图
Boundary overlay
```

但 Preview 属于第二阶段功能，MVP 不必实现。

## 24. Catalog 更新机制

推荐：

```text
Catalog Collector
       ↓
官方 Catalog
       ↓
Metadata Normalizer
       ↓
INSERT / UPDATE
       ↓
SQLite
       ↓
FTS5 rebuild/update
```

每次更新记录：

```text
dataset_id
old_metadata
new_metadata
updated_at
```

未来可支持 changelog。

## 25. 缓存

建议缓存：

```text
Dataset Metadata
Validation Result
Search Result
```

例如：

```text
validation_cache
```

字段：

```text
dataset_id
valid
accessible
type
checked_at
```

Validation 缓存可以设置 TTL，例如：

```text
1 hour
```

具体根据实际运行情况调整。

## 26. 性能策略

对于：

```text
EVI
```

如果 Catalog 有 2000 个候选：

不要：

```text
2000 × GEE API
```

应该：

```text
SQLite Search
 ↓
Top 20
 ↓
Ranking
 ↓
Top 5
 ↓
GEE Validation
```

这样可以显著降低延迟。

## 27. 错误处理

### 无结果

```text
没有找到满足全部条件的数据集。

建议放宽：
- 时间分辨率
- 空间分辨率
- 时间范围
- 数据类型
```

### Dataset 不存在

```text
No dataset found.
```

### Catalog 可能过期

```text
Catalog metadata may be outdated.
Run gee_catalog_update().
```

### GEE 无法访问

```text
Dataset exists in Catalog,
but current Earth Engine account cannot access it.
```

### 数据时间范围不满足

```text
Dataset only partially covers the requested period.
```

## 28. 安全

Catalog Search 本身不需要 OAuth：

```text
Search
 ↓
无需登录
```

Validation / Download：

```text
需要 GEE Auth
```

这样用户可以先查数据，再决定是否登录。

同时：

- 不向 AI 返回 OAuth token。
- 不向 Tool 输出 credentials。
- 不在普通日志中记录敏感认证信息。
- Dataset Validation 使用当前认证账号。
- 下载仅访问用户有权限访问的资源。

## 29. MVP 实现顺序

### Phase 1：Catalog

```text
□ 获取 GEE Catalog
□ 建立 SQLite
□ datasets 表
□ bands 表
□ tags 表
□ FTS5
```

### Phase 2：Search

```text
□ gee_search_datasets
□ Query Normalize
□ Keyword Search
□ Band Search
□ Resolution Filter
□ Date Filter
□ Temporal Filter
```

### Phase 3：Ranking

```text
□ Keyword Score
□ Band Score
□ Resolution Score
□ Temporal Score
□ Date Coverage Score
□ Match Reasons
```

### Phase 4：Validation

```text
□ gee_validate_dataset
□ GEE API
□ Image / ImageCollection 判断
□ Band 验证
```

### Phase 5：AI Workflow

```text
□ Search Prompt
□ Dataset Recommendation
□ User Selection
□ 自动进入 Download
```

## 30. MVP 验收标准

用户输入：

> 找 2017-2021 年 EVI，最好日尺度，分辨率 1km 左右。

系统必须能够：

```text
✓ 理解 EVI
✓ 搜索 GEE Catalog
✓ 找到包含 EVI Band 的数据集
✓ 过滤时间覆盖
✓ 过滤 / 排序时间分辨率
✓ 计算空间分辨率匹配度
✓ 返回 Top N
✓ 显示 Dataset ID
✓ 显示名称
✓ 显示类型
✓ 显示空间分辨率
✓ 显示时间分辨率
✓ 显示可用时间
✓ 显示 Band
✓ 显示 Provider
✓ 显示平台 / 传感器
✓ 显示推荐理由
✓ 用户选择后验证 GEE Asset
✓ 最终进入 gee_download
```

完成这些后，第一版核心闭环成立。

## 31. 推荐项目目录

```text
ai-gee-downloader/
│
├── server.py
├── pyproject.toml
├── README.md
├── config.yaml
│
├── catalog/
│   ├── collector.py
│   ├── normalizer.py
│   ├── database.py
│   ├── search.py
│   ├── ranking.py
│   └── schema.sql
│
├── gee/
│   ├── auth.py
│   ├── dataset.py
│   ├── validator.py
│   ├── boundary.py
│   ├── export.py
│   └── task.py
│
├── prompts/
│   └── search.py
│
├── models/
│   ├── dataset.py
│   ├── search.py
│   └── result.py
│
├── download/
│   ├── direct.py
│   ├── export.py
│   └── manager.py
│
├── data/
│   └── gee_catalog.db
│
└── tests/
    ├── test_catalog.py
    ├── test_search.py
    ├── test_ranking.py
    └── test_validation.py
```

## 32. 最终产品定位

第一阶段：

> GEE Dataset Search

第二阶段：

> AI GEE Dataset Discovery

第三阶段：

> AI GEE Downloader

第四阶段：

> AI Remote Sensing Dataset Builder

最终用户可以只说：

> 我需要 2017-2021 年中国区域的土壤湿度下采样训练数据，9km 辅助数据、1km 高分辨率变量，帮我从 GEE 找数据并下载。

系统自动：

```text
研究需求理解
      ↓
Dataset Discovery
      ↓
Dataset Recommendation
      ↓
Dataset Validation
      ↓
Spatial / Temporal Planning
      ↓
Grid Alignment
      ↓
Download Planning
      ↓
GEE Export
      ↓
Local GeoTIFF
      ↓
Metadata
      ↓
QA
```

最终：

> **AI Remote Sensing Data Acquisition & Dataset Builder**

## 33. 实现时的核心原则

1. **不要依赖 Google 普通搜索作为主要数据源。**
2. **Catalog 与 GEE API 验证必须分离。**
3. **搜索结果必须结构化。**
4. **Band 是强语义字段，不应只当普通关键词。**
5. **时间覆盖必须明确区分完整覆盖、部分覆盖和无覆盖。**
6. **空间分辨率使用可解释的匹配评分。**
7. **先规则搜索，再考虑 Embedding。**
8. **搜索 Top N 后再调用 GEE API 验证，避免大量 API 请求。**
9. **搜索不强制要求登录，验证和下载才要求认证。**
10. **Dataset Discovery 必须能够无缝进入 Downloader。**

## 34. 官方参考

实现时以官方文档为准：

- Google Earth Engine Data Catalog
- Google Earth Engine Python API
- Google Earth Engine Dataset API
- Google Earth Engine Export API
- Model Context Protocol Prompts
- Model Context Protocol Tools
- Model Context Protocol Resources

主要入口：

```text
https://developers.google.com/earth-engine/datasets/catalog

https://developers.google.com/earth-engine/apidocs

https://modelcontextprotocol.io/specification
```

---

## 35. 一句话架构总结

```text
Catalog 负责“发现”
    +
GEE API 负责“验证”
    +
AI 负责“理解与推荐”
    +
Downloader 负责“执行”
    =
AI GEE Dataset Discovery & Downloader
```
