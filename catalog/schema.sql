-- AI GEE Downloader —— GEE Dataset Catalog 数据库 Schema
-- 对应设计文档《GEE Dataset Discovery》第 6 节

-- 数据集主表
CREATE TABLE IF NOT EXISTS datasets (
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
    cadence_days INTEGER,
    temporal_resolution TEXT,

    spatial_resolution REAL,
    spatial_resolution_unit TEXT,

    native_crs TEXT,
    coverage TEXT,
    bbox TEXT,             -- JSON: [minx, miny, maxx, maxy]

    catalog_url TEXT,
    gee_snippet TEXT,

    updated_at TEXT
);

-- 波段表
CREATE TABLE IF NOT EXISTS bands (
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

-- 标签表
CREATE TABLE IF NOT EXISTS tags (
    dataset_id TEXT,
    tag TEXT,

    PRIMARY KEY(dataset_id, tag),

    FOREIGN KEY(dataset_id)
        REFERENCES datasets(id)
);

-- 全文检索（FTS5）
CREATE VIRTUAL TABLE IF NOT EXISTS dataset_fts
USING fts5(
    dataset_id UNINDEXED,
    name,
    description,
    tags,
    bands
);

-- 目录元信息（更新时间 / 数据源等）
CREATE TABLE IF NOT EXISTS catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 数据集验证缓存（设计文档第 25 节，TTL 可配）
CREATE TABLE IF NOT EXISTS validation_cache (
    dataset_id TEXT PRIMARY KEY,
    valid INTEGER,
    accessible INTEGER,
    type TEXT,
    bands TEXT,        -- JSON 数组
    error TEXT,
    checked_at TEXT
);

-- 常用过滤索引
CREATE INDEX IF NOT EXISTS idx_datasets_type ON datasets(type);
CREATE INDEX IF NOT EXISTS idx_datasets_platform ON datasets(platform);
CREATE INDEX IF NOT EXISTS idx_datasets_sensor ON datasets(sensor);
CREATE INDEX IF NOT EXISTS idx_datasets_provider ON datasets(provider);
CREATE INDEX IF NOT EXISTS idx_datasets_start_date ON datasets(start_date);
CREATE INDEX IF NOT EXISTS idx_datasets_end_date ON datasets(end_date);
CREATE INDEX IF NOT EXISTS idx_bands_dataset_id ON bands(dataset_id);
