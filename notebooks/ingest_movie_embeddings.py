# Databricks notebook source
# MAGIC %md
# MAGIC # Ingest Movie Data and Compute Embeddings
# MAGIC
# MAGIC This notebook:
# MAGIC 1. Fetches popular movies from TMDB API
# MAGIC 2. Stores them in Lakebase
# MAGIC 3. Computes embeddings using Spark
# MAGIC 4. Stores embeddings in pgvector

# COMMAND ----------

# MAGIC %pip install -q databricks-sdk sentence-transformers psycopg2-binary requests

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration

# COMMAND ----------

dbutils.widgets.text("movies_table", "movies", "Movies table")
dbutils.widgets.text("embeddings_table", "movie_embeddings", "Embeddings table")
dbutils.widgets.text("embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Embedding model")
dbutils.widgets.text("movies_to_fetch", "100", "Number of popular movies to fetch")
dbutils.widgets.text("tmdb_secret_scope", "database", "TMDB secret scope")
dbutils.widgets.text("tmdb_api_key_secret", "tmdb-api-key", "TMDB API key secret")

MOVIES_TABLE = dbutils.widgets.get("movies_table")
EMBEDDINGS_TABLE = dbutils.widgets.get("embeddings_table")
EMBEDDING_MODEL = dbutils.widgets.get("embedding_model")
MOVIES_TO_FETCH = int(dbutils.widgets.get("movies_to_fetch"))
TMDB_SCOPE = dbutils.widgets.get("tmdb_secret_scope")
TMDB_KEY_SECRET = dbutils.widgets.get("tmdb_api_key_secret")

print(f"Movies to fetch: {MOVIES_TO_FETCH}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Get Secrets

# COMMAND ----------

import base64
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

def get_lakebase_url() -> str:
    secret = w.secrets.get_secret(scope="database", key="lakebase-url")
    return base64.b64decode(secret.value).decode("utf-8")

def get_tmdb_key() -> str:
    secret = w.secrets.get_secret(scope=TMDB_SCOPE, key=TMDB_KEY_SECRET)
    return base64.b64decode(secret.value).decode("utf-8")

LAKEBASE_URL = get_lakebase_url()
TMDB_API_KEY = get_tmdb_key()

print("✅ Secrets retrieved")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fetch Movies from TMDB

# COMMAND ----------

import requests
import json
import time

def fetch_tmdb_movies(page: int = 1) -> list:
    url = "https://api.themoviedb.org/3/movie/popular"
    params = {"api_key": TMDB_API_KEY, "language": "en-US", "page": page}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("results", [])

def fetch_movie_details(movie_id: int) -> dict:
    url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    params = {"api_key": TMDB_API_KEY, "language": "en-US", "append_to_response": "credits,keywords,watch/providers"}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()

all_movies = []
page = 1

print(f"Fetching {MOVIES_TO_FETCH} popular movies...")

while len(all_movies) < MOVIES_TO_FETCH and page <= 10:
    movies = fetch_tmdb_movies(page)
    if not movies:
        break
    
    for movie in movies[:MOVIES_TO_FETCH - len(all_movies)]:
        try:
            details = fetch_movie_details(movie["id"])
            all_movies.append({
                "id": details["id"],
                "tmdb_id": details["id"],
                "title": details["title"],
                "overview": details.get("overview", ""),
                "tagline": details.get("tagline", ""),
                "release_date": details.get("release_date"),
                "runtime": details.get("runtime"),
                "vote_average": details.get("vote_average", 0),
                "vote_count": details.get("vote_count", 0),
                "popularity": details.get("popularity", 0),
                "poster_path": details.get("poster_path", ""),
                "backdrop_path": details.get("backdrop_path", ""),
                "imdb_id": details.get("imdb_id", ""),
                "original_language": details.get("original_language", ""),
                "genres": json.dumps(details.get("genres", [])),
                "cast": json.dumps(details.get("credits", {}).get("cast", [])[:20]),
                "keywords": json.dumps(details.get("keywords", {}).get("keywords", [])),
                "providers": json.dumps(details.get("watch/providers", {}).get("results", {})),
            })
            print(f"  Fetched: {details['title']} ({len(all_movies)}/{MOVIES_TO_FETCH})")
            time.sleep(0.05)
        except Exception as e:
            print(f"  Error: {e}")
            continue
    page += 1

print(f"✅ Fetched {len(all_movies)} movies")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write to Lakebase via Spark

# COMMAND ----------

from urllib.parse import urlparse
parsed = urlparse(LAKEBASE_URL)
jdbc_url = f"jdbc:postgresql://{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}?sslmode=require"

# Create DataFrame
schema = ["id", "tmdb_id", "title", "overview", "tagline", "release_date", "runtime",
          "vote_average", "vote_count", "popularity", "poster_path", "backdrop_path",
          "imdb_id", "original_language", "genres", "cast", "keywords", "providers"]

df = spark.createDataFrame(all_movies, schema=schema)
print(f"Created DataFrame with {df.count()} rows")

# Write to Lakebase
df.write.mode("overwrite").format("jdbc") \
    .option("url", jdbc_url) \
    .option("dbtable", MOVIES_TABLE) \
    .option("user", parsed.username) \
    .option("password", parsed.password) \
    .option("driver", "org.postgresql.Driver") \
    .option("batchsize", "100") \
    .save()

print(f"✅ Wrote {df.count()} movies to Lakebase")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Compute Embeddings with Pandas UDF

# COMMAND ----------

from pyspark.sql.functions import pandas_udf, col, lit
from pyspark.sql.types import ArrayType, FloatType, StringType
import pandas as pd
from sentence_transformers import SentenceTransformer

model_name_bc = spark.sparkContext.broadcast(EMBEDDING_MODEL)

@pandas_udf(ArrayType(FloatType()))
def compute_embeddings_udf(title_series: pd.Series, overview_series: pd.Series, 
                           genres_series: pd.Series, cast_series: pd.Series) -> pd.Series:
    import os
    os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
    model = SentenceTransformer(model_name_bc.value, cache_folder="/tmp/.cache/huggingface")
    
    texts = []
    for i in range(len(title_series)):
        parts = []
        if title_series.iloc[i]:
            parts.append(f"Title: {title_series.iloc[i]}")
        if overview_series.iloc[i]:
            parts.append(f"Overview: {overview_series.iloc[i]}")
        texts.append(" ".join(parts))
    
    embeddings = model.encode(texts, show_progress_bar=True)
    return pd.Series(embeddings.tolist())

# Compute embeddings
movies_df = spark.read.format("jdbc") \
    .option("url", jdbc_url) \
    .option("dbtable", MOVIES_TABLE) \
    .option("user", parsed.username) \
    .option("password", parsed.password) \
    .option("driver", "org.postgresql.Driver") \
    .load()

df_emb = movies_df.withColumn(
    "embedding",
    compute_embeddings_udf(col("title"), col("overview"), col("genres"), col("cast"))
)

def array_to_vector(arr):
    if arr is None:
        return None
    return "[" + ",".join(str(float(v)) for v in arr) + "]"

to_vector_udf = pandas_udf(lambda s: pd.Series([array_to_vector(v) for v in s]), StringType())

df_final = df_emb.select(
    col("id").alias("movie_id"),
    to_vector_udf(col("embedding")).alias("embedding"),
    lit(EMBEDDING_MODEL).alias("model_name")
)

print(f"Computed {df_final.count()} embeddings")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write Embeddings to Lakebase

# COMMAND ----------

df_final.write.mode("overwrite").format("jdbc") \
    .option("url", jdbc_url) \
    .option("dbtable", EMBEDDINGS_TABLE) \
    .option("user", parsed.username) \
    .option("password", parsed.password) \
    .option("driver", "org.postgresql.Driver") \
    .option("batchsize", "100") \
    .save()

print(f"✅ Wrote {df_final.count()} embeddings to Lakebase")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify

# COMMAND ----------

import psycopg2

conn = psycopg2.connect(
    host=parsed.hostname,
    port=parsed.port or 5432,
    dbname=parsed.path.lstrip('/'),
    user=parsed.username,
    password=parsed.password,
    sslmode='require'
)

try:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {EMBEDDINGS_TABLE}")
    print(f"✅ Total embeddings: {cur.fetchone()[0]}")
finally:
    cur.close()
    conn.close()