"""
AI Movie Night Planner - MCP Server

Exposes tools for:
1. Semantic movie search (embedding-based)
2. Group management
3. Watchlist management
4. Ratings
5. AI-powered recommendations
"""

import os
import logging
import json
from datetime import datetime
from contextvars import ContextVar

from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from sentence_transformers import SentenceTransformer

import tmdb_broker
import lakebase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("movie-night-mcp")

# Table names
USERS_TABLE = os.environ.get("USERS_TABLE", "users")
GROUPS_TABLE = os.environ.get("GROUPS_TABLE", "groups")
GROUP_MEMBERS_TABLE = os.environ.get("GROUP_MEMBERS_TABLE", "group_members")
MOVIES_TABLE = os.environ.get("MOVIES_TABLE", "movies")
MOVIE_EMBEDDINGS_TABLE = os.environ.get("MOVIE_EMBEDDINGS_TABLE", "movie_embeddings")
RATINGS_TABLE = os.environ.get("RATINGS_TABLE", "ratings")
WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist_items")
RECOMMENDATIONS_TABLE = os.environ.get("RECOMMENDATIONS_TABLE", "recommendations")

# Embedding model
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

_request_context: ContextVar[dict] = ContextVar('request_context', default={})
_embedding_model = None

mcp = FastMCP("movie-night")


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    return _embedding_model


def _get_user_email() -> str:
    """Get the current user's email from request headers."""
    headers = _request_context.get()
    forwarded = headers.get('x-forwarded-user')
    if forwarded:
        return forwarded
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    return w.current_user.me().user_name or 'user@example.com'


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        headers = {
            'x-forwarded-user': request.headers.get('x-forwarded-user'),
            'x-forwarded-email': request.headers.get('x-forwarded-email'),
        }
        _request_context.set(headers)
        return await call_next(request)


def ensure_tables():
    """Create tables if they don't exist."""
    # Users
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
            email TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    
    # Groups
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {GROUPS_TABLE} (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES {USERS_TABLE}(email),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    
    # Group members
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {GROUP_MEMBERS_TABLE} (
            group_id INTEGER NOT NULL REFERENCES {GROUPS_TABLE}(id) ON DELETE CASCADE,
            email TEXT NOT NULL REFERENCES {USERS_TABLE}(email) ON DELETE CASCADE,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (group_id, email)
        )
    """)
    
    # Movies
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {MOVIES_TABLE} (
            id INTEGER PRIMARY KEY, tmdb_id INTEGER UNIQUE NOT NULL, title TEXT NOT NULL,
            overview TEXT, tagline TEXT, release_date DATE, runtime INTEGER,
            vote_average FLOAT, vote_count INTEGER, popularity FLOAT,
            poster_path TEXT, backdrop_path TEXT, imdb_id TEXT, original_language TEXT,
            genres JSONB, cast JSONB, keywords JSONB, providers JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    
    # Movie embeddings
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {MOVIE_EMBEDDINGS_TABLE} (
            movie_id INTEGER PRIMARY KEY REFERENCES {MOVIES_TABLE}(id) ON DELETE CASCADE,
            embedding VECTOR(384) NOT NULL, model_name TEXT NOT NULL, embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    
    # Ratings
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {RATINGS_TABLE} (
            id SERIAL PRIMARY KEY, email TEXT NOT NULL REFERENCES {USERS_TABLE}(email) ON DELETE CASCADE,
            movie_id INTEGER NOT NULL REFERENCES {MOVIES_TABLE}(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 10), review TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(email, movie_id)
        )
    """)
    
    # Watchlist
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {WATCHLIST_TABLE} (
            id SERIAL PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES {GROUPS_TABLE}(id) ON DELETE CASCADE,
            movie_id INTEGER NOT NULL REFERENCES {MOVIES_TABLE}(id) ON DELETE CASCADE,
            added_by TEXT NOT NULL REFERENCES {USERS_TABLE}(email) ON DELETE CASCADE,
            added_at TIMESTAMPTZ NOT NULL DEFAULT now(), status TEXT DEFAULT 'pending',
            watched_at TIMESTAMPTZ, UNIQUE(group_id, movie_id)
        )
    """)
    
    # Recommendations
    lakebase.run_write(f"""
        CREATE TABLE IF NOT EXISTS {RECOMMENDATIONS_TABLE} (
            id SERIAL PRIMARY KEY, group_id INTEGER NOT NULL REFERENCES {GROUPS_TABLE}(id) ON DELETE CASCADE,
            movie_id INTEGER NOT NULL REFERENCES {MOVIES_TABLE}(id) ON DELETE CASCADE,
            suggested_by TEXT NOT NULL REFERENCES {USERS_TABLE}(email) ON DELETE CASCADE,
            reasoning TEXT, score FLOAT, status TEXT DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def _get_or_create_user(email: str) -> dict:
    """Get or create a user."""
    ensure_tables()
    users = lakebase.run_query(f"SELECT * FROM {USERS_TABLE} WHERE email = %s", (email,))
    if users:
        return users[0]
    display_name = email.split('@')[0]
    lakebase.run_write(f"INSERT INTO {USERS_TABLE} (email, display_name) VALUES (%s, %s)", (email, display_name))
    return {"email": email, "display_name": display_name}


def _get_or_create_movie(tmdb_id: int) -> dict:
    """Get movie from DB or fetch from TMDB."""
    movies = lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE tmdb_id = %s", (tmdb_id,))
    if movies:
        return movies[0]
    
    data = tmdb_broker.get_movie_details(tmdb_id)
    
    lakebase.run_write(f"""
        INSERT INTO {MOVIES_TABLE} (
            id, tmdb_id, title, overview, tagline, release_date, runtime,
            vote_average, vote_count, popularity, poster_path, backdrop_path,
            imdb_id, original_language, genres, cast, keywords, providers
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tmdb_id) DO UPDATE SET title = EXCLUDED.title, updated_at = now()
    """, (
        data["id"], data["tmdb_id"], data["title"], data["overview"], data["tagline"],
        data["release_date"], data["runtime"], data["vote_average"], data["vote_count"],
        data["popularity"], data["poster_path"], data["backdrop_path"], data["imdb_id"],
        data["original_language"], json.dumps(data["genres"]), json.dumps(data["cast"]),
        json.dumps(data["keywords"]), json.dumps(data["providers"])
    ))
    
    return lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE tmdb_id = %s", (tmdb_id,))[0]


def _compute_embedding(movie_id: int):
    """Compute and store embedding for a movie."""
    movie = lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE id = %s", (movie_id,))[0]
    
    genres = json.loads(movie["genres"]) if isinstance(movie["genres"], str) else movie["genres"]
    cast = json.loads(movie["cast"]) if isinstance(movie["cast"], str) else movie["cast"]
    keywords = json.loads(movie["keywords"]) if isinstance(movie["keywords"], str) else movie["keywords"]
    
    text_parts = [
        f"Title: {movie['title']}",
        f"Overview: {movie['overview']}",
        f"Genres: {', '.join([g.get('name', '') for g in genres])}",
        f"Cast: {', '.join([c.get('name', '') for c in cast[:5]])}",
        f"Keywords: {', '.join(keywords[:10])}",
    ]
    text = " ".join(text_parts)
    
    model = get_embedding_model()
    embedding = model.encode(text).tolist()
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
    
    lakebase.run_write(f"""
        INSERT INTO {MOVIE_EMBEDDINGS_TABLE} (movie_id, embedding, model_name)
        VALUES (%s, %s::vector, %s)
        ON CONFLICT (movie_id) DO UPDATE SET embedding = EXCLUDED.embedding, model_name = EXCLUDED.model_name
    """, (movie_id, vector_str, EMBEDDING_MODEL))


# ============================================================
# MCP TOOLS
# ============================================================

@mcp.tool
def search_movies(query: str, limit: int = 10) -> dict:
    """
    Search for movies using semantic understanding.
    Example: "a funny sci-fi movie that isn't too violent"
    """
    ensure_tables()
    
    # Get initial results from TMDB
    tmdb_results = tmdb_broker.search_movies(query, limit=limit * 2)
    
    # Ensure movies are in DB with embeddings
    for r in tmdb_results:
        movie = _get_or_create_movie(r["id"])
        emb_count = lakebase.run_query(f"SELECT COUNT(*) as count FROM {MOVIE_EMBEDDINGS_TABLE} WHERE movie_id = %s", (movie["id"],))
        if not emb_count or emb_count[0]["count"] == 0:
            _compute_embedding(movie["id"])
    
    # Semantic search
    model = get_embedding_model()
    query_vector = model.encode(query).tolist()
    vector_str = "[" + ",".join(str(v) for v in query_vector) + "]"
    
    results = lakebase.run_query(f"""
        SELECT m.id, m.tmdb_id, m.title, m.overview, m.runtime, m.vote_average, m.release_date, m.genres, m.poster_path,
               1 - (e.embedding <=> %s::vector) AS similarity
        FROM {MOVIE_EMBEDDINGS_TABLE} e
        JOIN {MOVIES_TABLE} m ON m.id = e.movie_id
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """, (vector_str, vector_str, limit))
    
    for r in results:
        if r.get("genres"):
            genres = json.loads(r["genres"]) if isinstance(r["genres"], str) else r["genres"]
            r["genre_names"] = [g.get("name") for g in genres]
            r["poster_url"] = tmdb_broker.get_image_url(r["poster_path"])
    
    return {"query": query, "model": EMBEDDING_MODEL, "results": results}


@mcp.tool
def get_movie_details(movie_id: int) -> dict:
    """Get full movie details including cast, keywords, and streaming providers."""
    ensure_tables()
    movie = _get_or_create_movie(movie_id)
    
    genres = json.loads(movie["genres"]) if isinstance(movie["genres"], str) else movie["genres"]
    cast = json.loads(movie["cast"]) if isinstance(movie["cast"], str) else movie["cast"]
    keywords = json.loads(movie["keywords"]) if isinstance(movie["keywords"], str) else movie["keywords"]
    providers = json.loads(movie["providers"]) if isinstance(movie["providers"], str) else movie["providers"]
    
    return {
        "id": movie["id"], "tmdb_id": movie["tmdb_id"], "title": movie["title"],
        "overview": movie["overview"], "tagline": movie["tagline"], "release_date": movie["release_date"],
        "runtime": movie["runtime"], "vote_average": movie["vote_average"], "vote_count": movie["vote_count"],
        "poster_url": tmdb_broker.get_image_url(movie["poster_path"]),
        "backdrop_url": tmdb_broker.get_image_url(movie["backdrop_path"], "w780"),
        "genres": genres, "cast": cast[:10], "keywords": keywords, "providers": providers
    }


@mcp.tool
def create_group(name: str) -> dict:
    """Create a new movie night group."""
    ensure_tables()
    email = _get_user_email()
    _get_or_create_user(email)
    
    lakebase.run_write(f"INSERT INTO {GROUPS_TABLE} (name, created_by) VALUES (%s, %s)", (name, email))
    group = lakebase.run_query(f"SELECT * FROM {GROUPS_TABLE} WHERE id = (SELECT LASTVAL())")[0]
    lakebase.run_write(f"INSERT INTO {GROUP_MEMBERS_TABLE} (group_id, email) VALUES (%s, %s)", (group["id"], email))
    
    return {"id": group["id"], "name": group["name"], "created_by": group["created_by"], "members": [email]}


@mcp.tool
def join_group(group_id: int) -> dict:
    """Join an existing movie night group."""
    ensure_tables()
    email = _get_user_email()
    _get_or_create_user(email)
    
    group = lakebase.run_query(f"SELECT * FROM {GROUPS_TABLE} WHERE id = %s", (group_id,))
    if not group:
        return {"error": f"Group {group_id} not found"}
    
    lakebase.run_write(f"INSERT INTO {GROUP_MEMBERS_TABLE} (group_id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING", (group_id, email))
    members = lakebase.run_query(f"SELECT email FROM {GROUP_MEMBERS_TABLE} WHERE group_id = %s", (group_id,))
    
    return {"id": group[0]["id"], "name": group[0]["name"], "members": [m["email"] for m in members]}


@mcp.tool
def add_to_watchlist(group_id: int, movie_id: int) -> dict:
    """Add a movie to the group watchlist."""
    ensure_tables()
    email = _get_user_email()
    movie = _get_or_create_movie(movie_id)
    
    lakebase.run_write(f"""
        INSERT INTO {WATCHLIST_TABLE} (group_id, movie_id, added_by, status)
        VALUES (%s, %s, %s, 'pending')
        ON CONFLICT (group_id, movie_id) DO UPDATE SET status = 'pending'
    """, (group_id, movie["id"], email))
    
    return {"group_id": group_id, "movie_id": movie["id"], "title": movie["title"], "added_by": email, "status": "pending"}


@mcp.tool
def rate_movie(movie_id: int, rating: int, review: str = None) -> dict:
    """Rate a movie from 1-10 with an optional review."""
    ensure_tables()
    email = _get_user_email()
    
    if rating < 1 or rating > 10:
        return {"error": "Rating must be between 1 and 10"}
    
    movie = _get_or_create_movie(movie_id)
    
    lakebase.run_write(f"""
        INSERT INTO {RATINGS_TABLE} (email, movie_id, rating, review)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (email, movie_id) DO UPDATE SET rating = EXCLUDED.rating, review = EXCLUDED.review, updated_at = now()
    """, (email, movie["id"], rating, review))
    
    return {"email": email, "movie_id": movie["id"], "title": movie["title"], "rating": rating, "review": review}


@mcp.tool
def get_group_recommendations(group_id: int, limit: int = 5) -> dict:
    """
    Get AI-powered movie recommendations for a group.
    Considers everyone's ratings and preferences.
    """
    ensure_tables()
    
    # Get group members
    members = lakebase.run_query(f"SELECT email FROM {GROUP_MEMBERS_TABLE} WHERE group_id = %s", (group_id,))
    if not members:
        return {"error": "Group has no members"}
    
    member_emails = [m["email"] for m in members]
    
    # Get watched movies
    watched = lakebase.run_query(f"SELECT movie_id FROM {WATCHLIST_TABLE} WHERE group_id = %s AND status = 'watched'", (group_id,))
    watched_ids = [w["movie_id"] for w in watched]
    
    # Get ratings from group members
    ratings = lakebase.run_query(f"""
        SELECT movie_id, AVG(rating) as avg_rating, COUNT(*) as rating_count
        FROM {RATINGS_TABLE}
        WHERE email = ANY(%s)
        GROUP BY movie_id
        ORDER BY avg_rating DESC
    """, (member_emails,))
    
    rated_ids = [r["movie_id"] for r in ratings]
    
    # Get popular unrecommended movies
    import random
    popular = tmdb_broker.search_movies("popular", limit=limit * 5)
    random.shuffle(popular)
    
    recommendations = []
    for movie in popular:
        if movie["id"] in watched_ids or movie["id"] in rated_ids:
            continue
            
        movie_obj = _get_or_create_movie(movie["id"])
        
        # Compute score based on group preferences
        group_avg = next((r["avg_rating"] for r in ratings if r["movie_id"] == movie["id"]), None)
        score = (movie["vote_average"] * 0.6 + (group_avg * 0.4)) if group_avg else movie["vote_average"]
        
        reasoning = f"TMDB rating: {movie['vote_average']:.1f}/10"
        if group_avg:
            reasoning += f", Group average: {group_avg:.1f}/10"
        
        recommendations.append({
            "movie": movie_obj,
            "score": score,
            "reasoning": reasoning
        })
        
        if len(recommendations) >= limit:
            break
    
    return {
        "group_id": group_id,
        "members": member_emails,
        "recommendations": recommendations
    }


@mcp.tool
def compare_movies(movie_ids: list[int]) -> dict:
    """Compare multiple movies side by side."""
    ensure_tables()
    
    movies = []
    for movie_id in movie_ids:
        movie = _get_or_create_movie(movie_id)
        genres = json.loads(movie["genres"]) if isinstance(movie["genres"], str) else movie["genres"]
        movies.append({
            "id": movie["id"],
            "title": movie["title"],
            "year": movie["release_date"][:4] if movie["release_date"] else None,
            "runtime": movie["runtime"],
            "rating": movie["vote_average"],
            "genres": [g.get("name") for g in genres],
            "overview": movie["overview"][:200] + "..." if movie["overview"] and len(movie["overview"]) > 200 else movie["overview"],
        })
    
    return {"comparison": movies, "count": len(movies)}


if __name__ == "__main__":
    if hasattr(mcp, 'app') and mcp.app is not None:
        mcp.app.add_middleware(RequestContextMiddleware)
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))
    mcp.run(transport="http", host="0.0.0.0", port=port)