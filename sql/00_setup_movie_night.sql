-- ============================================================
-- AI Movie Night Planner - Complete SQL Setup
-- ============================================================
-- Run this entire file in your Lakebase Postgres database
-- before deploying the app or running notebooks.
-- ============================================================

-- ============================================================
-- Part 1: Enable pgvector extension
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Part 2: Users
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- Part 3: Groups (Movie Night Groups)
-- ============================================================
CREATE TABLE IF NOT EXISTS groups (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_groups_created_by ON groups (created_by);

-- ============================================================
-- Part 4: Group Members
-- ============================================================
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, email)
);

CREATE INDEX IF NOT EXISTS idx_group_members_email ON group_members (email);

-- ============================================================
-- Part 5: Movies (from TMDB API)
-- ============================================================
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY,
    tmdb_id INTEGER UNIQUE NOT NULL,
    title TEXT NOT NULL,
    overview TEXT,
    tagline TEXT,
    release_date DATE,
    runtime INTEGER,
    vote_average FLOAT,
    vote_count INTEGER,
    popularity FLOAT,
    poster_path TEXT,
    backdrop_path TEXT,
    imdb_id TEXT,
    original_language TEXT,
    genres JSONB,
    cast JSONB,
    keywords JSONB,
    providers JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_movies_title ON movies (title);
CREATE INDEX IF NOT EXISTS idx_movies_vote_average ON movies (vote_average DESC);

-- ============================================================
-- Part 6: Movie Embeddings (pgvector for semantic search)
-- ============================================================
CREATE TABLE IF NOT EXISTS movie_embeddings (
    movie_id INTEGER PRIMARY KEY REFERENCES movies(id) ON DELETE CASCADE,
    embedding VECTOR(384) NOT NULL,
    model_name TEXT NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_movie_embeddings_vector 
    ON movie_embeddings USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- Part 7: Ratings (1-10 scale)
-- ============================================================
CREATE TABLE IF NOT EXISTS ratings (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 10),
    review TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(email, movie_id)
);

CREATE INDEX IF NOT EXISTS idx_ratings_email ON ratings (email);
CREATE INDEX IF NOT EXISTS idx_ratings_movie_id ON ratings (movie_id);

-- ============================================================
-- Part 8: Watchlist Items
-- ============================================================
CREATE TABLE IF NOT EXISTS watchlist_items (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    added_by TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT DEFAULT 'pending',
    watched_at TIMESTAMPTZ,
    UNIQUE(group_id, movie_id)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_group_id ON watchlist_items (group_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist_items (status);

-- ============================================================
-- Part 9: AI Recommendations
-- ============================================================
CREATE TABLE IF NOT EXISTS recommendations (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    suggested_by TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    reasoning TEXT,
    score FLOAT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_group_id ON recommendations (group_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_score ON recommendations (score DESC);

-- ============================================================
-- Part 10: Verification
-- ============================================================
SELECT 
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name IN ('users', 'groups', 'group_members', 'movies', 
                     'movie_embeddings', 'ratings', 'watchlist_items', 'recommendations')
ORDER BY table_name, ordinal_position;