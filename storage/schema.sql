-- InvisibleCrawler Database Schema
-- Phase 1: Minimal viable schema

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
    crawl_type VARCHAR(20) DEFAULT 'discovery'  -- 'discovery' or 'refresh'
);

-- Create indexes for crawl log
CREATE INDEX IF NOT EXISTS idx_crawl_log_domain ON crawl_log(domain);
CREATE INDEX IF NOT EXISTS idx_crawl_log_crawled_at ON crawl_log(crawled_at);
CREATE INDEX IF NOT EXISTS idx_crawl_log_status ON crawl_log(status);

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
