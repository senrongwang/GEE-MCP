# AI GEE 下载器整体设计方案

> 版本：v0.1  
> 定位：AI 原生的 Google Earth Engine（GEE）遥感数据下载与任务管理工具  
> 推荐实现：**本地 MCP Server + Earth Engine Python API + 本地文件系统**

---

## 1. 项目概述

### 1.1 项目目标

构建一个能够被 ChatGPT、Codex、Claude 等 AI Agent 调用的 **AI GEE Downloader**。

用户不需要编写 GEE JavaScript 或 Python 代码，只需要告诉 AI：

- GEE 数据 ID
- 时间范围
- 空间分辨率
- Boundary Asset
- 输出目录
- 坐标系（默认 EPSG:3857）

AI 自动完成：

```text
自然语言需求
    ↓
参数解析
    ↓
GEE 登录 / 状态检查
    ↓
数据集识别
    ↓
Image / ImageCollection 判断
    ↓
时间筛选
    ↓
Boundary Asset 解析
    ↓
空间范围计算
    ↓
CRS / Resolution 参数处理
    ↓
下载策略规划
    ↓
Direct Download 或 Export Task
    ↓
任务监控
    ↓
本地 GeoTIFF
    ↓
文件质量检查
    ↓
向 AI 返回结果与元数据
```

---

## 2. 典型使用场景

用户：

> 下载 MODIS NDVI，2021 年全年，1 km，使用我的安徽边界资产，EPSG:3857，保存到 D:\GEE_Data。

AI 应自动理解为：

```yaml
dataset: MODIS/...
start_date: 2021-01-01
end_date: 2021-12-31
scale: 1000
boundary: projects/.../assets/Anhui
crs: EPSG:3857
output: D:/GEE_Data
format: GeoTIFF
```

然后自动完成整个下载流程。

---

# 3. 为什么选择 MCP

## 3.1 方案比较

| 方案 | AI 调用 | 本地文件 | GEE OAuth | 长任务 | 扩展性 | 推荐 |
|---|---:|---:|---:|---:|---:|---:|
| Python CLI | △ | ✅ | ✅ | △ | ⭐⭐⭐ | ⭐⭐⭐ |
| ChatGPT Plugin | ✅ | △ | △ | △ | ⭐⭐ | ⭐⭐ |
| 独立桌面程序 | △ | ✅ | ✅ | ✅ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| MCP Server | ✅ | ✅ | ✅ | ✅ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| MCP + 本地服务 | ✅ | ✅ | ✅ | ✅ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

### 结论

第一版采用：

```text
AI Client
    ↓
MCP
    ↓
本地 GEE MCP Server
    ↓
Earth Engine Python API
    ↓
本地文件系统
```

不建议第一版做传统 Plugin。

---

# 4. 为什么必须是本地 MCP

本项目有几个特殊需求：

1. GEE OAuth 凭据需要在本机使用。
2. 下载结果最终需要保存到用户本地磁盘。
3. 用户可能使用代理软件访问 GEE。
4. 大型 GEE Export Task 可能运行较长时间。
5. AI 需要查询任务状态。
6. 本地 GIS 数据、Boundary Asset 配置和输出目录需要由本机控制。

因此本地 MCP Server 比纯云端服务更合适。

---

# 5. 总体架构

```text
┌───────────────────────────────────────────────┐
│                AI Client                     │
│        ChatGPT / Codex / Claude              │
└───────────────────────┬───────────────────────┘
                        │ MCP
                        ▼
┌───────────────────────────────────────────────┐
│              GEE MCP Server                  │
│                                               │
│  Dataset Resolver                             │
│  Boundary Resolver                            │
│  Time Resolver                                │
│  CRS / Resolution Resolver                    │
│  Download Planner                             │
│  Task Manager                                 │
│  Metadata Inspector                           │
└───────────────┬────────────────┬──────────────┘
                │                │
                ▼                ▼
       ┌────────────────┐  ┌────────────────┐
       │ Google Earth   │  │ Local File     │
       │ Engine         │  │ System         │
       └────────────────┘  └────────────────┘
                │                │
                ▼                ▼
        GEE Image / Task       GeoTIFF
```

---

# 6. 核心设计原则

## 6.1 AI 负责理解，MCP 负责执行

AI 不应该自己生成大量 GEE Python 代码并直接运行。

推荐：

```text
AI
 ↓
结构化参数
 ↓
MCP Tool
 ↓
业务逻辑
 ↓
GEE API
```

这样可以降低：

- 参数错误
- 代码幻觉
- 不安全的本地操作
- GEE API 使用错误

---

## 6.2 下载策略自动选择

GEE 下载不能永远采用一种方式。

应该设计 Download Planner：

```text
                    Download Planner
                           │
             ┌─────────────┴─────────────┐
             │                           │
          小数据                       大数据
             │                           │
             ▼                           ▼
     Image.getDownloadURL       Export Task
             │                           │
             ▼                           ▼
        直接下载                     异步导出
```

GEE 官方文档说明，`Image.getDownloadURL()` 适合较小的数据块，当前限制包括最大请求约 32 MB、最大 grid dimension 10000；对于大型或长时间运行的导出，应使用 `ee.batch.Export`。 

因此：

> **不要让 AI 自己决定下载方式，而是由 MCP 的 Download Planner 根据任务规模自动决定。**

---

# 7. GEE 认证设计

## 7.1 首次登录

调用：

```text
gee_login()
```

流程：

```text
AI
 ↓
gee_login
 ↓
检查本地凭据
 ↓
不存在
 ↓
打开 Google OAuth
 ↓
用户登录
 ↓
授权 Earth Engine
 ↓
保存 credentials
```

GEE Python API 的 `ee.Authenticate()` 支持 OAuth，并将获得的凭据持久化到本地，之后可由 `ee.Initialize()` 使用。

---

## 7.2 后续调用

```text
gee_login()
      ↓
凭据存在？
      │
  ┌───┴───┐
  │       │
 是       否
  │       │
  ▼       ▼
Initialize OAuth
```

---

# 8. MCP Tool 设计

第一版建议提供以下工具。

## 8.1 gee_login

用途：

- 登录 GEE
- 检查认证状态
- 初始化 Earth Engine

输入：

```json
{
  "force": false
}
```

输出：

```json
{
  "authenticated": true,
  "project": "my-project"
}
```

---

## 8.2 gee_dataset_info

用途：

获取数据集信息。

输入：

```json
{
  "dataset_id": "MODIS/061/MOD13Q1"
}
```

输出：

```json
{
  "id": "MODIS/061/MOD13Q1",
  "type": "ImageCollection",
  "bands": [],
  "crs": "...",
  "scale": 250,
  "time_start": "...",
  "time_end": "..."
}
```

---

## 8.3 gee_boundary_info

用途：

检查 Boundary Asset。

输入：

```json
{
  "asset_id": "projects/xxx/assets/Anhui"
}
```

输出：

```json
{
  "asset_id": "...",
  "type": "FeatureCollection",
  "feature_count": 17,
  "bounds": []
}
```

---

## 8.4 gee_download

核心工具。

输入：

```json
{
  "dataset": "MODIS/061/MOD13Q1",
  "start_date": "2021-01-01",
  "end_date": "2021-12-31",
  "boundary": "projects/xxx/assets/Anhui",
  "scale": 1000,
  "crs": "EPSG:3857",
  "output": "D:/GEE_Data/MODIS",
  "format": "GeoTIFF",
  "dry_run": false
}
```

---

## 8.5 gee_task_status

输入：

```json
{
  "task_id": "xxxx"
}
```

输出：

```json
{
  "state": "RUNNING",
  "progress": null,
  "description": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

---

## 8.6 gee_list_tasks

用途：

查看当前用户的 GEE Export Tasks。

输出：

```json
[
  {
    "id": "...",
    "state": "COMPLETED",
    "description": "MODIS_NDVI_2021"
  },
  {
    "id": "...",
    "state": "RUNNING",
    "description": "GLDAS_SM_2021"
  }
]
```

---

# 9. Dataset Resolver

用户提供的 Dataset ID 可能是：

```text
Image
ImageCollection
FeatureCollection
```

因此必须首先判断类型。

```text
dataset_id
    │
    ▼
GEE Asset Inspection
    │
    ├── Image
    │
    ├── ImageCollection
    │
    └── FeatureCollection
```

---

# 10. Image 处理

如果输入：

```text
LANDSAT/...
```

是单个 Image：

```text
Image
 ↓
检查时间
 ↓
检查空间覆盖
 ↓
检查 bands
 ↓
Export / Direct Download
```

---

# 11. ImageCollection 处理

如果输入：

```text
MODIS/061/MOD13Q1
```

是 ImageCollection：

```text
ImageCollection
      ↓
filterDate()
      ↓
filterBounds()
      ↓
排序 / 筛选
      ↓
获取 Image
      ↓
Export
```

AI 不应该把 ImageCollection 错误地当成单个 Image。

---

# 12. 时间处理

基本参数：

```yaml
start_date: 2021-01-01
end_date: 2021-12-31
```

支持：

```text
native
daily
monthly
annual
mean
median
mosaic
first
best
```

例如：

> 下载 2021 年 MODIS NDVI 月平均。

执行：

```text
ImageCollection
 ↓
filterDate
 ↓
按月分组
 ↓
monthly mean
 ↓
12 Images
 ↓
Export
```

---

# 13. Boundary Resolver

支持：

```text
projects/xxx/assets/Anhui
users/xxx/Anhui
```

流程：

```text
Boundary Asset
      ↓
FeatureCollection
      ↓
geometry()
      ↓
bounds
      ↓
region
```

默认建议：

```python
region = boundary.geometry()
```

而不是默认对 Image 做 `clip()`。

只有用户明确要求裁剪后的像元掩膜时才执行：

```python
image.clip(region)
```

---

# 14. 空间分辨率

默认：

```yaml
scale: 1000
```

表示：

```text
1000 m
```

用户也可以输入：

```text
250m
500m
1km
9km
30m
```

AI 应转换为：

```text
250
500
1000
9000
30
```

---

# 15. CRS

默认：

```yaml
crs: EPSG:3857
```

用户可以指定：

```text
EPSG:4326
EPSG:3857
EPSG:32649
EPSG:32650
...
```

内部不要把 CRS 和 scale 混为一个参数。

推荐：

```json
{
  "crs": "EPSG:3857",
  "scale": 1000
}
```

---

# 16. Grid Alignment

为了支持遥感研究，未来需要支持：

```yaml
grid_mode:
  native
  target_crs
  aligned
```

### native

保持数据原始投影与网格。

### target_crs

使用：

```text
CRS + scale
```

重新构建输出网格。

### aligned

使用：

```text
CRS
crsTransform
origin
pixel size
```

使不同数据严格对齐。

这一功能对于：

```text
MODIS 1km
SMAP 9km
GLDAS 9km
ERA5-Land
DEM
```

等多源遥感数据尤其重要。

---

# 17. Download Planner

下载策略由系统自动决定。

```text
                 Request
                    │
                    ▼
             Estimate Size
                    │
          ┌─────────┴─────────┐
          │                   │
       Small                 Large
          │                   │
          ▼                   ▼
 getDownloadURL          Export Task
          │                   │
          ▼                   ▼
       Download           Task Monitor
          │                   │
          └─────────┬─────────┘
                    ▼
               Local File
```

---

# 18. Direct Download

适合：

- 小区域
- 单景 Image
- 小数据量
- 临时下载

流程：

```text
ee.Image
 ↓
getDownloadURL()
 ↓
requests
 ↓
GeoTIFF
```

GEE 官方 API 当前将 `Image.getDownloadURL()` 定位为小块 Image 数据下载接口，并限制请求大小和 grid dimension。

---

# 19. Export Task

适合：

- 大区域
- 多时相数据
- 大分辨率
- 长时间计算
- 大文件

典型：

```python
task = ee.batch.Export.image.toDrive(
    image=image,
    description=description,
    scale=1000,
    region=region,
    crs="EPSG:3857",
    fileFormat="GeoTIFF",
    maxPixels=1e13
)

task.start()
```

然后：

```text
READY
 ↓
RUNNING
 ↓
COMPLETED
```

GEE 官方 Python 教程也明确给出了 Export Task + `task.status()` 的工作流，并建议大型或长时间运行导出使用 Export。

---

# 20. 本地下载策略

MVP 可以采用：

```text
GEE Export
 ↓
Google Drive
 ↓
本地 Downloader
 ↓
Local GeoTIFF
```

未来支持：

```text
GEE
 ├── Google Drive
 ├── Cloud Storage
 └── Direct Download
```

再由 Download Engine 统一写入：

```text
D:/GEE_Data/
```

---

# 21. dry_run

这是 MVP 必须实现的功能。

用户：

> 下载 2017-2021 GLDAS 土壤湿度。

系统先：

```json
{
  "dry_run": true
}
```

返回：

```text
Dataset:
GLDAS/...

Type:
ImageCollection

Images:
1826

Region:
User Boundary

Resolution:
9000 m

CRS:
EPSG:3857

Estimated output:
XX GB

Recommended strategy:
Annual multi-band GeoTIFF

Estimated tasks:
5
```

然后 AI 再询问用户是否执行。

---

# 22. 任务规划

对于长期数据：

```text
2017-2021
```

系统不要机械地创建 1826 个任务。

应该分析：

```text
1826 days
```

然后提供：

```text
A. 每天一个 GeoTIFF
B. 每月一个 GeoTIFF
C. 每年一个多波段 GeoTIFF
D. 五年一个多波段 GeoTIFF
```

默认选择应该根据：

- 数据量
- ImageCollection 时间间隔
- GEE Task 数量
- 单文件大小
- 用户目标

自动规划。

---

# 23. 文件组织

推荐：

```text
D:/GEE_Data/
│
├── MODIS/
│   └── MOD13Q1/
│       └── 2021/
│           ├── 2021-01-01.tif
│           ├── 2021-01-17.tif
│           └── ...
│
├── GLDAS/
│   └── SoilMoisture/
│       ├── 2017/
│       ├── 2018/
│       ├── 2019/
│       ├── 2020/
│       └── 2021/
│
└── metadata/
```

---

# 24. Metadata

每次下载完成后生成：

```text
metadata.json
```

例如：

```json
{
  "dataset": "MODIS/061/MOD13Q1",
  "start_date": "2021-01-01",
  "end_date": "2021-12-31",
  "boundary": "projects/xxx/assets/Anhui",
  "crs": "EPSG:3857",
  "scale": 1000,
  "format": "GeoTIFF",
  "bands": [
    "NDVI",
    "EVI"
  ],
  "download_time": "...",
  "files": []
}
```

---

# 25. GeoTIFF QA

下载完成后自动检查：

```text
文件存在
 ↓
打开 raster
 ↓
检查 CRS
 ↓
检查 resolution
 ↓
检查 width / height
 ↓
检查 band
 ↓
检查 dtype
 ↓
检查 NoData
 ↓
检查 transform
 ↓
检查 bounds
```

输出：

```text
✓ File exists
✓ CRS = EPSG:3857
✓ Resolution = 1000 × 1000 m
✓ Bands = 2
✓ NoData valid
✓ Raster readable
```

如果失败：

```text
✗ CRS mismatch
```

则 AI 应明确告诉用户。

---

# 26. 项目目录结构

```text
ai-gee-downloader/
│
├── server.py
├── pyproject.toml
├── README.md
├── config.yaml
│
├── gee/
│   ├── __init__.py
│   ├── auth.py
│   ├── dataset.py
│   ├── collection.py
│   ├── boundary.py
│   ├── export.py
│   └── task.py
│
├── planner/
│   ├── __init__.py
│   ├── download_planner.py
│   ├── size_estimator.py
│   └── temporal_planner.py
│
├── download/
│   ├── __init__.py
│   ├── direct.py
│   ├── export.py
│   ├── drive.py
│   └── manager.py
│
├── raster/
│   ├── __init__.py
│   ├── inspect.py
│   ├── validate.py
│   └── metadata.py
│
├── models/
│   ├── request.py
│   ├── task.py
│   └── result.py
│
├── utils/
│   ├── paths.py
│   ├── logging.py
│   └── dates.py
│
└── tests/
    ├── test_auth.py
    ├── test_dataset.py
    ├── test_boundary.py
    ├── test_planner.py
    └── test_download.py
```

---

# 27. 技术栈

## 核心

```text
Python 3.11+
```

## GEE

```text
earthengine-api
```

## MCP

```text
MCP Python SDK
```

## GIS

```text
rasterio
geopandas
shapely
pyproj
```

## HTTP

```text
requests
httpx
```

## 可选

```text
geemap
```

`geemap` 作为辅助 GIS / Earth Engine 工具使用，不作为核心架构依赖。

---

# 28. MCP Tasks

大型 GEE Export 天然属于异步任务。

未来可以将：

```text
gee_download()
```

设计成：

```text
MCP Task
```

流程：

```text
tools/call
    ↓
Create Task
    ↓
task_id
    ↓
RUNNING
    ↓
poll
    ↓
COMPLETED
```

MCP 当前的 Tasks 扩展正是为“昂贵计算、批处理以及外部 Job API”这类异步工作设计的，支持 `tasks/get`、`tasks/update` 和 `tasks/cancel`。

因此长期运行的 GEE Export 与 MCP Tasks 非常契合。

---

# 29. 推荐的状态机

```text
PENDING
   ↓
VALIDATING
   ↓
PLANNING
   ↓
SUBMITTING
   ↓
RUNNING
   ↓
DOWNLOADING
   ↓
VALIDATING_OUTPUT
   ↓
COMPLETED
```

失败：

```text
ANY STATE
   ↓
FAILED
```

取消：

```text
RUNNING
   ↓
CANCELLED
```

---

# 30. 错误处理

必须区分：

### 认证错误

```text
GEE authentication failed
```

### Asset 不存在

```text
Boundary Asset not found
```

### 数据集不存在

```text
Dataset not found
```

### 时间范围无数据

```text
No images found for requested time range
```

### 权限问题

```text
Permission denied
```

### Export 失败

```text
Earth Engine export failed
```

### 本地磁盘问题

```text
Insufficient disk space
```

### 网络问题

```text
Download timeout
```

---

# 31. AI 对错误的处理

不要只返回：

```text
Error
```

应该返回：

```text
问题：
Boundary Asset 无法访问。

原因：
当前 GEE 账号没有读取该 Asset 的权限。

建议：
1. 检查 Asset 是否属于当前账号。
2. 检查 Asset sharing。
3. 使用 gee_boundary_info 重新检查。
```

---

# 32. 典型 AI 交互

## 示例 1：简单下载

用户：

> 下载 MODIS NDVI，2021 年全年，安徽，1km。

AI：

```text
我找到 MODIS/061/MOD13Q1。

时间：
2021-01-01 ~ 2021-12-31

分辨率：
1000 m

CRS：
EPSG:3857

Boundary：
安徽资产

预计：
约 23 个时间片

我将使用 GEE Export Task 下载。
```

---

## 示例 2：dry-run

用户：

> 下载 2017-2021 GLDAS 土壤湿度。

AI：

```text
任务规模较大。

预计：
1826 个日尺度影像。

建议：
按年份输出 5 个多波段 GeoTIFF。

预计任务：
5

是否开始？
```

---

## 示例 3：空间对齐

用户：

> 下载 MODIS 1km NDVI，和我之前的 1km 数据保持完全一致的网格。

AI：

```text
检测到需要 Grid Alignment。

我将使用：
CRS
Affine Transform
Pixel Origin
Pixel Size

确保输出 GeoTIFF 与目标网格逐像元对齐。
```

---

# 33. MVP 范围

第一阶段不要做太多功能。

必须实现：

```text
✓ GEE OAuth
✓ Image
✓ ImageCollection
✓ Date Filter
✓ Boundary Asset
✓ Scale
✓ CRS
✓ GeoTIFF
✓ Direct Download
✓ Export Task
✓ Task Status
✓ Local Output
✓ dry_run
✓ Metadata
✓ Raster QA
```

---

# 34. 第二阶段

加入：

```text
□ Monthly / Annual aggregation
□ Multi-band output
□ Automatic task batching
□ Google Drive download
□ Cloud Storage
□ Resume download
□ Retry
□ Download cache
□ Dataset catalog search
□ Boundary preview
□ Image preview
```

---

# 35. 第三阶段：AI 遥感数据 Agent

最终目标：

用户不再只是：

> 下载数据。

而是：

> 我要做 2017-2021 年土壤湿度下采样，帮我准备 GLDAS、MODIS NDVI、LST、DEM，9km 数据作为输入，1km 数据作为目标，全部使用同一个空间网格。

AI 自动：

```text
分析研究需求
      ↓
寻找 GEE Dataset
      ↓
确认时间范围
      ↓
确认空间范围
      ↓
确定 CRS
      ↓
确定目标网格
      ↓
确定时间尺度
      ↓
规划 Export
      ↓
执行下载
      ↓
检查 GeoTIFF
      ↓
生成 metadata
      ↓
生成 dataset manifest
```

最终成为：

> **AI Remote Sensing Data Acquisition Agent**

而不仅仅是一个 GEE Downloader。

---

# 36. Dataset Manifest

未来生成：

```yaml
project:
  name: soil_moisture_downscaling

spatial:
  crs: EPSG:3857
  resolution: 1000
  grid: aligned

temporal:
  start: 2017-01-01
  end: 2021-12-31
  frequency: daily

datasets:

  - name: GLDAS_SM
    gee_id: ...
    native_resolution: 10000

  - name: MODIS_NDVI
    gee_id: ...
    native_resolution: 250

  - name: MODIS_LST
    gee_id: ...
    native_resolution: 1000
```

这样下载完成后，可以直接作为后续机器学习 / 深度学习数据预处理的输入。

---

# 37. 安全设计

本地 MCP 必须限制 AI 能访问的本地路径。

推荐配置：

```yaml
filesystem:
  allowed_roots:
    - D:/GEE_Data
    - D:/RemoteSensing
```

AI 不应该默认拥有：

```text
C:/
C:/Users/
系统目录
```

等全部文件系统权限。

同样：

- 不把 OAuth token 返回给 AI。
- 不把 Google credentials 放入 MCP Tool 输出。
- 不把 GEE 私密信息写入日志。
- Export Task 只能操作当前认证账号允许的资源。

---

# 38. 配置文件

建议：

```yaml
gee:
  project: your-earth-engine-project

download:
  default_crs: EPSG:3857
  default_format: GeoTIFF
  default_max_pixels: 10000000000000

filesystem:
  default_output: D:/GEE_Data
  allowed_roots:
    - D:/GEE_Data

planner:
  direct_download_max_mb: 32
  max_grid_dimension: 10000

network:
  timeout: 300
  retry: 3
```

实际的 Direct Download 阈值应保守设置，而不是刚好卡在 GEE API 上限。

---

# 39. 重要设计决策

## 决策 1

**MCP Server，而不是传统 Plugin。**

原因：

- AI Agent 原生调用
- 本地文件系统
- 本地 OAuth
- 长任务
- 易扩展

## 决策 2

**Earth Engine Python API 为核心。**

不要把 geemap 当成核心抽象层。

## 决策 3

**Direct Download + Export Task 双引擎。**

不要只使用 `getDownloadURL()`。

## 决策 4

**默认 EPSG:3857。**

但 CRS 与 scale 独立管理。

## 决策 5

**未来支持 Grid Alignment。**

这是遥感科研场景非常重要的能力。

## 决策 6

**dry_run 必须进入 MVP。**

防止 AI 直接创建大量 GEE Export Task。

---

# 40. 最终产品形态

```text
                  ┌─────────────────────┐
                  │     ChatGPT /       │
                  │       Codex         │
                  └──────────┬──────────┘
                             │
                            MCP
                             │
                  ┌──────────▼──────────┐
                  │  AI GEE Downloader  │
                  │                     │
                  │ Dataset Resolver    │
                  │ Boundary Resolver   │
                  │ Time Resolver       │
                  │ Grid Resolver       │
                  │ Download Planner    │
                  │ Task Manager        │
                  │ QA Engine           │
                  └───────┬───────┬─────┘
                          │       │
                    Earth Engine  │
                          │       │
                          ▼       ▼
                       GEE API  Local FS
                          │       │
                          ▼       ▼
                     Export    GeoTIFF
                          │       │
                          └───┬───┘
                              ▼
                       Metadata / QA
                              │
                              ▼
                              AI
```

---

# 41. 最终目标

第一阶段：

> **AI 可以帮用户下载 GEE 数据。**

第二阶段：

> **AI 可以帮用户规划 GEE 数据下载任务。**

第三阶段：

> **AI 可以理解遥感研究需求，并自动构建多源数据集。**

最终：

> **AI GEE Downloader → AI Remote Sensing Data Acquisition Agent**

这个方向不仅适用于 MODIS、Landsat、Sentinel、GLDAS、ERA5-Land、SMAP 等 GEE 数据，也可以成为后续遥感数据预处理、空间对齐、时序构建和深度学习数据集生成的基础设施。

---

# 42. 参考资料

- Google Earth Engine Python API / API Reference
- Google Earth Engine Python API 教程
- Google Earth Engine `ee.Authenticate`
- Google Earth Engine `Image.getDownloadURL`
- Google Earth Engine Export API
- Model Context Protocol 2026-07-28 Specification
- MCP Tasks Extension

核心参考：

- Earth Engine Python API：Google Developers
- Earth Engine Export：Google Developers
- MCP Specification：Model Context Protocol
