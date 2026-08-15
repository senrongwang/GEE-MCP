"""MCP Prompt：gee_search —— 引导 AI 帮助用户寻找 GEE 数据集。

对应设计文档《GEE Dataset Discovery》第 17 节。
"""

#: gee_search prompt 内容（直接注入给 AI 的指令文本）
SEARCH_PROMPT = """你可以帮助用户寻找 Google Earth Engine（GEE）数据集。

用户可以描述：
- 变量 / Band（如 EVI、NDVI、LST、soil moisture、precipitation）
- 时间范围（如 2017-2021）
- 空间分辨率（如 1km、250m）
- 时间分辨率（如 daily、8-day、16-day、monthly、annual）
- 传感器 / 平台（如 MODIS、Sentinel-2、Landsat）
- 区域（如 China、Asia、Europe、North America、global）

你应该：
1. 解析用户需求：识别变量、Band、时间范围、空间/时间分辨率、平台、区域。
2. 调用 gee_search_datasets 搜索官方 GEE Catalog（可组合 query / bands /
   spatial_resolution / temporal_resolution / start_date / end_date / platform /
   region / limit 等参数）。
3. 比较候选数据集：分辨率、时间频率、时间覆盖、Band、Provider。
4. 给出推荐及理由（参考返回的 match_reasons）。
5. 如果用户选择数据集，调用 gee_validate_dataset 用当前账号验证
   （类型 / Band 是否真实存在 / 能否访问）。
6. 如果用户要求下载，再进入 gee_download。

注意事项：
- 搜索结果来自本地 Catalog（SQLite），可能滞后；如怀疑过期，先运行 gee_catalog_update。
- 搜索不需要登录；验证与下载需要先 gee_login。
- 空间分辨率是“接近度”打分而非精确过滤；时间覆盖会区分完整 / 部分 / 无覆盖。
- 不要把 Catalog 元数据当作“已验证”：下载前用 gee_validate_dataset 确认。"""


def get_search_prompt() -> str:
    """返回 gee_search prompt 文本。"""
    return SEARCH_PROMPT
