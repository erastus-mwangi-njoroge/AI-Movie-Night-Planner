# 🎬 AI Movie Night Planner - Capstone Project

A complete movie recommendation system with semantic search, group coordination, and AI-powered recommendations.

## Requirements Met

✅ **Spark Data Pipeline** - `notebooks/ingest_movie_embeddings.py` fetches movies from TMDB and computes embeddings using Spark

✅ **Third-Party API Integration** - TMDB API for movie data, cast, keywords, and streaming providers

✅ **Unstructured Data Processing** - Movie plot summaries, reviews, and descriptions are embedded for semantic search

✅ **Databricks App with Frontend** - Flask dashboard with search, watchlist, and ratings UI

✅ **AI Agent** - MCP server with 8+ tools that can search, recommend, and write data back to Lakebase

## Quick Start

### 1. Setup Database

```bash
# Run in Lakebase
psql -f sql/00_setup_movie_night.sql

```
### 2. Store Secrets

```python
python setup_secrets.py
# Enter: Lakebase URL, TMDB API Key
```
### 3. Run Embedding Pipeline
```python
# In Databricks, run notebooks/ingest_movie_embeddings.py
```
### 4. Deploy

#### MCP Server: Deploy mcp_server/ as Databricks App
#### Dashboard: Deploy dashboard/ as Databricks App

### 5. Register MCP Server

#### AI Gateway → MCPs → Add MCP → Paste MCP App URL

### 6. Create Agent


#### Create Agent Bricks agent with system prompt and tools from *mcp_server/*

### Tools

| Tool | Description |
|------|-------------|
| `search_movies` | Semantic movie search |
| `get_movie_details` | Full movie info |
| `create_group` | Create group |
| `join_group` | Join group |
| `add_to_watchlist` | Add to watchlist |
| `rate_movie` | Rate 1-10 |
| `get_group_recommendations` | AI recommendations |
| `compare_movies` | Compare movies |

### Example Queries
* - "Find a funny sci-fi movie that isn't too violent"*
* - "What should our group watch this weekend?"*
* - "Compare these movies for our movie night"*
* - "Rate The Dark Knight 9/10"*
### Architecture
TMDB API → Spark Pipeline → Lakebase + pgvector
                              ↓
                        MCP Server (FastMCP)
                              ↓
                    Agent Bricks + Dashboard

 
### Deployed URLs
MCP Server:
Dashboard: