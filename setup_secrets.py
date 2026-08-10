"""
One-time setup script for AI Movie Night Planner.
Stores Lakebase URL and TMDB API key in Databricks secrets.
"""

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace
import getpass

w = WorkspaceClient()

print("\n🎬 AI Movie Night Planner - Secret Setup")
print("=" * 40)

# Create scope
try:
    w.secrets.create_scope(scope="database")
    print("✅ Created scope: database")
except Exception as e:
    print(f"ℹ️ Scope exists: {e}")

# Store Lakebase URL
url = getpass.getpass("Lakebase URL: ")
w.secrets.put_secret(scope="database", key="lakebase-url", string_value=url)
print("✅ Stored lakebase-url")

# Store TMDB API Key
key = getpass.getpass("TMDB API Key: ")
w.secrets.put_secret(scope="database", key="tmdb-api-key", string_value=key)
print("✅ Stored tmdb-api-key")

# Grant permissions
try:
    w.secrets.put_acl(scope="database", principal="users", permission=workspace.AclPermission.READ)
    print("✅ Granted read access")
except Exception as e:
    print(f"⚠️ Error: {e}")

print("\n✅ Setup complete!")
print("Secrets stored: lakebase-url, tmdb-api-key")