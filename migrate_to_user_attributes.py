#!/usr/bin/env python3
"""
Migration script to move watchlist, continue_watching, and favorites
from separate collections to user document attributes.

Run this script ONCE after deploying the updated movie_api.py code.
"""

import os
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def migrate_data():
    """Migrate data from separate collections to user attributes"""

    # Connect to MongoDB
    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
    client = MongoClient(mongo_uri)
    db = client['retroflix']

    # Get collections
    users_collection = db['users']
    watchlists_collection = db['watchlists']
    continue_watching_collection = db['continue_watching']
    favorites_collection = db['favorites']

    print("Starting migration...")
    print("=" * 60)

    # Get all users
    users = list(users_collection.find({}))
    print(f"Found {len(users)} users to migrate")

    migrated_users = 0
    total_watchlist_items = 0
    total_continue_watching_items = 0
    total_favorite_items = 0

    for user in users:
        user_id = user['_id']
        username = user.get('username', 'unknown')

        # Initialize arrays if they don't exist
        watchlist = []
        continue_watching = []
        favorites = []

        # Migrate watchlist items
        watchlist_items = list(watchlists_collection.find({'user_id': user_id}))
        for item in watchlist_items:
            watchlist.append({
                'content_id': item['content_id'],
                'content_type': item['content_type'],
                'title': item['title'],
                'poster_path': item.get('poster_path'),
                'list_name': item.get('list_name', 'My Watchlist'),
                'added_at': item.get('added_at', datetime.utcnow())
            })
        total_watchlist_items += len(watchlist_items)

        # Migrate continue watching items
        continue_watching_items = list(continue_watching_collection.find({'user_id': user_id}))
        for item in continue_watching_items:
            continue_watching.append({
                'content_id': item['content_id'],
                'content_type': item['content_type'],
                'title': item['title'],
                'poster_path': item.get('poster_path'),
                'progress': item.get('progress', 0),
                'season': item.get('season'),
                'episode': item.get('episode'),
                'last_watched': item.get('last_watched', datetime.utcnow())
            })
        total_continue_watching_items += len(continue_watching_items)

        # Migrate favorites items
        favorite_items = list(favorites_collection.find({'user_id': user_id}))
        for item in favorite_items:
            favorites.append({
                'channel_id': item['channel_id'],
                'channel_name': item['channel_name'],
                'added_at': item.get('added_at', datetime.utcnow())
            })
        total_favorite_items += len(favorite_items)

        # Update user document with all arrays
        if watchlist or continue_watching or favorites:
            users_collection.update_one(
                {'_id': user_id},
                {
                    '$set': {
                        'watchlist': watchlist,
                        'continue_watching': continue_watching,
                        'favorites': favorites
                    }
                }
            )
            migrated_users += 1
            print(f"✓ Migrated user '{username}': {len(watchlist)} watchlist, "
                  f"{len(continue_watching)} continue watching, {len(favorites)} favorites")

    print("=" * 60)
    print(f"Migration complete!")
    print(f"Migrated {migrated_users} users")
    print(f"Total items migrated:")
    print(f"  - Watchlist: {total_watchlist_items}")
    print(f"  - Continue Watching: {total_continue_watching_items}")
    print(f"  - Favorites: {total_favorite_items}")
    print()
    print("=" * 60)
    print("IMPORTANT: Old collections are still intact.")
    print("After verifying the migration worked correctly, you can drop them:")
    print("  db.watchlists.drop()")
    print("  db.continue_watching.drop()")
    print("  db.favorites.drop()")
    print("=" * 60)

    client.close()

if __name__ == '__main__':
    try:
        migrate_data()
    except Exception as e:
        print(f"Error during migration: {e}")
        import traceback
        traceback.print_exc()
