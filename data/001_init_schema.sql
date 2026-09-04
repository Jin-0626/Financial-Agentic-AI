CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS bursa_announcements (
    id BIGSERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL,
    company_name VARCHAR(150) NOT NULL,
    fiscal_quarter VARCHAR(20) NOT NULL,       
    quarter_ended DATE NOT NULL,
    section_category VARCHAR(50) NOT NULL,    
    content_chunk TEXT NOT NULL,
    embedding vector(768),                    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bursa_lookup ON bursa_announcements(stock_code, quarter_ended);
CREATE INDEX IF NOT EXISTS idx_bursa_chunk_embedding ON bursa_announcements 
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);