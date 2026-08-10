"""
AI Movie Night Planner - Dashboard

Flask app providing a UI for:
- Searching movies
- Viewing group watchlists
- Getting AI recommendations
- Rating movies
- Managing groups
"""

import os
import logging
import json
from flask import Flask, jsonify, render_template, request

import tmdb_broker
import lakebase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("movie-night-dashboard")

app = Flask(__name__)

# Table names
USERS_TABLE = os.environ.get("USERS_TABLE", "users")
GROUPS_TABLE = os.environ.get("GROUPS_TABLE", "groups")
GROUP_MEMBERS_TABLE = os.environ.get("GROUP_MEMBERS_TABLE", "group_members")
MOVIES_TABLE = os.environ.get("MOVIES_TABLE", "movies")
WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist_items")
RATINGS_TABLE = os.environ.get("RATINGS_TABLE", "ratings")
RECOMMENDATIONS_TABLE = os.environ.get("RECOMMENDATIONS_TABLE", "recommendations")

DEFAULT_EMAIL = os.environ.get("DEFAULT_USER_EMAIL", "user@example.com")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    status_code = getattr(err, "code", 500)
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Main dashboard UI."""
    return render_template("index.html", default_email=DEFAULT_EMAIL)


# ============================================================
# Movie Endpoints
# ============================================================

@app.route("/api/search")
def api_search():
    """Search for movies."""
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "q parameter is required"}), 400
    
    limit = int(request.args.get("limit", 10))
    results = tmdb_broker.search_movies(query, limit=limit)
    
    # Add poster URLs
    for r in results:
        r["poster_url"] = tmdb_broker.get_image_url(r.get("poster_path"))
    
    return jsonify(results)


@app.route("/api/movie/<int:movie_id>")
def api_movie(movie_id):
    """Get movie details."""
    try:
        movie = tmdb_broker.get_movie_details(movie_id)
        movie["poster_url"] = tmdb_broker.get_image_url(movie.get("poster_path"))
        movie["backdrop_url"] = tmdb_broker.get_image_url(movie.get("backdrop_path"), "w780")
        return jsonify(movie)
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/movie/<int:movie_id>/providers")
def api_movie_providers(movie_id):
    """Get streaming providers for a movie."""
    try:
        providers = tmdb_broker.get_movie_providers(movie_id)
        return jsonify(providers)
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/genres")
def api_genres():
    """Get all movie genres."""
    genres = tmdb_broker.get_genre_list()
    return jsonify(genres)


@app.route("/api/discover")
def api_discover():
    """Discover movies by criteria."""
    genres = request.args.get("genres", "")
    genres = [int(g) for g in genres.split(",")] if genres else None
    
    year = request.args.get("year")
    year = int(year) if year else None
    
    min_vote = request.args.get("min_vote")
    min_vote = float(min_vote) if min_vote else None
    
    limit = int(request.args.get("limit", 20))
    
    results = tmdb_broker.discover_movies(genres=genres, year=year, min_vote=min_vote, limit=limit)
    for r in results:
        r["poster_url"] = tmdb_broker.get_image_url(r.get("poster_path"))
    
    return jsonify(results)


# ============================================================
# Group Endpoints
# ============================================================

@app.route("/api/groups")
def api_groups():
    """Get groups for a user."""
    email = request.args.get("email", DEFAULT_EMAIL)
    
    groups = lakebase.run_query(f"""
        SELECT g.id, g.name, g.created_by, g.created_at,
               COUNT(DISTINCT gm.email) as member_count,
               COUNT(DISTINCT wi.id) as watchlist_count
        FROM {GROUPS_TABLE} g
        JOIN {GROUP_MEMBERS_TABLE} gm ON g.id = gm.group_id
        LEFT JOIN {WATCHLIST_TABLE} wi ON g.id = wi.group_id
        WHERE gm.email = %s
        GROUP BY g.id, g.name, g.created_by, g.created_at
        ORDER BY g.created_at DESC
    """, (email,))
    
    return jsonify(groups)


@app.route("/api/group/<int:group_id>/members")
def api_group_members(group_id):
    """Get group members."""
    members = lakebase.run_query(f"""
        SELECT email, joined_at FROM {GROUP_MEMBERS_TABLE}
        WHERE group_id = %s ORDER BY joined_at ASC
    """, (group_id,))
    return jsonify(members)


@app.route("/api/group", methods=["POST"])
def api_create_group():
    """Create a new group."""
    data = request.json
    name = data.get("name")
    email = data.get("email", DEFAULT_EMAIL)
    
    if not name:
        return jsonify({"error": "name is required"}), 400
    
    # Create user if needed
    lakebase.run_write(f"INSERT INTO {USERS_TABLE} (email, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING", 
                       (email, email.split('@')[0]))
    
    # Create group
    lakebase.run_write(f"INSERT INTO {GROUPS_TABLE} (name, created_by) VALUES (%s, %s)", (name, email))
    group = lakebase.run_query(f"SELECT * FROM {GROUPS_TABLE} WHERE id = (SELECT LASTVAL())")[0]
    
    # Add creator as member
    lakebase.run_write(f"INSERT INTO {GROUP_MEMBERS_TABLE} (group_id, email) VALUES (%s, %s)", (group["id"], email))
    
    return jsonify({"id": group["id"], "name": group["name"], "created_by": group["created_by"]}), 201


@app.route("/api/group/<int:group_id>/join", methods=["POST"])
def api_join_group(group_id):
    """Join a group."""
    email = request.json.get("email", DEFAULT_EMAIL)
    
    # Check if group exists
    group = lakebase.run_query(f"SELECT * FROM {GROUPS_TABLE} WHERE id = %s", (group_id,))
    if not group:
        return jsonify({"error": "Group not found"}), 404
    
    # Add member
    lakebase.run_write(f"INSERT INTO {GROUP_MEMBERS_TABLE} (group_id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING", 
                       (group_id, email))
    
    return jsonify({"id": group_id, "email": email, "joined": True})


# ============================================================
# Watchlist Endpoints
# ============================================================

@app.route("/api/group/<int:group_id>/watchlist")
def api_watchlist(group_id):
    """Get group watchlist with movie details."""
    watchlist = lakebase.run_query(f"""
        SELECT wi.id, wi.movie_id, wi.added_by, wi.added_at, wi.status, wi.watched_at,
               m.title, m.overview, m.poster_path, m.runtime, m.vote_average, m.release_date, m.genres
        FROM {WATCHLIST_TABLE} wi
        JOIN {MOVIES_TABLE} m ON m.id = wi.movie_id
        WHERE wi.group_id = %s
        ORDER BY wi.added_at DESC
    """, (group_id,))
    
    for item in watchlist:
        if item.get("genres"):
            genres = json.loads(item["genres"]) if isinstance(item["genres"], str) else item["genres"]
            item["genre_names"] = [g.get("name") for g in genres]
        item["poster_url"] = tmdb_broker.get_image_url(item.get("poster_path"))
    
    return jsonify(watchlist)


@app.route("/api/watchlist", methods=["POST"])
def api_add_to_watchlist():
    """Add movie to watchlist."""
    data = request.json
    group_id = data.get("group_id")
    movie_id = data.get("movie_id")
    email = data.get("email", DEFAULT_EMAIL)
    
    if not group_id or not movie_id:
        return jsonify({"error": "group_id and movie_id are required"}), 400
    
    # Check if movie exists in DB
    movie = lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE tmdb_id = %s", (movie_id,))
    if not movie:
        # Fetch from TMDB and insert
        movie_data = tmdb_broker.get_movie_details(movie_id)
        lakebase.run_write(f"""
            INSERT INTO {MOVIES_TABLE} (id, tmdb_id, title, overview, tagline, release_date, runtime,
                vote_average, vote_count, popularity, poster_path, backdrop_path, imdb_id, 
                original_language, genres, cast, keywords, providers)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tmdb_id) DO NOTHING
        """, (
            movie_data["id"], movie_data["tmdb_id"], movie_data["title"], movie_data["overview"],
            movie_data["tagline"], movie_data["release_date"], movie_data["runtime"],
            movie_data["vote_average"], movie_data["vote_count"], movie_data["popularity"],
            movie_data["poster_path"], movie_data["backdrop_path"], movie_data["imdb_id"],
            movie_data["original_language"], json.dumps(movie_data["genres"]),
            json.dumps(movie_data["cast"]), json.dumps(movie_data["keywords"]),
            json.dumps(movie_data["providers"])
        ))
        movie = lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE tmdb_id = %s", (movie_id,))
    
    # Add to watchlist
    lakebase.run_write(f"""
        INSERT INTO {WATCHLIST_TABLE} (group_id, movie_id, added_by, status)
        VALUES (%s, %s, %s, 'pending')
        ON CONFLICT (group_id, movie_id) DO UPDATE SET status = 'pending'
    """, (group_id, movie[0]["id"], email))
    
    return jsonify({"group_id": group_id, "movie_id": movie_id, "title": movie[0]["title"], "status": "pending"})


@app.route("/api/watchlist/<int:item_id>/status", methods=["PUT"])
def api_update_watchlist_status(item_id):
    """Update watchlist item status (watched/skipped)."""
    data = request.json
    status = data.get("status")
    
    if status not in ["watched", "skipped", "pending"]:
        return jsonify({"error": "Invalid status"}), 400
    
    watched_at = "now()" if status == "watched" else "NULL"
    lakebase.run_write(f"""
        UPDATE {WATCHLIST_TABLE} 
        SET status = %s, watched_at = {watched_at}
        WHERE id = %s
    """, (status, item_id))
    
    return jsonify({"id": item_id, "status": status})


# ============================================================
# Ratings Endpoints
# ============================================================

@app.route("/api/group/<int:group_id>/ratings")
def api_group_ratings(group_id):
    """Get ratings from group members."""
    ratings = lakebase.run_query(f"""
        SELECT r.email, r.movie_id, r.rating, r.review, r.created_at, m.title, m.poster_path
        FROM {RATINGS_TABLE} r
        JOIN {MOVIES_TABLE} m ON m.id = r.movie_id
        JOIN {GROUP_MEMBERS_TABLE} gm ON gm.email = r.email
        WHERE gm.group_id = %s
        ORDER BY r.created_at DESC
        LIMIT 50
    """, (group_id,))
    
    for r in ratings:
        r["poster_url"] = tmdb_broker.get_image_url(r.get("poster_path"))
    
    return jsonify(ratings)


@app.route("/api/rate", methods=["POST"])
def api_rate_movie():
    """Rate a movie."""
    data = request.json
    movie_id = data.get("movie_id")
    rating = data.get("rating")
    review = data.get("review")
    email = data.get("email", DEFAULT_EMAIL)
    
    if not movie_id or rating is None:
        return jsonify({"error": "movie_id and rating are required"}), 400
    
    if rating < 1 or rating > 10:
        return jsonify({"error": "Rating must be between 1 and 10"}), 400
    
    # Get movie
    movie = lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE tmdb_id = %s", (movie_id,))
    if not movie:
        return jsonify({"error": "Movie not found"}), 404
    
    # Save rating
    lakebase.run_write(f"""
        INSERT INTO {RATINGS_TABLE} (email, movie_id, rating, review)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (email, movie_id) DO UPDATE SET 
            rating = EXCLUDED.rating, review = EXCLUDED.review, updated_at = now()
    """, (email, movie[0]["id"], rating, review))
    
    return jsonify({"movie_id": movie_id, "title": movie[0]["title"], "rating": rating, "review": review})


# ============================================================
# Recommendations Endpoint (calls MCP tool via direct DB)
# ============================================================

@app.route("/api/group/<int:group_id>/recommendations")
def api_recommendations(group_id):
    """Get AI recommendations for a group."""
    limit = int(request.args.get("limit", 5))
    
    # Get group members
    members = lakebase.run_query(f"SELECT email FROM {GROUP_MEMBERS_TABLE} WHERE group_id = %s", (group_id,))
    if not members:
        return jsonify({"error": "Group has no members"})
    
    member_emails = [m["email"] for m in members]
    
    # Get watched movies
    watched = lakebase.run_query(f"SELECT movie_id FROM {WATCHLIST_TABLE} WHERE group_id = %s AND status = 'watched'", (group_id,))
    watched_ids = [w["movie_id"] for w in watched]
    
    # Get group ratings
    ratings = lakebase.run_query(f"""
        SELECT movie_id, AVG(rating) as avg_rating
        FROM {RATINGS_TABLE}
        WHERE email = ANY(%s)
        GROUP BY movie_id
        ORDER BY avg_rating DESC
    """, (member_emails,))
    
    rated_ids = [r["movie_id"] for r in ratings]
    
    # Get popular movies from TMDB
    import random
    popular = tmdb_broker.search_movies("popular", limit=limit * 5)
    random.shuffle(popular)
    
    recommendations = []
    for movie in popular:
        if movie["id"] in watched_ids or movie["id"] in rated_ids:
            continue
        
        # Get or create movie in DB
        existing = lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE tmdb_id = %s", (movie["id"],))
        if not existing:
            movie_data = tmdb_broker.get_movie_details(movie["id"])
            lakebase.run_write(f"""
                INSERT INTO {MOVIES_TABLE} (id, tmdb_id, title, overview, tagline, release_date, runtime,
                    vote_average, vote_count, popularity, poster_path, backdrop_path, imdb_id, 
                    original_language, genres, cast, keywords, providers)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tmdb_id) DO NOTHING
            """, (
                movie_data["id"], movie_data["tmdb_id"], movie_data["title"], movie_data["overview"],
                movie_data["tagline"], movie_data["release_date"], movie_data["runtime"],
                movie_data["vote_average"], movie_data["vote_count"], movie_data["popularity"],
                movie_data["poster_path"], movie_data["backdrop_path"], movie_data["imdb_id"],
                movie_data["original_language"], json.dumps(movie_data["genres"]),
                json.dumps(movie_data["cast"]), json.dumps(movie_data["keywords"]),
                json.dumps(movie_data["providers"])
            ))
            existing = lakebase.run_query(f"SELECT * FROM {MOVIES_TABLE} WHERE tmdb_id = %s", (movie["id"],))
        
        movie_obj = existing[0]
        genres = json.loads(movie_obj["genres"]) if isinstance(movie_obj["genres"], str) else movie_obj["genres"]
        
        # Calculate score
        group_avg = next((r["avg_rating"] for r in ratings if r["movie_id"] == movie_obj["id"]), None)
        score = (movie["vote_average"] * 0.6 + (group_avg * 0.4)) if group_avg else movie["vote_average"]
        
        recommendations.append({
            "movie": {
                "id": movie_obj["id"],
                "tmdb_id": movie_obj["tmdb_id"],
                "title": movie_obj["title"],
                "overview": movie_obj["overview"],
                "runtime": movie_obj["runtime"],
                "vote_average": movie_obj["vote_average"],
                "release_date": movie_obj["release_date"],
                "poster_url": tmdb_broker.get_image_url(movie_obj["poster_path"]),
                "genre_names": [g.get("name") for g in genres],
            },
            "score": score,
            "reasoning": f"TMDB: {movie['vote_average']:.1f}/10" + (f", Group: {group_avg:.1f}/10" if group_avg else "")
        })
        
        if len(recommendations) >= limit:
            break
    
    return jsonify({"group_id": group_id, "members": member_emails, "recommendations": recommendations})


# ============================================================
# User Endpoints
# ============================================================

@app.route("/api/user/preferences", methods=["GET", "POST"])
def api_user_preferences():
    """Get or set user preferences."""
    email = request.args.get("email", DEFAULT_EMAIL) if request.method == "GET" else request.json.get("email", DEFAULT_EMAIL)
    
    if request.method == "GET":
        prefs = lakebase.run_query(f"SELECT * FROM user_preferences WHERE email = %s", (email,))
        return jsonify(prefs[0] if prefs else {"email": email})
    
    data = request.json
    updates = []
    params = [email]
    
    for field in ["favorite_genres", "favorite_actors", "preferred_runtime_min", 
                  "preferred_runtime_max", "max_violence_rating", "min_rating"]:
        if field in data and data[field] is not None:
            updates.append(f"{field} = %s")
            params.append(json.dumps(data[field]) if field in ["favorite_genres", "favorite_actors"] else data[field])
    
    if updates:
        updates.append("updated_at = now()")
        lakebase.run_write(f"""
            INSERT INTO user_preferences (email) VALUES (%s)
            ON CONFLICT (email) DO UPDATE SET {', '.join(updates)}
        """, params)
    
    prefs = lakebase.run_query(f"SELECT * FROM user_preferences WHERE email = %s", (email,))
    return jsonify(prefs[0] if prefs else {"email": email})


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", 8001))
    app.run(debug=True, host=host, port=port)