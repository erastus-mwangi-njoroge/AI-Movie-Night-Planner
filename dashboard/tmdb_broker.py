"""
TMDB API Broker - Shared with MCP server.
Handles all TMDB API calls with rate limiting.
"""

import base64
import os
import time
import json
import requests
from databricks.sdk import WorkspaceClient

_w = WorkspaceClient()

_SECRET_SCOPE = os.environ.get("TMDB_SECRET_SCOPE", "database")
_API_KEY_SECRET = os.environ.get("TMDB_API_KEY_SECRET", "tmdb-api-key")
_BASE_URL = "https://api.themoviedb.org/3"
_IMAGE_BASE = "https://image.tmdb.org/t/p"

_last_request = 0
_MIN_INTERVAL = 0.05


def _get_api_key() -> str:
    secret = _w.secrets.get_secret(scope=_SECRET_SCOPE, key=_API_KEY_SECRET)
    return base64.b64decode(secret.value).decode("utf-8")


def _rate_limit():
    global _last_request
    now = time.time()
    if now - _last_request < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - (now - _last_request))
    _last_request = time.time()


def _request(endpoint: str, params: dict = None) -> dict:
    _rate_limit()
    params = params or {}
    params["api_key"] = _get_api_key()
    
    resp = requests.get(f"{_BASE_URL}{endpoint}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def search_movies(query: str, limit: int = 10) -> list[dict]:
    data = _request("/search/movie", {"query": query, "language": "en-US"})
    return data.get("results", [])[:limit]


def get_movie_details(movie_id: int) -> dict:
    details = _request(f"/movie/{movie_id}", {"language": "en-US"})
    credits = _request(f"/movie/{movie_id}/credits")
    keywords = _request(f"/movie/{movie_id}/keywords")
    providers = _request(f"/movie/{movie_id}/watch/providers")
    
    return {
        "id": details.get("id"),
        "tmdb_id": details.get("id"),
        "title": details.get("title"),
        "overview": details.get("overview"),
        "tagline": details.get("tagline"),
        "release_date": details.get("release_date"),
        "runtime": details.get("runtime"),
        "vote_average": details.get("vote_average"),
        "vote_count": details.get("vote_count"),
        "popularity": details.get("popularity"),
        "poster_path": details.get("poster_path"),
        "backdrop_path": details.get("backdrop_path"),
        "imdb_id": details.get("imdb_id"),
        "original_language": details.get("original_language"),
        "genres": details.get("genres", []),
        "movie_cast": [
            {"name": c.get("name"), "character": c.get("character")}
            for c in credits.get("cast", [])[:10]
        ],
        "keywords": [k.get("name") for k in keywords.get("keywords", [])],
        "providers": providers.get("results", {}),
    }


def get_movie_providers(movie_id: int) -> dict:
    data = _request(f"/movie/{movie_id}/watch/providers")
    return data.get("results", {})


def get_image_url(path: str, size: str = "w342") -> str:
    return f"{_IMAGE_BASE}/{size}{path}" if path else ""


def get_genre_list() -> list[dict]:
    data = _request("/genre/movie/list", {"language": "en-US"})
    return data.get("genres", [])


def discover_movies(genres: list[int] = None, year: int = None, 
                    min_vote: float = None, limit: int = 20) -> list[dict]:
    params = {
        "include_adult": False,
        "language": "en-US",
        "sort_by": "popularity.desc",
        "page": 1
    }
    if genres:
        params["with_genres"] = ",".join(str(g) for g in genres)
    if year:
        params["primary_release_year"] = year
    if min_vote:
        params["vote_average.gte"] = min_vote
    
    data = _request("/discover/movie", params)
    return data.get("results", [])[:limit]