-- InvisibleCrawler Database Schema
-- Phase 2: Includes Redis scheduling, crawl runs, and perceptual hashes

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Images table: stores metadata about discovered images
CREATE TABLE IF NOT EXISTS images (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url TEXT NOT NULL UNIQUE,
    sha256_hash VARCHAR(64) NOT NULL,
    width INTEGER,
    height INTEGER,
    format VARCHAR(10),
    content_type VARCHAR(100),
    file_size_bytes INTEGER,
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    download_success BOOLEAN DEFAULT FALSE,
    
    -- Perceptual hashes (Phase 2)
    phash_hash VARCHAR(16),
    dhash_hash VARCHAR(16),
    
    -- Future InvisibleID fields (reserved, initially empty)
    invisible_id_detected BOOLEAN DEFAULT NULL,
    invisible_id_payload TEXT DEFAULT NULL,
    invisible_id_confidence FLOAT DEFAULT NULL,
    invisible_id_version VARCHAR(20) DEFAULT NULL,
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT NULL
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_images_sha256 ON images(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_images_discovered_at ON images(discovered_at);
CREATE INDEX IF NOT EXISTS idx_images_download_success ON images(download_success);
CREATE INDEX IF NOT EXISTS idx_images_url ON images(url);
CREATE INDEX IF NOT EXISTS idx_images_phash ON images(phash_hash);
CREATE INDEX IF NOT EXISTS idx_images_dhash ON images(dhash_hash);

-- Crawl runs table: tracks high-level crawl runs (Phase 2)
CREATE TABLE IF NOT EXISTS crawl_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    mode VARCHAR(20) NOT NULL,  -- 'discovery' | 'refresh'
    pages_crawled INTEGER DEFAULT 0,
    images_found INTEGER DEFAULT 0,
    images_downloaded INTEGER DEFAULT 0,
    seed_source VARCHAR(255),
    status VARCHAR(20) DEFAULT 'running',  -- 'running' | 'completed' | 'failed'
    error_message TEXT
);

-- Create indexes for crawl runs
CREATE INDEX IF NOT EXISTS idx_crawl_runs_started_at ON crawl_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_mode ON crawl_runs(mode);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_status ON crawl_runs(status);

-- Crawl log table: tracks crawled pages
CREATE TABLE IF NOT EXISTS crawl_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_url TEXT NOT NULL,
    domain VARCHAR(255) NOT NULL,
    crawled_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    status INTEGER,
    images_found INTEGER DEFAULT 0,
    images_downloaded INTEGER DEFAULT 0,
    error_message TEXT,
    crawl_type VARCHAR(20) DEFAULT 'discovery',  -- 'discovery' or 'refresh'
    crawl_run_id UUID REFERENCES crawl_runs(id) ON DELETE SET NULL
);

-- Create indexes for crawl log
CREATE INDEX IF NOT EXISTS idx_crawl_log_domain ON crawl_log(domain);
CREATE INDEX IF NOT EXISTS idx_crawl_log_crawled_at ON crawl_log(crawled_at);
CREATE INDEX IF NOT EXISTS idx_crawl_log_status ON crawl_log(status);
CREATE INDEX IF NOT EXISTS idx_crawl_log_run_id ON crawl_log(crawl_run_id);

-- Provenance table: tracks where images were found
CREATE TABLE IF NOT EXISTS provenance (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    image_id UUID NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    source_page_url TEXT NOT NULL,
    source_domain VARCHAR(255) NOT NULL,
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    discovery_type VARCHAR(20) DEFAULT 'discovery',  -- 'discovery' or 'refresh'
    CONSTRAINT uq_provenance_image_page UNIQUE (image_id, source_page_url)
);

-- Create indexes for provenance
CREATE INDEX IF NOT EXISTS idx_provenance_image_id ON provenance(image_id);
CREATE INDEX IF NOT EXISTS idx_provenance_source_domain ON provenance(source_domain);
