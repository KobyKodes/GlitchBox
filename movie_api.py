#!/usr/bin/env python3
"""
Movie API Server - Flask backend for TMDB integration
Provides REST API for movie search and streaming URL generation
"""

# IMPORTANT: Eventlet monkey patching MUST be done before any other imports
import eventlet
eventlet.monkey_patch()
import eventlet.tpool

from flask import Flask, request, jsonify, render_template_string, send_file, Response, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
import requests
import sys
import os
import base64
import gzip
from io import BytesIO
import random
import string
from datetime import datetime, timedelta
import json
import atexit
import re
import subprocess
import time
from urllib.parse import urlparse, parse_qs, quote
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), 'backend', '.env'))

# Load API keys from environment variables (secure for production)
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', 'e577f3394a629d69efa3a9414e172237')

# VidSrc scraper service URL (Playwright-based, runs on port 4000)
VIDSRC_SCRAPER_URL = os.environ.get('VIDSRC_SCRAPER_URL', 'http://localhost:4000')
OMDB_API_KEY = os.environ.get('OMDB_API_KEY', 'ecbf499d')

app = Flask(__name__)
# Generate a secure random secret key for production
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-this-in-production')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=7)

CORS(app)  # Enable CORS for frontend access
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
jwt = JWTManager(app)

# MongoDB connection
mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri)
db = client['retroflix']

# Collections
users_collection = db['users']
comments_collection = db['comments']
comment_likes_collection = db['comment_likes']
friend_requests_collection = db['friend_requests']
ratings_collection = db['ratings']

# Create indexes
try:
    users_collection.create_index('username', unique=True)
    users_collection.create_index('email', unique=True)
    comments_collection.create_index([('content_id', 1), ('user_id', 1)])
    comment_likes_collection.create_index([('comment_id', 1), ('user_id', 1)], unique=True)
    ratings_collection.create_index([('content_key', 1), ('user_id', 1)], unique=True)
    ratings_collection.create_index([('content_key', 1)])
except Exception as e:
    print(f"Note: Some indexes may already exist: {e}")

# Cache file paths
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
SEASON_CACHE_FILE = os.path.join(CACHE_DIR, 'season_cache.json')
OMDB_CACHE_FILE = os.path.join(CACHE_DIR, 'omdb_cache.json')

# Ensure cache directory exists
os.makedirs(CACHE_DIR, exist_ok=True)

# Load caches from disk
def load_cache(cache_file):
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading cache from {cache_file}: {e}")
    return {}

def save_cache(cache_file, cache_data):
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)
    except Exception as e:
        print(f"Error saving cache to {cache_file}: {e}")

# Caching
season_cache = load_cache(SEASON_CACHE_FILE)  # {tv_id_season_number: season_data}
omdb_cache = load_cache(OMDB_CACHE_FILE)  # {imdb_id: omdb_data}

print(f"Loaded {len(season_cache)} season(s) and {len(omdb_cache)} OMDB entries from cache")

# Save caches on exit
def save_all_caches():
    save_cache(SEASON_CACHE_FILE, season_cache)
    save_cache(OMDB_CACHE_FILE, omdb_cache)
    print(f"Saved {len(season_cache)} season(s) and {len(omdb_cache)} OMDB entries to cache")

atexit.register(save_all_caches)

# Watchparty storage
watchparty_rooms = {}  # {room_code: {host: sid, users: {sid: username}, content: {}, state: {}}}
user_rooms = {}  # {sid: room_code}

# Bingeflix scraper cache and concurrency control
bingeflix_cache = {}  # {cache_key: {data: ..., timestamp: ...}}
bingeflix_m3u8_cache = {}  # {stream_id: {content: str, referer: str, base_url: str, timestamp: float}}
BINGEFLIX_CACHE_TTL = 15 * 60  # 15 minutes
bingeflix_lock = eventlet.semaphore.Semaphore(1)

class TMDBService:
    def __init__(self):
        self.base_url = "https://api.themoviedb.org/3"
        self.api_key = TMDB_API_KEY
        self.image_base_url = "https://image.tmdb.org/t/p/w500"

    def search_movies(self, query, year=None, page=1):
        """Search for movies"""
        params = {
            "api_key": self.api_key,
            "query": query,
            "include_adult": "false",
            "page": page
        }
        if year:
            params["year"] = year

        try:
            response = requests.get(f"{self.base_url}/search/movie", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs
            for movie in data.get('results', []):
                if movie.get('poster_path'):
                    movie['poster_url'] = self.image_base_url + movie['poster_path']
                else:
                    movie['poster_url'] = None

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def search_tv_shows(self, query, year=None, page=1):
        """Search for TV shows"""
        params = {
            "api_key": self.api_key,
            "query": query,
            "include_adult": "false",
            "page": page
        }
        if year:
            params["first_air_date_year"] = year

        try:
            response = requests.get(f"{self.base_url}/search/tv", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs and content type
            for show in data.get('results', []):
                show['content_type'] = 'tv'
                if show.get('poster_path'):
                    show['poster_url'] = self.image_base_url + show['poster_path']
                else:
                    show['poster_url'] = None

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def search_multi(self, query, page=1):
        """Search for both movies and TV shows"""
        params = {
            "api_key": self.api_key,
            "query": query,
            "include_adult": "false",
            "page": page
        }

        try:
            response = requests.get(f"{self.base_url}/search/multi", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs and content type
            for item in data.get('results', []):
                if item.get('poster_path'):
                    item['poster_url'] = self.image_base_url + item['poster_path']
                else:
                    item['poster_url'] = None

                # Ensure content type is set
                if 'media_type' not in item:
                    # Guess based on available fields
                    if 'title' in item and 'release_date' in item:
                        item['media_type'] = 'movie'
                    elif 'name' in item and 'first_air_date' in item:
                        item['media_type'] = 'tv'

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_movie_details(self, movie_id):
        """Get detailed movie information"""
        try:
            params = {"api_key": self.api_key, "append_to_response": "external_ids"}
            response = requests.get(f"{self.base_url}/movie/{movie_id}", params=params)
            response.raise_for_status()
            movie = response.json()

            # Add full poster and backdrop URLs
            if movie.get('poster_path'):
                movie['poster_url'] = self.image_base_url + movie['poster_path']
            if movie.get('backdrop_path'):
                movie['backdrop_url'] = self.image_base_url.replace('w500', 'w1280') + movie['backdrop_path']

            return movie

        except Exception as e:
            return {"error": str(e)}

    def get_tv_details(self, tv_id):
        """Get detailed TV show information"""
        try:
            params = {"api_key": self.api_key, "append_to_response": "external_ids"}
            response = requests.get(f"{self.base_url}/tv/{tv_id}", params=params)
            response.raise_for_status()
            show = response.json()

            # Add full poster and backdrop URLs
            if show.get('poster_path'):
                show['poster_url'] = self.image_base_url + show['poster_path']
            if show.get('backdrop_path'):
                show['backdrop_url'] = self.image_base_url.replace('w500', 'w1280') + show['backdrop_path']

            return show

        except Exception as e:
            return {"error": str(e)}

    def get_tv_episode_external_ids(self, tv_id, season_number, episode_number):
        """Get external IDs for a specific episode"""
        try:
            params = {"api_key": self.api_key}
            response = requests.get(
                f"{self.base_url}/tv/{tv_id}/season/{season_number}/episode/{episode_number}/external_ids",
                params=params
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching episode external IDs: {e}")
            return {}

    def get_tv_season_details(self, tv_id, season_number, include_ratings=True):
        """Get detailed season information"""
        try:
            # Check cache first
            cache_key = f"{tv_id}_{season_number}"
            if cache_key in season_cache and include_ratings:
                print(f"Loading season {cache_key} from cache")
                return season_cache[cache_key]

            print(f"Fetching season {cache_key} from API (not in cache or quick mode)")

            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/tv/{tv_id}/season/{season_number}", params=params)
            response.raise_for_status()
            season = response.json()

            # Add full poster URLs for episodes
            for episode in season.get('episodes', []):
                if episode.get('still_path'):
                    episode['still_url'] = self.image_base_url + episode['still_path']

            # Fetch OMDB ratings only if requested (not in quick mode)
            if include_ratings:
                for episode in season.get('episodes', []):
                    # Fetch external IDs and OMDB ratings (lightweight) for each episode
                    external_ids = self.get_tv_episode_external_ids(
                        tv_id,
                        season_number,
                        episode['episode_number']
                    )

                    if external_ids.get('imdb_id'):
                        omdb_data = get_omdb_episode_ratings(external_ids['imdb_id'])
                        if omdb_data:
                            episode.update(omdb_data)

                # Cache the result only if it includes ratings
                season_cache[cache_key] = season
                save_cache(SEASON_CACHE_FILE, season_cache)  # Save immediately
                print(f"Cached season data for {cache_key}")

            return season

        except Exception as e:
            return {"error": str(e)}

    def get_movie_release_info(self, movie_id):
        """Get movie release dates and certifications"""
        try:
            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/movie/{movie_id}/release_dates", params=params, timeout=3)
            response.raise_for_status()
            data = response.json()

            # Try to find US certification first, fallback to any certification
            for country_data in data.get('results', []):
                if country_data['iso_3166_1'] == 'US':
                    for release in country_data['release_dates']:
                        if release.get('certification'):
                            return release['certification']

            # If no US cert, try any country
            for country_data in data.get('results', []):
                for release in country_data['release_dates']:
                    if release.get('certification'):
                        return release['certification']
            return None
        except:
            return None

    def get_tv_content_rating(self, tv_id):
        """Get TV show content ratings"""
        try:
            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/tv/{tv_id}/content_ratings", params=params, timeout=3)
            response.raise_for_status()
            data = response.json()

            # Try to find US rating first
            for rating_data in data.get('results', []):
                if rating_data['iso_3166_1'] == 'US':
                    if rating_data.get('rating'):
                        return rating_data['rating']

            # If no US rating, try any country
            for rating_data in data.get('results', []):
                if rating_data.get('rating'):
                    return rating_data['rating']
            return None
        except:
            return None

    def get_popular_movies(self, page=1, language=None):
        """Get popular movies"""
        try:
            params = {"api_key": self.api_key, "page": page}

            # Use discover API if language is specified
            if language:
                params["sort_by"] = "popularity.desc"
                params["with_original_language"] = language
                endpoint = f"{self.base_url}/discover/movie"
            else:
                endpoint = f"{self.base_url}/movie/popular"

            response = requests.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs
            for movie in data.get('results', []):
                if movie.get('poster_path'):
                    movie['poster_url'] = self.image_base_url + movie['poster_path']

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_popular_tv_shows(self, page=1, language=None):
        """Get popular TV shows"""
        try:
            params = {"api_key": self.api_key, "page": page}

            # Use discover API if language is specified
            if language:
                params["sort_by"] = "popularity.desc"
                params["with_original_language"] = language
                endpoint = f"{self.base_url}/discover/tv"
            else:
                endpoint = f"{self.base_url}/tv/popular"

            response = requests.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs and content type
            for show in data.get('results', []):
                show['content_type'] = 'tv'
                if show.get('poster_path'):
                    show['poster_url'] = self.image_base_url + show['poster_path']

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_trending_movies(self, time_window='day', language=None, page=1):
        """Get trending movies"""
        try:
            # Use discover API if language is specified, otherwise use trending endpoint
            if language:
                params = {
                    "api_key": self.api_key,
                    "sort_by": "popularity.desc",
                    "with_original_language": language,
                    "page": page
                }
                endpoint = f"{self.base_url}/discover/movie"
            else:
                params = {"api_key": self.api_key, "page": page}
                endpoint = f"{self.base_url}/trending/movie/{time_window}"

            response = requests.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs
            for movie in data.get('results', []):
                if movie.get('poster_path'):
                    movie['poster_url'] = self.image_base_url + movie['poster_path']

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_trending_tv_shows(self, time_window='day', language=None, page=1):
        """Get trending TV shows"""
        try:
            # Use discover API if language is specified, otherwise use trending endpoint
            if language:
                params = {
                    "api_key": self.api_key,
                    "sort_by": "popularity.desc",
                    "with_original_language": language,
                    "page": page
                }
                endpoint = f"{self.base_url}/discover/tv"
            else:
                params = {"api_key": self.api_key, "page": page}
                endpoint = f"{self.base_url}/trending/tv/{time_window}"

            response = requests.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs and content type
            for show in data.get('results', []):
                show['content_type'] = 'tv'
                if show.get('poster_path'):
                    show['poster_url'] = self.image_base_url + show['poster_path']

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_now_playing_movies(self, page=1, language=None):
        """Get now playing movies using discover API with date ranges"""
        try:
            from datetime import datetime, timedelta

            # Calculate date range: last 45 days to today
            today = datetime.now()
            min_date = (today - timedelta(days=45)).strftime('%Y-%m-%d')
            max_date = today.strftime('%Y-%m-%d')

            params = {
                "api_key": self.api_key,
                "page": page,
                "include_adult": "false",
                "include_video": "false",
                "sort_by": "popularity.desc",
                "with_release_type": "2|3",  # Theatrical releases
                "release_date.gte": min_date,
                "release_date.lte": max_date
            }

            if language:
                params["with_original_language"] = language

            response = requests.get(f"{self.base_url}/discover/movie", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs
            for movie in data.get('results', []):
                if movie.get('poster_path'):
                    movie['poster_url'] = self.image_base_url + movie['poster_path']

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_now_playing_tv_shows(self, page=1, language=None):
        """Get now playing (on the air) TV shows"""
        try:
            params = {"api_key": self.api_key, "page": page}

            if language:
                params["with_original_language"] = language

            response = requests.get(f"{self.base_url}/tv/on_the_air", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs and content type
            for show in data.get('results', []):
                show['content_type'] = 'tv'
                if show.get('poster_path'):
                    show['poster_url'] = self.image_base_url + show['poster_path']

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_upcoming_movies(self, page=1, language=None):
        """Get upcoming movies using discover API with date ranges"""
        try:
            from datetime import datetime, timedelta

            # Calculate date range: today to 30 days in the future
            today = datetime.now()
            min_date = today.strftime('%Y-%m-%d')
            max_date = (today + timedelta(days=30)).strftime('%Y-%m-%d')

            params = {
                "api_key": self.api_key,
                "page": page,
                "include_adult": "false",
                "include_video": "false",
                "sort_by": "popularity.desc",
                "with_release_type": "2|3",  # Theatrical releases
                "region": "US",
                "release_date.gte": min_date,
                "release_date.lte": max_date
            }

            if language:
                params["with_original_language"] = language

            response = requests.get(f"{self.base_url}/discover/movie", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs
            for movie in data.get('results', []):
                if movie.get('poster_path'):
                    movie['poster_url'] = self.image_base_url + movie['poster_path']

            return data

        except Exception as e:
            return {"error": str(e), "results": []}

    def get_movie_genres(self):
        """Get list of movie genres"""
        try:
            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/genre/movie/list", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e), "genres": []}

    def get_tv_genres(self):
        """Get list of TV show genres"""
        try:
            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/genre/tv/list", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e), "genres": []}

    def discover_movies_by_genre(self, genre_id, page=1, sort_by='popularity.desc', language=None):
        """Discover movies by genre"""
        try:
            params = {
                "api_key": self.api_key,
                "with_genres": genre_id,
                "page": page,
                "sort_by": sort_by
            }

            # Add language filter if provided
            if language:
                params["with_original_language"] = language

            print(f"DEBUG: Calling TMDB discover/movie with params: {params}")
            response = requests.get(f"{self.base_url}/discover/movie", params=params)
            response.raise_for_status()
            data = response.json()
            print(f"DEBUG: TMDB returned {len(data.get('results', []))} results")

            # Add full poster URLs
            for movie in data.get('results', []):
                if movie.get('poster_path'):
                    movie['poster_url'] = self.image_base_url + movie['poster_path']

            return data
        except Exception as e:
            return {"error": str(e), "results": []}

    def discover_tv_by_genre(self, genre_id, page=1, sort_by='popularity.desc', language=None):
        """Discover TV shows by genre"""
        try:
            params = {
                "api_key": self.api_key,
                "with_genres": genre_id,
                "page": page,
                "sort_by": sort_by
            }

            # Add language filter if provided
            if language:
                params["with_original_language"] = language

            print(f"DEBUG: Calling TMDB discover/tv with params: {params}")
            response = requests.get(f"{self.base_url}/discover/tv", params=params)
            response.raise_for_status()
            data = response.json()
            print(f"DEBUG: TMDB returned {len(data.get('results', []))} results")

            # Add full poster URLs and content type
            for show in data.get('results', []):
                show['content_type'] = 'tv'
                if show.get('poster_path'):
                    show['poster_url'] = self.image_base_url + show['poster_path']

            return data
        except Exception as e:
            return {"error": str(e), "results": []}

    def get_movie_recommendations(self, movie_id, page=1):
        """Get movie recommendations based on a movie"""
        try:
            params = {"api_key": self.api_key, "page": page}
            response = requests.get(f"{self.base_url}/movie/{movie_id}/recommendations", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs
            for movie in data.get('results', []):
                if movie.get('poster_path'):
                    movie['poster_url'] = self.image_base_url + movie['poster_path']

            return data
        except Exception as e:
            return {"error": str(e), "results": []}

    def get_tv_recommendations(self, tv_id, page=1):
        """Get TV show recommendations based on a show"""
        try:
            params = {"api_key": self.api_key, "page": page}
            response = requests.get(f"{self.base_url}/tv/{tv_id}/recommendations", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full poster URLs and content type
            for show in data.get('results', []):
                show['content_type'] = 'tv'
                if show.get('poster_path'):
                    show['poster_url'] = self.image_base_url + show['poster_path']

            return data
        except Exception as e:
            return {"error": str(e), "results": []}

    def get_movie_credits(self, movie_id):
        """Get movie cast and crew with profile images"""
        try:
            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/movie/{movie_id}/credits", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full profile image URLs for cast
            for cast_member in data.get('cast', []):
                if cast_member.get('profile_path'):
                    cast_member['profile_url'] = self.image_base_url.replace('w500', 'w185') + cast_member['profile_path']
                else:
                    cast_member['profile_url'] = None

            return data
        except Exception as e:
            print(f"Error fetching movie credits: {e}")
            return {"error": str(e), "cast": [], "crew": []}

    def get_tv_credits(self, tv_id):
        """Get TV show cast and crew with profile images"""
        try:
            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/tv/{tv_id}/credits", params=params)
            response.raise_for_status()
            data = response.json()

            # Add full profile image URLs for cast
            for cast_member in data.get('cast', []):
                if cast_member.get('profile_path'):
                    cast_member['profile_url'] = self.image_base_url.replace('w500', 'w185') + cast_member['profile_path']
                else:
                    cast_member['profile_url'] = None

            return data
        except Exception as e:
            print(f"Error fetching TV credits: {e}")
            return {"error": str(e), "cast": [], "crew": []}

tmdb = TMDBService()

class SubtitleService:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.user_agent})

    def search_subtitles(self, imdb_id=None, query=None, season=None, episode=None, language="eng"):
        """Search for subtitles using multiple sources"""

        # Map language codes to full names
        lang_map = {
            'eng': 'English', 'spa': 'Spanish', 'fre': 'French', 'ger': 'German',
            'ita': 'Italian', 'por': 'Portuguese', 'ara': 'Arabic', 'chi': 'Chinese',
            'jpn': 'Japanese', 'kor': 'Korean', 'rus': 'Russian', 'hin': 'Hindi'
        }

        language_name = lang_map.get(language, 'English')
        subtitles = []

        # Try OpenSubtitles.com API (newer version)
        try:
            subtitles.extend(self.search_opensubtitles_com(imdb_id, query, season, episode, language))
        except Exception as e:
            print(f"OpenSubtitles.com search error: {e}")

        # Try generating subtitle links from known sources
        if imdb_id and not subtitles:
            subtitles.extend(self.generate_subtitle_links(imdb_id, query, season, episode, language, language_name))

        return subtitles[:10]  # Return top 10

    def search_opensubtitles_com(self, imdb_id, query, season, episode, language):
        """Try OpenSubtitles.com (newer API)"""
        try:
            # OpenSubtitles.com has a different endpoint
            base_url = "https://www.opensubtitles.com/api/v1/subtitles"

            # This is a fallback - real implementation would need API key
            # For now, return empty to use generated links
            return []

        except Exception as e:
            print(f"OpenSubtitles.com error: {e}")
            return []

    def generate_subtitle_links(self, imdb_id, title, season, episode, lang_code, lang_name):
        """Generate subtitle links from known subtitle sources"""
        subtitles = []

        # Clean IMDB ID
        clean_imdb = imdb_id.replace('tt', '') if imdb_id else ''

        # Generate links to popular subtitle sites
        if clean_imdb:
            # YIFY Subtitles search page
            subtitles.append({
                'SubFileName': f'{title or "Movie"} - {lang_name} - YIFY Subtitles',
                'SubDownloadLink': f'https://yifysubtitles.ch/movie-imdb/tt{clean_imdb}',
                'SubDownloadsCnt': '5000',
                'SubRating': '8.0',
                'LanguageName': lang_name,
                'SubFormat': 'srt',
                'Source': 'YIFY',
                'IsSearchPage': True
            })

            # OpenSubtitles.org direct link pattern
            subtitles.append({
                'SubFileName': f'{title or "Movie"} - {lang_name} - OpenSubtitles.srt',
                'SubDownloadLink': f'https://www.opensubtitles.org/en/search/imdbid-{clean_imdb}/sublanguageid-{lang_code}',
                'SubDownloadsCnt': '10000',
                'SubRating': '9.0',
                'LanguageName': lang_name,
                'SubFormat': 'srt',
                'Source': 'OpenSubtitles.org',
                'IsSearchPage': True  # This is a search page, not direct download
            })

            # Subscene pattern
            subtitles.append({
                'SubFileName': f'{title or "Movie"} - {lang_name} - Subscene.srt',
                'SubDownloadLink': f'https://subscene.com/subtitles/title?q={clean_imdb}',
                'SubDownloadsCnt': '8000',
                'SubRating': '8.5',
                'LanguageName': lang_name,
                'SubFormat': 'srt',
                'Source': 'Subscene',
                'IsSearchPage': True
            })

        # For TV shows
        if season and episode:
            subtitles.append({
                'SubFileName': f'{title or "TV Show"} S{season:02d}E{episode:02d} - {lang_name}.srt',
                'SubDownloadLink': f'https://www.opensubtitles.org/en/search/imdbid-{clean_imdb}/season-{season}/episode-{episode}',
                'SubDownloadsCnt': '5000',
                'SubRating': '8.0',
                'LanguageName': lang_name,
                'SubFormat': 'srt',
                'Source': 'OpenSubtitles.org',
                'IsSearchPage': True
            })

        return subtitles

    def get_subtitle_file(self, subtitle_url):
        """Download and decompress subtitle file"""
        try:
            response = requests.get(subtitle_url, timeout=30)
            response.raise_for_status()

            # OpenSubtitles files are gzipped
            if subtitle_url.endswith('.gz'):
                decompressed = gzip.decompress(response.content)
                return decompressed.decode('utf-8', errors='ignore')
            else:
                return response.content.decode('utf-8', errors='ignore')

        except Exception as e:
            print(f"Subtitle download error: {e}")
            return None

    def search_alternative_subtitles(self, title, year=None, season=None, episode=None):
        """Search using alternative subtitle sources"""
        try:
            subtitles = []

            # Just return the generated links since we can't access most subtitle APIs without keys
            # Users can click to open search pages and manually download

            return subtitles

        except Exception as e:
            print(f"Alternative subtitle search error: {e}")
            return []

    def search_opensub_api(self, imdb_id, query, season, episode, language):
        """
        Search OpenSubtitles using their REST API
        Note: This requires an API key for production use
        Free tier is limited - mainly provides search page links
        """
        try:
            # For production, you would:
            # 1. Register at https://www.opensubtitles.com/api
            # 2. Get an API key
            # 3. Use the v1 API with authentication

            # For now, we provide direct search page links
            return []

        except Exception as e:
            print(f"OpenSub API error: {e}")
            return []

subtitle_service = SubtitleService()

@app.route('/')
def index():
    """Serve the main movie streaming interface"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return send_file(os.path.join(current_dir, 'index.html'))
    except:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return send_file(os.path.join(current_dir, 'movie_search_player.html'))

@app.route('/favicon.ico')
def favicon():
    """Return empty response for favicon to prevent 404 errors"""
    return '', 204

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files (images, JS, CSS) from the root directory"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(current_dir, filename)

@app.route('/api/search')
def search_movies():
    """Search for movies"""
    query = request.args.get('query', '').strip()
    year = request.args.get('year')
    page = request.args.get('page', 1, type=int)

    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    try:
        year = int(year) if year else None
    except ValueError:
        year = None

    results = tmdb.search_movies(query, year, page)
    return jsonify(results)

def get_omdb_episode_ratings(imdb_id):
    """Fetch only IMDb ratings from OMDB API for episodes (lightweight)"""
    # Check cache first
    if imdb_id in omdb_cache:
        return omdb_cache[imdb_id]

    try:
        params = {
            "apikey": OMDB_API_KEY,
            "i": imdb_id
        }
        response = requests.get("https://www.omdbapi.com/", params=params, timeout=5)
        response.raise_for_status()
        data = response.json()

        if data.get('Response') == 'True':
            result = {
                'omdb_imdb_rating': data.get('imdbRating'),
                'omdb_ratings': data.get('Ratings', []),
            }
            # Cache the result
            omdb_cache[imdb_id] = result
            save_cache(OMDB_CACHE_FILE, omdb_cache)  # Save immediately
            return result
    except Exception as e:
        print(f"OMDB fetch error: {e}")

    # Cache empty result to avoid repeated failed requests
    omdb_cache[imdb_id] = {}
    save_cache(OMDB_CACHE_FILE, omdb_cache)  # Save immediately
    return {}

def get_omdb_data(imdb_id):
    """Fetch additional data from OMDB API"""
    # Check cache first
    if imdb_id in omdb_cache:
        return omdb_cache[imdb_id]

    try:
        params = {
            "apikey": OMDB_API_KEY,
            "i": imdb_id,
            "plot": "full"
        }
        response = requests.get("https://www.omdbapi.com/", params=params, timeout=5)
        response.raise_for_status()
        data = response.json()

        if data.get('Response') == 'True':
            result = {
                'omdb_plot': data.get('Plot'),
                'omdb_rated': data.get('Rated'),
                'omdb_runtime': data.get('Runtime'),
                'omdb_genre': data.get('Genre'),
                'omdb_director': data.get('Director'),
                'omdb_writer': data.get('Writer'),
                'omdb_actors': data.get('Actors'),
                'omdb_language': data.get('Language'),
                'omdb_country': data.get('Country'),
                'omdb_awards': data.get('Awards'),
                'omdb_ratings': data.get('Ratings', []),
                'omdb_metascore': data.get('Metascore'),
                'omdb_imdb_rating': data.get('imdbRating'),
                'omdb_imdb_votes': data.get('imdbVotes'),
                'omdb_box_office': data.get('BoxOffice'),
            }
            # Cache the result
            omdb_cache[imdb_id] = result
            save_cache(OMDB_CACHE_FILE, omdb_cache)  # Save immediately
            return result
    except Exception as e:
        print(f"OMDB fetch error: {e}")

    # Cache empty result to avoid repeated failed requests
    omdb_cache[imdb_id] = {}
    save_cache(OMDB_CACHE_FILE, omdb_cache)  # Save immediately
    return {}

@app.route('/api/omdb/<imdb_id>', methods=['GET'])
def api_omdb_lookup(imdb_id):
    data = get_omdb_data(imdb_id)
    return jsonify(data), 200

# ---- Friends list (user's accepted friends) ----
## Duplicate route removed - using the one at line 3037 instead
# @app.route('/api/friends', methods=['GET'])
# @jwt_required(optional=True)
# def api_friends():
#     identity = get_jwt_identity()
#     if not identity:
#         return jsonify({'friends': []}), 200
#     u = _identity_to_user(identity)
#     if not u or not u.get('username'):
#         return jsonify({'friends': []}), 200
#     # ensure we have _id
#     user_doc = users_collection.find_one({'username': u['username']}, {'_id': 1, 'username': 1})
#     if not user_doc:
#         return jsonify({'friends': []}), 200
#     usernames = list(_get_friends_usernames_for(user_doc['_id']) or [])
#     return jsonify({'friends': usernames}), 200

# ---- Ratings (get & post) ----
## Duplicate routes removed - using the ones at lines 1139-1180 instead
# @app.route('/api/ratings/<content_type>/<int:tmdb_id>', methods=['GET'])
# @jwt_required(optional=True)
# def api_get_ratings(content_type, tmdb_id):
#     key = _content_key(content_type, tmdb_id)
#     cur = ratings_collection.find({'content_key': key}, {'_id': 0, 'username': 1, 'rating': 1})
#     ratings_by_user = {}
#     for d in cur:
#         try:
#             u = d.get('username')
#             v = float(d.get('rating')) if d.get('rating') is not None else None
#             if u and v is not None:
#                 ratings_by_user[u] = v
#         except Exception:
#             continue
#     return jsonify({'content_key': key, 'ratings_by_user': ratings_by_user}), 200
#
# @app.route('/api/ratings/<content_type>/<int:tmdb_id>', methods=['POST'])
# @jwt_required()
# def api_post_rating(content_type, tmdb_id):
#     identity = get_jwt_identity()
#     u = _identity_to_user(identity)
#     if not u or not u.get('username'):
#         return jsonify({'error': 'Unauthorized'}), 401
#     user_doc = users_collection.find_one({'username': u['username']}, {'_id': 1, 'username': 1})
#     if not user_doc:
#         return jsonify({'error': 'Unauthorized'}), 401
#
#     payload = request.get_json(silent=True) or {}
#     try:
#         rating = float(payload.get('rating'))
#     except Exception:
#         return jsonify({'error': 'Invalid rating'}), 400
#     if not (1.0 <= rating <= 10.0):
#         return jsonify({'error': 'Rating must be between 1 and 10'}), 400
#
#     key = _content_key(content_type, tmdb_id)
#     ratings_collection.update_one(
#         {'content_key': key, 'user_id': user_doc['_id']},
#         {'$set': {
#             'username': user_doc['username'],
#             'rating': rating,
#             'updated_at': datetime.utcnow()
#         }},
#         upsert=True
#     )
#
#     doc = ratings_collection.find_one(
#         {'content_key': key, 'user_id': user_doc['_id']},
#         {'_id': 0, 'username': 1, 'rating': 1}
#     )
#     return jsonify({'ok': True, 'rating': doc}), 200

# ==== Ratings & Friends (MongoDB) =============================================

def _content_key(content_type, tmdb_id):
    return f"{'tv' if content_type == 'tv' else 'movie'}:{int(tmdb_id)}"

def _identity_to_user(identity):
    """
    Resolve a user doc from JWT identity. Handles:
    - identity as string ObjectId
    - identity as plain username string
    - identity as dict with 'id'|'_id'|'user_id' or 'username'
    """
    try:
        if isinstance(identity, dict):
            uid = identity.get('id') or identity.get('_id') or identity.get('user_id')
            uname = identity.get('username') or identity.get('name')
            if uid:
                try:
                    udoc = users_collection.find_one({"_id": ObjectId(str(uid))}, {"username": 1})
                    if udoc:
                        return udoc
                except Exception:
                    pass
            if uname:
                udoc = users_collection.find_one({"username": uname}, {"username": 1})
                if udoc:
                    return udoc
        elif isinstance(identity, str):
            # Try ObjectId then fallback to username
            try:
                udoc = users_collection.find_one({"_id": ObjectId(identity)}, {"username": 1})
                if udoc:
                    return udoc
            except Exception:
                pass
            udoc = users_collection.find_one({"username": identity}, {"username": 1})
            if udoc:
                return udoc
    except Exception:
        pass
    return None

def _get_friends_usernames_for(user_id: ObjectId):
    """
    Build friend list from friend_requests_collection with status 'accepted'.
    Returns a list of usernames (strings).
    """
    friends = set()
    try:
        accepted = friend_requests_collection.find(
            {
                "status": "accepted",
                "$or": [{"requester_id": user_id}, {"receiver_id": user_id}]
            },
            {"requester_id": 1, "receiver_id": 1}
        )
        for fr in accepted:
            other_id = fr["receiver_id"] if fr.get("requester_id") == user_id else fr.get("requester_id")
            if other_id:
                u = users_collection.find_one({"_id": other_id}, {"username": 1})
                if u and u.get("username"):
                    friends.add(u["username"])
    except Exception as e:
        print(f"friends lookup error: {e}")
    return list(friends)

# ---------- Ratings ----------
@app.route('/api/ratings/<content_type>/<int:tmdb_id>', methods=['GET'])
def get_ratings(content_type, tmdb_id):
    key = _content_key(content_type, tmdb_id)
    print(f"[Ratings] GET {content_type}/{tmdb_id}, key: {key}")
    cursor = ratings_collection.find({"content_key": key}, {"username": 1, "rating": 1})
    ratings_by_user = {doc["username"]: float(doc["rating"]) for doc in cursor if "username" in doc and "rating" in doc}
    print(f"[Ratings] Found {len(ratings_by_user)} ratings: {ratings_by_user}")
    values = list(ratings_by_user.values())
    avg = round(sum(values) / len(values), 2) if values else None
    return jsonify({
        "ratings_by_user": ratings_by_user,
        "average": avg,
        "count": len(values)
    })

from flask_jwt_extended import jwt_required, get_jwt_identity

@app.route('/api/ratings/<content_type>/<int:tmdb_id>', methods=['POST'])
@jwt_required()
def post_rating(content_type, tmdb_id):
    identity = get_jwt_identity()
    udoc = _identity_to_user(identity)
    if not udoc:
        return jsonify({"error": "User not found"}), 401

    data = request.get_json(silent=True) or {}
    try:
        rating = float(data.get('rating'))
    except Exception:
        return jsonify({"error": "Invalid rating"}), 400
    if not (1.0 <= rating <= 10.0):
        return jsonify({"error": "Rating must be between 1 and 10"}), 400

    key = _content_key(content_type, tmdb_id)
    now = datetime.utcnow().isoformat()

    print(f"[Ratings] POST {content_type}/{tmdb_id} by {udoc['username']}: rating={rating}")
    print(f"[Ratings] Updating with key={key}, user_id={udoc['_id']}")

    result = ratings_collection.update_one(
        {"content_key": key, "user_id": udoc["_id"]},
        {"$set": {"username": udoc["username"], "rating": round(rating, 1), "updated_at": now}},
        upsert=True
    )

    print(f"[Ratings] Update result: matched={result.matched_count}, modified={result.modified_count}, upserted_id={result.upserted_id}")

    # Return fresh aggregate
    return get_ratings(content_type, tmdb_id)

# ---------- Friends ----------
## Duplicate route removed - using the one at line 3037 instead
# @app.route('/api/friends', methods=['GET'])
# @jwt_required()
# def get_friends_me():
#     identity = get_jwt_identity()
#     udoc = _identity_to_user(identity)
#     if not udoc:
#         return jsonify({"error": "User not found"}), 401
#     friends = _get_friends_usernames_for(udoc["_id"])
#     return jsonify({"friends": [{"username": u} for u in friends]})



@app.route('/api/movie/<int:movie_id>')
def get_movie_details(movie_id):
    """Get detailed movie information with OMDB enhancement and cast"""
    movie = tmdb.get_movie_details(movie_id)

    # Fetch OMDB data if we have an IMDb ID
    imdb_id = movie.get('external_ids', {}).get('imdb_id')
    print(f"Movie ID: {movie_id}, IMDb ID: {imdb_id}")

    if imdb_id:
        omdb_data = get_omdb_data(imdb_id)
        print(f"OMDB data received: {omdb_data}")
        movie.update(omdb_data)

    # Fetch cast and crew data from TMDB
    credits = tmdb.get_movie_credits(movie_id)
    if 'cast' in credits:
        movie['tmdb_cast'] = credits['cast'][:5]  # Top 5 cast members
    if 'crew' in credits:
        movie['tmdb_crew'] = credits['crew']

    return jsonify(movie)



@app.route('/api/popular')
def get_popular_movies():
    """Get popular movies"""
    page = request.args.get('page', 1, type=int)
    language = request.args.get('language')
    results = tmdb.get_popular_movies(page, language)
    return jsonify(results)

@app.route('/api/trending')
def get_trending_movies():
    """Get trending movies"""
    time_window = request.args.get('time_window', 'day')
    if time_window not in ['day', 'week']:
        time_window = 'day'
    language = request.args.get('language')
    page = request.args.get('page', 1, type=int)

    results = tmdb.get_trending_movies(time_window, language, page)
    return jsonify(results)

# TV Show Endpoints

@app.route('/api/tv/search')
def search_tv_shows():
    """Search for TV shows"""
    query = request.args.get('query', '').strip()
    year = request.args.get('year')
    page = request.args.get('page', 1, type=int)

    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    try:
        year = int(year) if year else None
    except ValueError:
        year = None

    results = tmdb.search_tv_shows(query, year, page)
    return jsonify(results)

@app.route('/api/search/multi')
def search_multi():
    """Search for both movies and TV shows"""
    query = request.args.get('query', '').strip()
    page = request.args.get('page', 1, type=int)

    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    results = tmdb.search_multi(query, page)
    return jsonify(results)

@app.route('/api/tv/<int:tv_id>')
def get_tv_details(tv_id):
    """Get detailed TV show information with OMDB enhancement and cast"""
    show = tmdb.get_tv_details(tv_id)

    # Fetch OMDB data if we have an IMDb ID
    imdb_id = show.get('external_ids', {}).get('imdb_id')
    print(f"TV ID: {tv_id}, IMDb ID: {imdb_id}")

    if imdb_id:
        omdb_data = get_omdb_data(imdb_id)
        print(f"OMDB data received: {omdb_data}")
        show.update(omdb_data)

    # Fetch cast and crew data from TMDB
    credits = tmdb.get_tv_credits(tv_id)
    if 'cast' in credits:
        show['tmdb_cast'] = credits['cast'][:5]  # Top 5 cast members
    if 'crew' in credits:
        show['tmdb_crew'] = credits['crew']

    return jsonify(show)

@app.route('/api/tv/<int:tv_id>/season/<int:season_number>')
def get_tv_season_details(tv_id, season_number):
    """Get detailed season information"""
    # Support quick mode without ratings
    quick = request.args.get('quick', 'false').lower() == 'true'
    include_ratings = not quick
    season = tmdb.get_tv_season_details(tv_id, season_number, include_ratings)
    return jsonify(season)

@app.route('/api/tv/<int:tv_id>/season/<int:season_number>/episode/<int:episode_number>/rating')
def get_episode_rating(tv_id, season_number, episode_number):
    """Get IMDB rating for a specific episode"""
    try:
        external_ids = tmdb.get_tv_episode_external_ids(tv_id, season_number, episode_number)
        if external_ids.get('imdb_id'):
            omdb_data = get_omdb_episode_ratings(external_ids['imdb_id'])
            return jsonify({
                'episode_number': episode_number,
                'imdb_rating': omdb_data.get('omdb_imdb_rating', 'N/A'),
                'ratings': omdb_data.get('omdb_ratings', [])
            })
        return jsonify({'episode_number': episode_number, 'imdb_rating': 'N/A', 'ratings': []})
    except Exception as e:
        return jsonify({'error': str(e), 'episode_number': episode_number}), 500

@app.route('/api/tv/popular')
def get_popular_tv_shows():
    """Get popular TV shows"""
    page = request.args.get('page', 1, type=int)
    language = request.args.get('language')
    results = tmdb.get_popular_tv_shows(page, language)
    return jsonify(results)

@app.route('/api/tv/trending')
def get_trending_tv_shows():
    """Get trending TV shows"""
    time_window = request.args.get('time_window', 'day')
    if time_window not in ['day', 'week']:
        time_window = 'day'
    language = request.args.get('language')
    page = request.args.get('page', 1, type=int)

    results = tmdb.get_trending_tv_shows(time_window, language, page)
    return jsonify(results)

@app.route('/api/movies/now-playing')
def get_now_playing_movies():
    """Get now playing movies"""
    page = request.args.get('page', 1, type=int)
    language = request.args.get('language')
    results = tmdb.get_now_playing_movies(page, language)
    return jsonify(results)

@app.route('/api/tv/now-playing')
def get_now_playing_tv_shows_route():
    """Get now playing (on the air) TV shows"""
    page = request.args.get('page', 1, type=int)
    language = request.args.get('language')
    results = tmdb.get_now_playing_tv_shows(page, language)
    return jsonify(results)

@app.route('/api/movies/upcoming')
def get_upcoming_movies_route():
    """Get upcoming movies"""
    page = request.args.get('page', 1, type=int)
    language = request.args.get('language')
    results = tmdb.get_upcoming_movies(page, language)
    return jsonify(results)

@app.route('/api/genres/movies')
def get_movie_genres():
    """Get list of movie genres"""
    results = tmdb.get_movie_genres()
    return jsonify(results)

@app.route('/api/genres/tv')
def get_tv_genres():
    """Get list of TV genres"""
    results = tmdb.get_tv_genres()
    return jsonify(results)

@app.route('/api/discover/movies')
def discover_movies():
    """Discover movies by genre"""
    genre_id = request.args.get('genre', type=int)
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort_by', 'popularity.desc')
    language = request.args.get('language')

    print(f"DEBUG Flask route: genre={genre_id}, page={page}, language={language}")

    if not genre_id:
        return jsonify({"error": "Genre ID is required"}), 400

    results = tmdb.discover_movies_by_genre(genre_id, page, sort_by, language)
    return jsonify(results)

@app.route('/api/discover/tv')
def discover_tv():
    """Discover TV shows by genre"""
    genre_id = request.args.get('genre', type=int)
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort_by', 'popularity.desc')
    language = request.args.get('language')

    print(f"DEBUG Flask route: genre={genre_id}, page={page}, language={language}")

    if not genre_id:
        return jsonify({"error": "Genre ID is required"}), 400

    results = tmdb.discover_tv_by_genre(genre_id, page, sort_by, language)
    return jsonify(results)

@app.route('/api/recommendations/movies/<int:movie_id>')
def get_movie_recommendations(movie_id):
    """Get movie recommendations"""
    page = request.args.get('page', 1, type=int)
    results = tmdb.get_movie_recommendations(movie_id, page)
    return jsonify(results)

@app.route('/api/recommendations/tv/<int:tv_id>')
def get_tv_recommendations(tv_id):
    """Get TV recommendations"""
    page = request.args.get('page', 1, type=int)
    results = tmdb.get_tv_recommendations(tv_id, page)
    return jsonify(results)

def extract_bingeflix_stream(tmdb_id, content_type='movie', season=None, episode=None):
    """
    Extract m3u8 stream from bingeflix.tv via standalone Playwright subprocess.
    Returns dict with type/url/source/subtitles or None on failure.
    """
    cache_key = f"{tmdb_id}_{content_type}_{season}_{episode}"

    # Check cache first
    cached = bingeflix_cache.get(cache_key)
    if cached and (time.time() - cached['timestamp']) < BINGEFLIX_CACHE_TTL:
        print(f"[Bingeflix] Cache hit for {cache_key}")
        return cached['data']

    # Try to acquire semaphore (non-blocking) - only one browser at a time
    if not bingeflix_lock.acquire(blocking=False):
        print(f"[Bingeflix] Another scrape in progress, skipping")
        return None

    try:
        # Build subprocess command
        cmd = [sys.executable, 'bingeflix_scraper.py', str(tmdb_id), content_type]
        if content_type == 'tv' and season and episode:
            cmd.extend([str(season), str(episode)])

        print(f"[Bingeflix] Running: {' '.join(cmd)}")

        # Run subprocess via tpool to avoid blocking eventlet
        result = eventlet.tpool.execute(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )

        # Log stderr (debug output from scraper)
        if result.stderr:
            for line in result.stderr.strip().split('\n'):
                print(f"[Bingeflix] {line}")

        if result.returncode != 0:
            print(f"[Bingeflix] Scraper exited with code {result.returncode}")
            return None

        # Parse JSON from stdout
        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError as e:
            print(f"[Bingeflix] Failed to parse JSON output: {e}")
            print(f"[Bingeflix] stdout was: {result.stdout[:500]}")
            return None

        if not data.get('success') or not data.get('hls_url'):
            print(f"[Bingeflix] Scraper failed: {data.get('error', 'unknown')}")
            return None

        hls_url = data['hls_url']
        hls_content = data.get('hls_content')
        referer = data.get('referer', 'https://bingeflix.tv/')
        subtitles = data.get('subtitles', [])

        print(f"[Bingeflix] Found stream: {hls_url}")
        print(f"[Bingeflix] Referer: {referer}")
        print(f"[Bingeflix] Subtitles: {len(subtitles)} tracks")
        print(f"[Bingeflix] Has m3u8 content: {hls_content is not None}")

        if hls_content:
            # Cache the m3u8 content and serve it directly (avoids CDN re-fetch/redirect issues)
            stream_id = f"{tmdb_id}_{content_type}_{season}_{episode}"
            base_url = hls_url.rsplit('/', 1)[0] + '/'
            bingeflix_m3u8_cache[stream_id] = {
                'content': hls_content,
                'referer': referer,
                'base_url': base_url,
                'timestamp': time.time()
            }
            proxied_url = f"/api/hls/bingeflix/{quote(stream_id, safe='')}"
            print(f"[Bingeflix] Serving cached m3u8 via {proxied_url}")
        else:
            # Fallback to standard proxy if we couldn't capture content
            proxied_url = f"/api/hls/proxy?url={quote(hls_url, safe='')}&referer={quote(referer, safe='')}"

        stream_data = {
            'type': 'direct',
            'url': proxied_url,
            'source': 'bingeflix',
            'subtitles': subtitles,
            'original_url': hls_url,
            'referer': referer
        }

        # Cache successful result
        bingeflix_cache[cache_key] = {
            'data': stream_data,
            'timestamp': time.time()
        }

        return stream_data

    except subprocess.TimeoutExpired:
        print(f"[Bingeflix] Scraper timed out (90s)")
        return None
    except Exception as e:
        print(f"[Bingeflix] Error: {e}")
        return None
    finally:
        bingeflix_lock.release()


def call_vidsrc_scraper(tmdb_id, content_type='movie', season=None, episode=None):
    """
    Call the vidsrc-scraper Node.js service (Playwright-based) to get m3u8 streams.
    Returns dict with type/url/source/subtitles or None on failure.
    """
    try:
        params = {'tmdb_id': tmdb_id, 'type': content_type}
        if content_type == 'tv':
            params['season'] = season
            params['episode'] = episode

        print(f"[VidSrc Scraper] Calling scraper: {VIDSRC_SCRAPER_URL}/extract with params={params}")
        resp = requests.get(f"{VIDSRC_SCRAPER_URL}/extract", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        if not data.get('success') or not data.get('results'):
            print(f"[VidSrc Scraper] No successful results from scraper")
            return None

        # Iterate domains, pick first with a valid hls_url
        for domain, info in data['results'].items():
            hls_url = info.get('hls_url')
            if hls_url:
                subtitles = info.get('subtitles', [])
                referer = info.get('referer', 'https://vidnest.fun/')
                print(f"[VidSrc Scraper] Found stream from {domain}: {hls_url}")
                print(f"[VidSrc Scraper] Subtitles: {len(subtitles)} tracks")
                print(f"[VidSrc Scraper] Referer: {referer}")

                # Create proxied URL to handle Referer header
                proxied_url = f"/api/hls/proxy?url={quote(hls_url, safe='')}&referer={quote(referer, safe='')}"

                return {
                    'type': 'direct',
                    'url': proxied_url,
                    'source': domain,
                    'subtitles': subtitles,
                    'original_url': hls_url,  # Keep original for debugging
                    'referer': referer
                }

        print(f"[VidSrc Scraper] No domains returned a valid hls_url")
        return None

    except requests.exceptions.ConnectionError:
        print(f"[VidSrc Scraper] Scraper service not running at {VIDSRC_SCRAPER_URL}")
        return None
    except requests.exceptions.Timeout:
        print(f"[VidSrc Scraper] Scraper request timed out (60s)")
        return None
    except Exception as e:
        print(f"[VidSrc Scraper] Error: {e}")
        return None


def extract_vidsrc_stream(tmdb_id, content_type='movie', season=None, episode=None):
    """
    Extract direct stream URL from VidSrc
    Tries Bingeflix first, then Playwright scraper, then BeautifulSoup, then embed fallback.
    """
    # Tier 0: Try Bingeflix scraper (local Playwright subprocess)
    bingeflix_result = extract_bingeflix_stream(tmdb_id, content_type, season, episode)
    if bingeflix_result:
        return bingeflix_result

    # Tier 1: Try Playwright-based scraper service
    scraper_result = call_vidsrc_scraper(tmdb_id, content_type, season, episode)
    if scraper_result:
        return scraper_result

    # Tier 2: Fall back to BeautifulSoup extraction
    try:
        from bs4 import BeautifulSoup

        # Build vidsrc.to URL
        if content_type == 'movie':
            vidsrc_url = f"https://vidsrc.to/embed/movie/{tmdb_id}"
        else:
            vidsrc_url = f"https://vidsrc.to/embed/tv/{tmdb_id}/{season}/{episode}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://vidsrc.to/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        print(f"[Stream Extractor] Fetching: {vidsrc_url}")

        # Fetch the embed page
        response = requests.get(vidsrc_url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse the HTML
        soup = BeautifulSoup(response.content, 'html.parser')

        # Look for data-id attribute (vidsrc.to uses this)
        data_id = None
        iframe = soup.find('iframe', {'id': 'player_iframe'})

        if iframe and iframe.get('src'):
            iframe_src = iframe.get('src')
            print(f"[Stream Extractor] Found iframe src: {iframe_src}")

            # If it's a relative URL, make it absolute
            if iframe_src.startswith('/'):
                iframe_src = f"https://vidsrc.to{iframe_src}"

            # Try to fetch the iframe source
            try:
                iframe_response = requests.get(iframe_src, headers=headers, timeout=10)
                iframe_soup = BeautifulSoup(iframe_response.content, 'html.parser')

                # Look for .m3u8 URLs in scripts or sources
                scripts = iframe_soup.find_all('script')
                for script in scripts:
                    if script.string:
                        # Look for .m3u8 URLs
                        m3u8_matches = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', script.string)
                        if m3u8_matches:
                            m3u8_url = m3u8_matches[0]
                            print(f"[Stream Extractor] Found m3u8 URL: {m3u8_url}")
                            return {
                                'type': 'direct',
                                'url': m3u8_url,
                                'source': 'vidsrc.to'
                            }

                # Look for source tags
                video_source = iframe_soup.find('source', {'type': 'application/x-mpegURL'}) or \
                              iframe_soup.find('source', {'src': re.compile(r'\.m3u8')})

                if video_source and video_source.get('src'):
                    m3u8_url = video_source.get('src')
                    print(f"[Stream Extractor] Found m3u8 in source tag: {m3u8_url}")
                    return {
                        'type': 'direct',
                        'url': m3u8_url,
                        'source': 'vidsrc.to'
                    }

            except Exception as iframe_error:
                print(f"[Stream Extractor] Error fetching iframe: {iframe_error}")

        # Fallback: return embed URL
        print(f"[Stream Extractor] Could not extract direct stream, using embed URL")
        return {
            'type': 'embed',
            'url': vidsrc_url,
            'source': 'vidsrc.to'
        }

    except Exception as e:
        print(f"[Stream Extractor] Error: {e}")
        # Return embed URL as absolute fallback
        if content_type == 'movie':
            fallback_url = f"https://vidsrc.to/embed/movie/{tmdb_id}"
        else:
            fallback_url = f"https://vidsrc.to/embed/tv/{tmdb_id}/{season}/{episode}"

        return {
            'type': 'embed',
            'url': fallback_url,
            'source': 'vidsrc.to'
        }

@app.route('/api/stream/<int:movie_id>')
def generate_stream_url(movie_id):
    """Generate streaming URL with direct stream support"""
    try:
        # Get movie details first
        movie = tmdb.get_movie_details(movie_id)
        if 'error' in movie:
            return jsonify(movie), 404

        # Fallback URLs
        vidking_url = f"https://www.vidking.net/embed/movie/{movie_id}"
        vidsrc_url = f"https://vidsrc.to/embed/movie/{movie_id}"

        # Try to get direct stream
        stream_info = extract_vidsrc_stream(movie_id, 'movie')

        # Defensive: ensure stream_info has the expected structure
        subtitles = []
        if stream_info and isinstance(stream_info, dict):
            stream_url = stream_info.get('url', vidsrc_url)
            stream_type = stream_info.get('type', 'embed')
            subtitles = stream_info.get('subtitles', [])
            print(f"[API] Movie {movie_id} - Stream URL: {stream_url}, Type: {stream_type}")
        else:
            print(f"[API] Movie {movie_id} - No stream info, using fallback")
            stream_url = vidsrc_url
            stream_type = 'embed'

        return jsonify({
            "movie_id": movie_id,
            "title": movie.get('title'),
            "year": movie.get('release_date', '')[:4] if movie.get('release_date') else None,
            "stream_url": stream_url,
            "stream_type": stream_type,
            "subtitles": subtitles,
            "vidking_url": vidking_url,
            "vidsrc_url": vidsrc_url,
            "movie_details": movie
        })

    except Exception as e:
        print(f"[API] Error in generate_stream_url: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tv/stream/<int:tv_id>')
def generate_tv_stream_url(tv_id):
    """Generate streaming URL for TV show with direct stream support"""
    season = request.args.get('season', 1, type=int)
    episode = request.args.get('episode', 1, type=int)

    try:
        # Get TV show details first
        show = tmdb.get_tv_details(tv_id)
        if 'error' in show:
            return jsonify(show), 404

        # Fallback URLs
        vidking_url = f"https://www.vidking.net/embed/tv/{tv_id}/{season}/{episode}"
        vidsrc_url = f"https://vidsrc.to/embed/tv/{tv_id}/{season}/{episode}"

        # Try to get direct stream
        stream_info = extract_vidsrc_stream(tv_id, 'tv', season, episode)

        # Defensive: ensure stream_info has the expected structure
        subtitles = []
        if stream_info and isinstance(stream_info, dict):
            stream_url = stream_info.get('url', vidsrc_url)
            stream_type = stream_info.get('type', 'embed')
            subtitles = stream_info.get('subtitles', [])
            print(f"[API] TV {tv_id} S{season}E{episode} - Stream URL: {stream_url}, Type: {stream_type}")
        else:
            print(f"[API] TV {tv_id} S{season}E{episode} - No stream info, using fallback")
            stream_url = vidsrc_url
            stream_type = 'embed'

        return jsonify({
            "tv_id": tv_id,
            "title": show.get('name'),
            "year": show.get('first_air_date', '')[:4] if show.get('first_air_date') else None,
            "season": season,
            "episode": episode,
            "stream_url": stream_url,
            "stream_type": stream_type,
            "subtitles": subtitles,
            "vidking_url": vidking_url,
            "vidsrc_url": vidsrc_url,
            "show_details": show
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/search-and-stream')
def search_and_stream():
    """Search for a movie and return streaming URL"""
    query = request.args.get('query', '').strip()
    year = request.args.get('year')

    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    try:
        year = int(year) if year else None
    except ValueError:
        year = None

    # Search for movies
    search_results = tmdb.search_movies(query, year)

    if 'error' in search_results or not search_results.get('results'):
        return jsonify({"error": "No movies found"}), 404

    # Get the first/best match
    movie = search_results['results'][0]

    # Try to find exact title match
    for m in search_results['results']:
        if m.get('title', '').lower() == query.lower():
            if not year or (m.get('release_date', '').startswith(str(year))):
                movie = m
                break

    movie_id = movie['id']
    vidking_url = f"https://www.vidking.net/embed/movie/{movie_id}"

    return jsonify({
        "search_query": query,
        "search_year": year,
        "movie_id": movie_id,
        "title": movie.get('title'),
        "year": movie.get('release_date', '')[:4] if movie.get('release_date') else None,
        "overview": movie.get('overview'),
        "poster_url": movie.get('poster_url'),
        "vidking_url": vidking_url,
        "vidking_autoplay_url": f"{vidking_url}?color=9146ff&autoPlay=true&quality=720p&sub.default=en",
        "all_results": search_results['results'][:5]  # Return top 5 matches
    })

# HTML file serving routes
@app.route('/api-docs')
def api_docs():
    """Serve the API documentation page"""
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Movie API Server</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; }
            .endpoint { margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 5px; }
            .method { color: #28a745; font-weight: bold; }
            .url { color: #007bff; font-family: monospace; }
            .description { margin-top: 10px; color: #666; }
            h1 { color: #333; }
            h2 { color: #555; margin-top: 30px; }
            .status { color: #28a745; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Movie API Server</h1>
            <p>Flask backend for TMDB movie search and streaming integration</p>
            <p><a href="/">← Back to Movie Streaming Site</a></p>

            <h2>Available Endpoints:</h2>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/search?query=movie_title&year=2020</span>
                <div class="description">Search for movies by title and optional year</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/movie/&lt;movie_id&gt;</span>
                <div class="description">Get detailed information about a specific movie</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/popular?page=1</span>
                <div class="description">Get popular movies</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/trending?time_window=day</span>
                <div class="description">Get trending movies (day/week)</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/stream/&lt;movie_id&gt;</span>
                <div class="description">Generate VidKing streaming URL for a movie</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/tv/search?query=show_title&year=year</span>
                <div class="description">Search for TV shows by title and optional year</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/tv/&lt;tv_id&gt;</span>
                <div class="description">Get detailed information about a specific TV show</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/tv/stream/&lt;tv_id&gt;?season=1&episode=1</span>
                <div class="description">Generate VidKing streaming URL for a TV episode</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/tv/popular</span>
                <div class="description">Get popular TV shows</div>
            </div>

            <div class="endpoint">
                <span class="method">GET</span> <span class="url">/api/search/multi?query=title</span>
                <div class="description">Search both movies and TV shows</div>
            </div>

            <h2>Example Usage:</h2>
            <div class="endpoint">
                <div style="font-family: monospace; background: #000; color: #0f0; padding: 10px; border-radius: 3px;">
# Search for Inception<br/>
curl "http://localhost:5001/api/search?query=Inception&year=2010"<br/><br/>

# Get movie details<br/>
curl "http://localhost:5001/api/movie/27205"<br/><br/>

# Generate streaming URL<br/>
curl "http://localhost:5001/api/stream/27205"
                </div>
            </div>

            <p><span class="status">Server Status:</span> Running</p>
        </div>
    </body>
    </html>
    """)

@app.route('/movie_tv_player.html')
def movie_tv_player():
    """Serve the movie and TV player interface"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_file(os.path.join(current_dir, 'movie_tv_player.html'))

@app.route('/movie_search_player.html')
def movie_search_player():
    """Serve the movie search player interface"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_file(os.path.join(current_dir, 'movie_search_player.html'))

@app.route('/video_player_with_subtitles.html')
def subtitle_player():
    """Serve the video player with subtitle support"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_file(os.path.join(current_dir, 'video_player_with_subtitles.html'))

@app.route('/api/server-url')
def get_server_url():
    """Get the current server URL (supports ngrok)"""
    # Check if request is coming through ngrok
    host = request.host
    if 'ngrok' in host or 'ngrok-free.dev' in host:
        # External user accessing via ngrok
        scheme = 'https' if request.is_secure else 'http'
        return jsonify({'url': f'{scheme}://{host}', 'type': 'ngrok'})

    # Try to get ngrok URL from local ngrok API
    try:
        import requests as req
        response = req.get('http://localhost:4040/api/tunnels', timeout=1)
        if response.status_code == 200:
            data = response.json()
            for tunnel in data.get('tunnels', []):
                if tunnel.get('proto') == 'https':
                    ngrok_url = tunnel.get('public_url')
                    return jsonify({'url': ngrok_url, 'type': 'ngrok'})
    except:
        pass

    # Fall back to localhost
    return jsonify({'url': 'http://localhost:5001', 'type': 'local'})

@app.route('/proxy-vidking')
def proxy_vidking():
    """Proxy VidKing player with Safari subtitle fixes"""
    video_url = request.args.get('url')

    if not video_url:
        return jsonify({"error": "URL parameter required"}), 400

    try:
        # Fetch VidKing page
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(video_url, headers=headers, timeout=30)
        response.raise_for_status()

        content = response.text

        # Inject Safari subtitle fix script
        safari_fix = """
        <script>
        (function() {
            // Safari subtitle fix
            if (navigator.userAgent.indexOf('Safari') !== -1 && navigator.userAgent.indexOf('Chrome') === -1) {
                // Override video element creation
                const originalCreateElement = document.createElement;
                document.createElement = function(tagName) {
                    const element = originalCreateElement.call(document, tagName);

                    if (tagName.toLowerCase() === 'video') {
                        // Force subtitle display
                        element.setAttribute('crossorigin', 'anonymous');

                        // Monitor for track additions
                        const observer = new MutationObserver(function(mutations) {
                            mutations.forEach(function(mutation) {
                                mutation.addedNodes.forEach(function(node) {
                                    if (node.tagName === 'TRACK') {
                                        node.setAttribute('mode', 'showing');
                                        node.track.mode = 'showing';
                                        console.log('Safari: Forced subtitle track to show');
                                    }
                                });
                            });
                        });

                        observer.observe(element, { childList: true, subtree: true });

                        // Force textTracks to showing
                        element.addEventListener('loadedmetadata', function() {
                            setTimeout(() => {
                                for (let i = 0; i < element.textTracks.length; i++) {
                                    element.textTracks[i].mode = 'showing';
                                    console.log('Safari: Enabled track', i);
                                }
                            }, 500);
                        });
                    }

                    return element;
                };
            }
        })();
        </script>
        """

        # Inject before closing head tag
        if '</head>' in content:
            content = content.replace('</head>', safari_fix + '</head>')
        else:
            content = safari_fix + content

        return Response(
            content,
            mimetype='text/html',
            headers={
                'Access-Control-Allow-Origin': '*',
                'X-Frame-Options': 'ALLOWALL',
                'Content-Security-Policy': "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:; frame-ancestors *;"
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# @app.route('/sw.js')
# def serve_sw():
#     """Serve Monetag service worker for push notifications"""
#     current_dir = os.path.dirname(os.path.abspath(__file__))
#     return send_file(os.path.join(current_dir, 'sw.js'), mimetype='application/javascript')

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "movie-api"})

# Subtitle Endpoints

@app.route('/api/subtitles/search')
def search_subtitles():
    """Search for subtitles"""
    imdb_id = request.args.get('imdb_id')
    query = request.args.get('query')
    tmdb_id = request.args.get('tmdb_id')
    content_type = request.args.get('type', 'movie')  # 'movie' or 'tv'
    season = request.args.get('season', type=int)
    episode = request.args.get('episode', type=int)
    language = request.args.get('language', 'eng')

    # If TMDB ID is provided, get IMDB ID first
    if tmdb_id and not imdb_id:
        try:
            if content_type == 'tv':
                details = tmdb.get_tv_details(int(tmdb_id))
            else:
                details = tmdb.get_movie_details(int(tmdb_id))

            if details and 'imdb_id' in details:
                imdb_id = details['imdb_id']
            elif details and 'external_ids' in details and 'imdb_id' in details['external_ids']:
                imdb_id = details['external_ids']['imdb_id']

            # Also get the title for fallback search
            if not query:
                if content_type == 'tv':
                    query = details.get('name', '')
                else:
                    query = details.get('title', '')
        except:
            pass

    # Search subtitles
    subtitles = subtitle_service.search_subtitles(
        imdb_id=imdb_id,
        query=query,
        season=season,
        episode=episode,
        language=language
    )

    # If no results and we have a query, try alternative sources
    if not subtitles and query:
        subtitles = subtitle_service.search_alternative_subtitles(
            title=query,
            season=season,
            episode=episode
        )

    return jsonify({
        "subtitles": subtitles,
        "imdb_id": imdb_id,
        "query": query,
        "season": season,
        "episode": episode,
        "language": language
    })

@app.route('/api/subtitles/download')
def download_subtitle():
    """Download and serve subtitle file"""
    subtitle_url = request.args.get('url')

    if not subtitle_url:
        return jsonify({"error": "URL parameter is required"}), 400

    subtitle_content = subtitle_service.get_subtitle_file(subtitle_url)

    if subtitle_content:
        # Serve as VTT (Web Video Text Tracks) format
        # Convert SRT to VTT if needed
        if not subtitle_content.startswith('WEBVTT'):
            vtt_content = convert_srt_to_vtt(subtitle_content)
        else:
            vtt_content = subtitle_content

        return Response(
            vtt_content,
            mimetype='text/vtt',
            headers={
                'Content-Disposition': 'inline; filename="subtitle.vtt"',
                'Access-Control-Allow-Origin': '*'
            }
        )
    else:
        return jsonify({"error": "Failed to download subtitle"}), 500

@app.route('/api/subtitles/proxy', methods=['GET', 'OPTIONS'])
def proxy_subtitle():
    """Proxy subtitle file to avoid CORS issues"""

    # Handle OPTIONS preflight request (for Safari)
    if request.method == 'OPTIONS':
        response = Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    url = request.args.get('url')

    if not url:
        return jsonify({"error": "URL parameter is required"}), 400

    try:
        response = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()

        content = response.content

        # Decompress if gzipped
        if url.endswith('.gz'):
            content = gzip.decompress(content)

        # Decode to text
        text_content = content.decode('utf-8', errors='ignore')

        # Convert to VTT if it's SRT
        if not text_content.startswith('WEBVTT'):
            text_content = convert_srt_to_vtt(text_content)

        # Ensure proper VTT format
        if not text_content.strip().startswith('WEBVTT'):
            text_content = 'WEBVTT\n\n' + text_content

        return Response(
            text_content,
            mimetype='text/vtt; charset=utf-8',
            headers={
                'Content-Disposition': 'inline; filename="subtitle.vtt"',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Cache-Control': 'public, max-age=3600',
                'Content-Type': 'text/vtt; charset=utf-8'
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def convert_srt_to_vtt(srt_content):
    """Convert SRT subtitle format to WebVTT"""
    lines = srt_content.strip().split('\n')
    vtt_lines = ['WEBVTT', '']

    for i, line in enumerate(lines):
        # Replace SRT timestamp format (,) with VTT format (.)
        if '-->' in line:
            line = line.replace(',', '.')
        vtt_lines.append(line)

    return '\n'.join(vtt_lines)

# ============= BINGEFLIX CACHED M3U8 ENDPOINT =============

@app.route('/api/hls/bingeflix/<stream_id>', methods=['GET', 'OPTIONS'])
def serve_bingeflix_m3u8(stream_id):
    """
    Serve cached m3u8 content from Bingeflix scraper.
    Rewrites segment URLs to go through the HLS proxy.
    """
    if request.method == 'OPTIONS':
        response = Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Range'
        return response

    cached = bingeflix_m3u8_cache.get(stream_id)
    if not cached or (time.time() - cached['timestamp']) > BINGEFLIX_CACHE_TTL:
        print(f"[Bingeflix M3U8] Cache miss or expired for {stream_id}")
        return jsonify({"error": "Stream not found or expired"}), 404

    content = cached['content']
    base_url = cached['base_url']
    referer = cached['referer']

    # Rewrite URLs in the m3u8 to go through the proxy
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            # This is a URL (segment or sub-playlist)
            if stripped.startswith('http'):
                segment_url = stripped
            else:
                segment_url = base_url + stripped
            proxied = f"/api/hls/proxy?url={quote(segment_url, safe='')}&referer={quote(referer, safe='')}"
            new_lines.append(proxied)
        else:
            new_lines.append(line)

    rewritten = '\n'.join(new_lines)

    return Response(
        rewritten,
        mimetype='application/vnd.apple.mpegurl',
        headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Range',
            'Cache-Control': 'no-cache',
        }
    )

# ============= HLS PROXY (for VidNest streams) =============

@app.route('/api/hls/proxy', methods=['GET', 'OPTIONS'])
def proxy_hls():
    """
    Proxy HLS m3u8 and video segments with proper Referer header.
    Usage: /api/hls/proxy?url=<m3u8_url>&referer=<referer_url>
    """
    if request.method == 'OPTIONS':
        response = Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Range'
        return response

    url = request.args.get('url')
    referer = request.args.get('referer', 'https://vidnest.fun/')

    if not url:
        return jsonify({"error": "URL parameter is required"}), 400

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            'Referer': referer,
            'Origin': referer.rstrip('/'),
        }

        # Forward range header if present (for video segments)
        if 'Range' in request.headers:
            headers['Range'] = request.headers['Range']

        resp = requests.get(url, headers=headers, timeout=30, stream=True, allow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get('Content-Type', 'application/octet-stream')

        # If it's an m3u8 file, rewrite URLs to go through proxy
        if '.m3u8' in url or 'application/vnd.apple.mpegurl' in content_type or 'application/x-mpegurl' in content_type:
            content = resp.text

            # Get base URL for relative paths
            base_url = url.rsplit('/', 1)[0] + '/'

            # Rewrite URLs in the m3u8
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    # This is a URL (segment or sub-playlist)
                    if line.startswith('http'):
                        segment_url = line
                    else:
                        segment_url = base_url + line
                    # Rewrite to go through proxy
                    proxied_url = f"/api/hls/proxy?url={requests.utils.quote(segment_url)}&referer={requests.utils.quote(referer)}"
                    new_lines.append(proxied_url)
                else:
                    new_lines.append(line)

            content = '\n'.join(new_lines)

            return Response(
                content,
                mimetype='application/vnd.apple.mpegurl',
                headers={
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Range',
                    'Cache-Control': 'no-cache',
                }
            )
        else:
            # For video segments, stream directly
            response_headers = {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Range',
                'Content-Type': content_type,
            }

            # Forward content-length and accept-ranges
            if 'Content-Length' in resp.headers:
                response_headers['Content-Length'] = resp.headers['Content-Length']
            if 'Accept-Ranges' in resp.headers:
                response_headers['Accept-Ranges'] = resp.headers['Accept-Ranges']
            if 'Content-Range' in resp.headers:
                response_headers['Content-Range'] = resp.headers['Content-Range']

            return Response(
                resp.iter_content(chunk_size=8192),
                status=resp.status_code,
                headers=response_headers
            )

    except requests.exceptions.RequestException as e:
        print(f"[HLS Proxy] Error fetching {url}: {e}")
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        print(f"[HLS Proxy] Error: {e}")
        return jsonify({"error": str(e)}), 500

# ============= WATCHPARTY SOCKETIO EVENTS =============

def generate_room_code():
    """Generate a unique 6-character room code"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in watchparty_rooms:
            return code

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print(f"Client connected: {request.sid}")
    emit('connected', {'sid': request.sid})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    sid = request.sid
    print(f"Client disconnected: {sid}")

    # Remove user from their room
    if sid in user_rooms:
        room_code = user_rooms[sid]
        if room_code in watchparty_rooms:
            room = watchparty_rooms[room_code]
            username = room['users'].get(sid, 'Unknown')

            # Remove user
            if sid in room['users']:
                del room['users'][sid]

            # Convert users dict to list of user objects with socket_id
            users_list = [{'username': uname, 'socket_id': usid} for usid, uname in room['users'].items()]

            # Notify others in room
            emit('user_left', {
                'username': username,
                'socket_id': sid,
                'users': users_list
            }, room=room_code)

            # If host left, assign new host or delete room
            if room['host'] == sid:
                if room['users']:
                    # Assign new host (first user)
                    new_host_sid = list(room['users'].keys())[0]
                    room['host'] = new_host_sid
                    emit('new_host', {
                        'username': room['users'][new_host_sid]
                    }, room=room_code)
                else:
                    # Delete empty room
                    del watchparty_rooms[room_code]

        del user_rooms[sid]

@socketio.on('create_party')
def handle_create_party(data):
    """Create a new watchparty room"""
    sid = request.sid
    username = data.get('username', 'Anonymous')
    content = data.get('content', {})

    # Generate unique room code
    room_code = generate_room_code()

    # Create room
    watchparty_rooms[room_code] = {
        'host': sid,
        'users': {sid: username},
        'content': content,
        'state': {
            'playing': False,
            'currentTime': 0,
            'timestamp': datetime.now().isoformat()
        },
        'sync_mode': 'none',
        'ready_users': {},
        'ready_check_active': False,
        'created_at': datetime.now().isoformat()
    }

    user_rooms[sid] = room_code
    join_room(room_code)

    print(f"Party created: {room_code} by {username}")

    emit('party_created', {
        'room_code': room_code,
        'is_host': True,
        'content': content,
        'users': [{'username': username, 'socket_id': sid}]
    })

@socketio.on('join_party')
def handle_join_party(data):
    """Join an existing watchparty room"""
    sid = request.sid
    room_code = data.get('room_code', '').upper()
    username = data.get('username', 'Anonymous')

    # Check if room exists
    if room_code not in watchparty_rooms:
        emit('join_error', {'message': 'Room not found'})
        return

    room = watchparty_rooms[room_code]

    # Add user to room
    room['users'][sid] = username
    user_rooms[sid] = room_code
    join_room(room_code)

    print(f"{username} joined party: {room_code}")

    # Convert users dict to list of user objects with socket_id
    users_list = [{'username': uname, 'socket_id': usid} for usid, uname in room['users'].items()]

    # Notify user who joined
    emit('party_joined', {
        'room_code': room_code,
        'is_host': (sid == room['host']),
        'content': room['content'],
        'users': users_list,
        'state': room['state'],
        'sync_mode': room.get('sync_mode', 'none')
    })

    # Notify others in room
    emit('user_joined', {
        'username': username,
        'socket_id': sid,
        'users': users_list
    }, room=room_code, skip_sid=sid)

@socketio.on('leave_party')
def handle_leave_party():
    """Leave the current watchparty"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        if room_code in watchparty_rooms:
            room = watchparty_rooms[room_code]
            username = room['users'].get(sid, 'Unknown')

            # Remove user
            if sid in room['users']:
                del room['users'][sid]

            leave_room(room_code)

            # Convert users dict to list of user objects with socket_id
            users_list = [{'username': uname, 'socket_id': usid} for usid, uname in room['users'].items()]

            # Notify others
            emit('user_left', {
                'username': username,
                'socket_id': sid,
                'users': users_list
            }, room=room_code)

            # Handle host leaving
            if room['host'] == sid:
                if room['users']:
                    new_host_sid = list(room['users'].keys())[0]
                    room['host'] = new_host_sid
                    emit('new_host', {
                        'username': room['users'][new_host_sid]
                    }, room=room_code)
                else:
                    del watchparty_rooms[room_code]

        del user_rooms[sid]
        emit('party_left')

@socketio.on('sync_play')
def handle_sync_play(data):
    """Sync play state across party"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:  # Only host can control playback
            current_time = data.get('currentTime', 0)
            room['state']['playing'] = True
            room['state']['currentTime'] = current_time
            room['state']['timestamp'] = datetime.now().isoformat()

            print(f"[Sync] Host {room['users'][sid]} played at {current_time}s - broadcasting to room {room_code}")
            emit('play_sync', {
                'currentTime': current_time,
                'username': room['users'][sid]
            }, room=room_code, skip_sid=sid)
        else:
            print(f"[Sync] Play sync rejected - not host or room not found")

@socketio.on('sync_pause')
def handle_sync_pause(data):
    """Sync pause state across party"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:  # Only host can control playback
            current_time = data.get('currentTime', 0)
            room['state']['playing'] = False
            room['state']['currentTime'] = current_time
            room['state']['timestamp'] = datetime.now().isoformat()

            print(f"[Sync] Host {room['users'][sid]} paused at {current_time}s - broadcasting to room {room_code}")
            emit('pause_sync', {
                'currentTime': current_time,
                'username': room['users'][sid]
            }, room=room_code, skip_sid=sid)
        else:
            print(f"[Sync] Pause sync rejected - not host or room not found")

@socketio.on('sync_seek')
def handle_sync_seek(data):
    """Sync seek/scrub across party"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:  # Only host can control playback
            current_time = data.get('currentTime', 0)
            room['state']['currentTime'] = current_time
            room['state']['timestamp'] = datetime.now().isoformat()

            print(f"[Sync] Host {room['users'][sid]} seeked to {current_time}s - broadcasting to room {room_code}")
            emit('seek_sync', {
                'currentTime': current_time,
                'username': room['users'][sid]
            }, room=room_code, skip_sid=sid)
        else:
            print(f"[Sync] Seek sync rejected - not host or room not found")

@socketio.on('sync_content')
def handle_sync_content(data):
    """Sync content change across party"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:  # Only host can change content
            content = data.get('content', {})
            room['content'] = content

            emit('content_sync', {
                'content': content
            }, room=room_code, skip_sid=sid)

@socketio.on('sync_stop')
def handle_sync_stop():
    """Sync stop playback across party"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:  # Only host can control playback
            room['state']['playing'] = False

# ============= PLAYBACK SYNC HANDLERS =============

@socketio.on('playback_play')
def handle_playback_play(data):
    """Handle play event - anyone can play"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room:
            current_time = data.get('currentTime', 0)
            room['state']['playing'] = True
            room['state']['currentTime'] = current_time
            room['state']['timestamp'] = datetime.now().isoformat()

            username = room['users'].get(sid, 'Unknown')
            print(f"[Playback] {username} played at {current_time}s in room {room_code}")

            # Broadcast to all OTHER users in the room
            emit('playback_play', {
                'currentTime': current_time,
                'username': username
            }, room=room_code, skip_sid=sid)

@socketio.on('playback_pause')
def handle_playback_pause(data):
    """Handle pause event - anyone can pause"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room:
            current_time = data.get('currentTime', 0)
            room['state']['playing'] = False
            room['state']['currentTime'] = current_time
            room['state']['timestamp'] = datetime.now().isoformat()

            username = room['users'].get(sid, 'Unknown')
            print(f"[Playback] {username} paused at {current_time}s in room {room_code}")

            # Broadcast to all OTHER users in the room
            emit('playback_pause', {
                'currentTime': current_time,
                'username': username
            }, room=room_code, skip_sid=sid)

@socketio.on('playback_seek')
def handle_playback_seek(data):
    """Handle seek event - anyone can seek"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room:
            current_time = data.get('currentTime', 0)
            room['state']['currentTime'] = current_time
            room['state']['timestamp'] = datetime.now().isoformat()

            username = room['users'].get(sid, 'Unknown')
            print(f"[Playback] {username} seeked to {current_time}s in room {room_code}")

            # Broadcast to all OTHER users in the room
            emit('playback_seek', {
                'currentTime': current_time,
                'username': username
            }, room=room_code, skip_sid=sid)

@socketio.on('request_sync_status')
def handle_request_sync_status():
    """Send current playback state to requesting user for drift correction"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room:
            state = room['state']
            # Calculate expected current time based on when last action happened
            from datetime import datetime
            last_update = datetime.fromisoformat(state['timestamp'])
            time_elapsed = (datetime.now() - last_update).total_seconds()

            expected_time = state['currentTime']
            if state['playing']:
                expected_time += time_elapsed

            emit('sync_status', {
                'playing': state['playing'],
                'currentTime': expected_time
            })

@socketio.on('chat_message')
def handle_chat_message(data):
    """Handle chat messages in watchparty"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room:
            username = room['users'].get(sid, 'Anonymous')
            message = data.get('message', '')
            reply_to = data.get('replyTo', None)

            emit('chat_message', {
                'username': username,
                'message': message,
                'timestamp': datetime.now().isoformat(),
                'is_host': (sid == room['host']),
                'replyTo': reply_to
            }, room=room_code)

@socketio.on('set_sync_mode')
def handle_set_sync_mode(data):
    """Host reports stream type so server can set sync mode for the room"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:
            mode = data.get('mode', 'none')
            if mode in ('full', 'coordinated', 'none'):
                room['sync_mode'] = mode
                print(f"[Sync] Room {room_code} sync mode set to: {mode}")

                emit('sync_mode_changed', {
                    'mode': mode
                }, room=room_code)

@socketio.on('ready_check')
def handle_ready_check():
    """Host initiates a ready check for coordinated sync"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:
            room['ready_users'] = {}
            room['ready_check_active'] = True
            total = len(room['users'])
            print(f"[Sync] Ready check started in room {room_code} ({total} users)")

            emit('ready_check_started', {
                'total': total
            }, room=room_code)

@socketio.on('user_ready')
def handle_user_ready():
    """User marks themselves as ready during a ready check"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room.get('ready_check_active'):
            username = room['users'].get(sid, 'Unknown')
            room['ready_users'][sid] = True
            ready_count = len(room['ready_users'])
            total = len(room['users'])
            print(f"[Sync] {username} ready in room {room_code} ({ready_count}/{total})")

            emit('user_ready_update', {
                'username': username,
                'ready_count': ready_count,
                'total': total
            }, room=room_code)

@socketio.on('start_countdown')
def handle_start_countdown():
    """Host triggers the coordinated countdown"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:
            room['ready_check_active'] = False
            room['ready_users'] = {}
            timestamp = datetime.now().isoformat()
            print(f"[Sync] Countdown started in room {room_code}")

            emit('countdown_start', {
                'timestamp': timestamp
            }, room=room_code)

@socketio.on('resync')
def handle_resync():
    """Host triggers a re-sync (new countdown) for all users"""
    sid = request.sid

    if sid in user_rooms:
        room_code = user_rooms[sid]
        room = watchparty_rooms.get(room_code)

        if room and room['host'] == sid:
            timestamp = datetime.now().isoformat()
            print(f"[Sync] Re-sync triggered in room {room_code}")

            emit('resync_triggered', {
                'timestamp': timestamp
            }, room=room_code)

# ============= END WATCHPARTY EVENTS =============

# ============= SUPEREMBED STREAMING =============

@app.route('/se_player.php')
def superembed_player():
    """SuperEmbed player proxy - Python replacement for PHP script"""
    video_id = request.args.get('video_id', '')
    tmdb = request.args.get('tmdb', '0')
    season = request.args.get('s', '0')
    episode = request.args.get('e', '0')

    # Build player settings from original PHP file
    player_font = "Poppins"
    player_bg_color = "000000"
    player_font_color = "ffffff"
    player_primary_color = "34cfeb"
    player_secondary_color = "6900e0"
    player_loader = "1"
    preferred_server = "0"
    player_sources_toggle_type = "2"

    if not video_id:
        return jsonify({"error": "Missing video_id"}), 400

    try:
        # Build SuperEmbed API URL
        superembed_url = f"https://getsuperembed.link/?video_id={video_id}&tmdb={tmdb}&season={season}&episode={episode}"
        superembed_url += f"&player_font={player_font}&player_bg_color={player_bg_color}"
        superembed_url += f"&player_font_color={player_font_color}&player_primary_color={player_primary_color}"
        superembed_url += f"&player_secondary_color={player_secondary_color}&player_loader={player_loader}"
        superembed_url += f"&preferred_server={preferred_server}&player_sources_toggle_type={player_sources_toggle_type}"

        # Make request to SuperEmbed
        response = requests.get(superembed_url, allow_redirects=True, timeout=7, verify=False)

        if response.status_code == 200:
            player_url = response.text.strip()

            if player_url.startswith('https://'):
                # Redirect to the player URL
                return Response(status=302, headers={'Location': player_url})
            else:
                return f"<span style='color:red'>{player_url}</span>"
        else:
            return "Request server didn't respond", 500

    except Exception as e:
        return f"Error: {str(e)}", 500

# ============= NGROK CONTROL =============
import subprocess
import json as json_lib

ngrok_process = None

@app.route('/api/ngrok/start', methods=['POST'])
def start_ngrok():
    """Start ngrok tunnel"""
    global ngrok_process

    if ngrok_process and ngrok_process.poll() is None:
        return jsonify({'error': 'ngrok is already running'}), 400

    try:
        # Find ngrok in common locations
        ngrok_paths = [
            'ngrok',  # In PATH
            '/usr/local/bin/ngrok',
            '/opt/homebrew/bin/ngrok',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ngrok'),
            os.path.expanduser('~/ngrok')
        ]

        ngrok_cmd = None
        for path in ngrok_paths:
            try:
                result = subprocess.run([path, 'version'], capture_output=True, timeout=2)
                if result.returncode == 0:
                    ngrok_cmd = path
                    print(f"Found ngrok at: {path}")
                    break
            except:
                continue

        if not ngrok_cmd:
            return jsonify({'error': 'ngrok not found. Please install ngrok first.'}), 400

        # Kill any existing ngrok processes first
        subprocess.run(['pkill', '-9', 'ngrok'], stderr=subprocess.DEVNULL)
        import time
        time.sleep(1)

        # Start ngrok in background
        ngrok_process = subprocess.Popen(
            [ngrok_cmd, 'http', '5001', '--log', '/tmp/ngrok.log'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait a bit for ngrok to start
        import time
        time.sleep(3)

        # Get ngrok URL
        try:
            # Disable proxy for localhost requests
            ngrok_api = requests.get('http://127.0.0.1:4040/api/tunnels',
                                    timeout=5,
                                    proxies={'http': None, 'https': None})
            tunnels = ngrok_api.json()

            https_tunnel = None
            for tunnel in tunnels.get('tunnels', []):
                if tunnel.get('proto') == 'https':
                    https_tunnel = tunnel.get('public_url')
                    break

            if https_tunnel:
                return jsonify({
                    'success': True,
                    'url': https_tunnel,
                    'dashboard': 'http://localhost:4040'
                })
            else:
                return jsonify({'error': 'Could not get ngrok URL'}), 500

        except Exception as e:
            return jsonify({'error': f'Failed to get ngrok URL: {str(e)}'}), 500

    except FileNotFoundError:
        return jsonify({'error': 'ngrok not found. Please install ngrok first.'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ngrok/stop', methods=['POST'])
def stop_ngrok():
    """Stop ngrok tunnel"""
    global ngrok_process

    if not ngrok_process or ngrok_process.poll() is not None:
        return jsonify({'error': 'ngrok is not running'}), 400

    try:
        ngrok_process.terminate()
        ngrok_process.wait(timeout=5)
        ngrok_process = None
        return jsonify({'success': True, 'message': 'ngrok stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ngrok/status')
def ngrok_status():
    """Get ngrok tunnel status"""
    global ngrok_process

    if not ngrok_process or ngrok_process.poll() is not None:
        return jsonify({'running': False})

    try:
        ngrok_api = requests.get('http://127.0.0.1:4040/api/tunnels',
                                timeout=2,
                                proxies={'http': None, 'https': None})
        tunnels = ngrok_api.json()

        https_tunnel = None
        for tunnel in tunnels.get('tunnels', []):
            if tunnel.get('proto') == 'https':
                https_tunnel = tunnel.get('public_url')
                break

        return jsonify({
            'running': True,
            'url': https_tunnel,
            'dashboard': 'http://localhost:4040'
        })
    except:
        return jsonify({'running': False})

# ============= END NGROK CONTROL =============

# ============= AUTHENTICATION ROUTES =============

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')

        if not username or not email or not password:
            return jsonify({'error': 'Username, email, and password are required'}), 400

        # Check if user already exists
        if users_collection.find_one({'username': username}):
            return jsonify({'error': 'Username already exists'}), 400

        if users_collection.find_one({'email': email}):
            return jsonify({'error': 'Email already exists'}), 400

        # Create new user
        user = {
            'username': username,
            'email': email,
            'password_hash': generate_password_hash(password),
            'created_at': datetime.utcnow(),
            'friends': [],
            'watchlist': [],
            'continue_watching': [],
            'favorites': []
        }

        result = users_collection.insert_one(user)

        # Create access token
        access_token = create_access_token(identity=str(result.inserted_id))

        return jsonify({
            'message': 'User created successfully',
            'access_token': access_token,
            'user': {
                'id': str(result.inserted_id),
                'username': username,
                'email': email
            }
        }), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return jsonify({'error': 'Username and password are required'}), 400

        user = users_collection.find_one({'username': username})

        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify({'error': 'Invalid username or password'}), 401

        access_token = create_access_token(identity=str(user['_id']))

        return jsonify({
            'message': 'Login successful',
            'access_token': access_token,
            'user': {
                'id': str(user['_id']),
                'username': user['username'],
                'email': user['email']
            }
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/auth/me', methods=['GET'])
@jwt_required()
def get_current_user():
    try:
        user_id = get_jwt_identity()
        user = users_collection.find_one({'_id': ObjectId(user_id)})

        if not user:
            return jsonify({'error': 'User not found'}), 404

        return jsonify({
            'id': str(user['_id']),
            'username': user['username'],
            'email': user['email'],
            'friends': [str(f) for f in user.get('friends', [])]
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============= WATCHLIST ROUTES =============

@app.route('/api/watchlist', methods=['GET'])
@jwt_required()
def get_watchlist():
    try:
        user_id = get_jwt_identity()
        user = users_collection.find_one({'_id': ObjectId(user_id)})

        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Get watchlist from user document (default to empty array)
        watchlist_items = user.get('watchlist', [])

        return jsonify(watchlist_items), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist', methods=['POST'])
@jwt_required()
def add_to_watchlist():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        # Get list name (default to "My Watchlist" for backwards compatibility)
        list_name = data.get('list_name', 'My Watchlist')

        watchlist_item = {
            'content_id': data['content_id'],
            'content_type': data['content_type'],
            'title': data['title'],
            'poster_path': data.get('poster_path'),
            'list_name': list_name,
            'added_at': datetime.utcnow()
        }

        # First ensure the user has a watchlist array
        users_collection.update_one(
            {'_id': ObjectId(user_id), 'watchlist': {'$exists': False}},
            {'$set': {'watchlist': []}}
        )

        # Check if already in watchlist and update if so
        result = users_collection.update_one(
            {
                '_id': ObjectId(user_id),
                'watchlist': {
                    '$not': {
                        '$elemMatch': {
                            'content_id': data['content_id'],
                            'list_name': list_name
                        }
                    }
                }
            },
            {
                '$push': {'watchlist': watchlist_item}
            }
        )

        if result.matched_count == 0:
            # Either user not found or item already exists
            user = users_collection.find_one({'_id': ObjectId(user_id)})
            if not user:
                return jsonify({'error': 'User not found'}), 404
            return jsonify({'error': 'Item already in watchlist'}), 400

        return jsonify(watchlist_item), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/<content_id>', methods=['DELETE'])
@jwt_required()
def remove_from_watchlist(content_id):
    try:
        user_id = get_jwt_identity()

        # Try to convert content_id to int if possible (TMDB IDs are integers)
        try:
            content_id_int = int(content_id)
        except ValueError:
            content_id_int = None

        # Remove item from watchlist array (try both string and int versions)
        result = users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {
                '$pull': {
                    'watchlist': {
                        'content_id': {'$in': [content_id, content_id_int] if content_id_int else [content_id]}
                    }
                }
            }
        )

        if result.matched_count == 0:
            return jsonify({'error': 'User not found'}), 404

        # Don't fail if item wasn't found - it might already be deleted
        return jsonify({'message': 'Removed from watchlist'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/rename', methods=['PUT'])
@jwt_required()
def rename_watchlist():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        old_name = data.get('old_name')
        new_name = data.get('new_name')

        if not old_name or not new_name:
            return jsonify({'error': 'old_name and new_name are required'}), 400

        if len(new_name) > 50:
            return jsonify({'error': 'List name must be 50 characters or less'}), 400

        # Update all watchlist items with the old list_name to the new list_name
        result = users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {
                '$set': {
                    'watchlist.$[elem].list_name': new_name
                }
            },
            array_filters=[{'elem.list_name': old_name}]
        )

        if result.matched_count == 0:
            return jsonify({'error': 'User not found'}), 404

        return jsonify({'message': f'Renamed list from "{old_name}" to "{new_name}"'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============= CONTINUE WATCHING ROUTES =============

@app.route('/api/continue-watching', methods=['GET'])
@jwt_required()
def get_continue_watching():
    try:
        user_id = get_jwt_identity()
        user = users_collection.find_one({'_id': ObjectId(user_id)})

        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Get continue watching from user document (default to empty array)
        items = user.get('continue_watching', [])

        # Sort by last_watched in descending order
        items = sorted(items, key=lambda x: x.get('last_watched', datetime.min), reverse=True)

        return jsonify(items), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/continue-watching', methods=['POST'])
@jwt_required()
def update_continue_watching():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        continue_watching_item = {
            'content_id': data['content_id'],
            'content_type': data['content_type'],
            'title': data['title'],
            'poster_path': data.get('poster_path'),
            'progress': data.get('progress', 0),
            'season': data.get('season'),
            'episode': data.get('episode'),
            'last_watched': datetime.utcnow()
        }

        # First ensure the user has a continue_watching array
        users_collection.update_one(
            {'_id': ObjectId(user_id), 'continue_watching': {'$exists': False}},
            {'$set': {'continue_watching': []}}
        )

        # Update existing item or add new one
        result = users_collection.update_one(
            {
                '_id': ObjectId(user_id),
                'continue_watching.content_id': data['content_id']
            },
            {
                '$set': {
                    'continue_watching.$': continue_watching_item
                }
            }
        )

        # If no existing item was updated, add a new one
        if result.matched_count == 0:
            users_collection.update_one(
                {'_id': ObjectId(user_id)},
                {
                    '$push': {'continue_watching': continue_watching_item}
                }
            )

        return jsonify({'message': 'Continue watching updated'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/continue-watching/<content_id>', methods=['DELETE'])
@jwt_required()
def remove_from_continue_watching(content_id):
    try:
        user_id = get_jwt_identity()

        # Try to convert content_id to int if possible (TMDB IDs are integers)
        try:
            content_id_int = int(content_id)
        except ValueError:
            content_id_int = None

        # Remove item from continue_watching array (try both string and int versions)
        result = users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {
                '$pull': {
                    'continue_watching': {
                        'content_id': {'$in': [content_id, content_id_int] if content_id_int else [content_id]}
                    }
                }
            }
        )

        if result.matched_count == 0:
            return jsonify({'error': 'User not found'}), 404

        # Don't fail if item wasn't found - it might already be deleted
        return jsonify({'message': 'Removed from continue watching'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============= FAVORITES (LIVE CHANNELS) ROUTES =============

@app.route('/api/favorites', methods=['GET'])
@jwt_required()
def get_favorites():
    try:
        user_id = get_jwt_identity()
        user = users_collection.find_one({'_id': ObjectId(user_id)})

        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Get favorites from user document (default to empty array)
        favorites = user.get('favorites', [])

        return jsonify(favorites), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/favorites', methods=['POST'])
@jwt_required()
def add_to_favorites():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        favorite_item = {
            'channel_id': data['channel_id'],
            'channel_name': data['channel_name'],
            'added_at': datetime.utcnow()
        }

        # First ensure the user has a favorites array
        users_collection.update_one(
            {'_id': ObjectId(user_id), 'favorites': {'$exists': False}},
            {'$set': {'favorites': []}}
        )

        # Check if already in favorites and add if not
        result = users_collection.update_one(
            {
                '_id': ObjectId(user_id),
                'favorites.channel_id': {'$ne': data['channel_id']}
            },
            {
                '$push': {'favorites': favorite_item}
            }
        )

        if result.matched_count == 0:
            # Either user not found or channel already in favorites
            user = users_collection.find_one({'_id': ObjectId(user_id)})
            if not user:
                return jsonify({'error': 'User not found'}), 404
            return jsonify({'error': 'Channel already in favorites'}), 400

        return jsonify(favorite_item), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/favorites/<path:channel_id>', methods=['DELETE'])
@jwt_required()
def remove_from_favorites(channel_id):
    try:
        user_id = get_jwt_identity()

        # Remove channel from favorites array
        result = users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {
                '$pull': {
                    'favorites': {'channel_id': channel_id}
                }
            }
        )

        if result.matched_count == 0:
            return jsonify({'error': 'User not found'}), 404

        if result.modified_count == 0:
            return jsonify({'error': 'Channel not found in favorites'}), 404

        return jsonify({'message': 'Removed from favorites'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/favorites/remove', methods=['POST'])
@jwt_required()
def remove_from_favorites_post():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()
        channel_id = data.get('channel_id')

        if not channel_id:
            return jsonify({'error': 'channel_id is required'}), 400

        result = users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {
                '$pull': {
                    'favorites': {'channel_id': channel_id}
                }
            }
        )

        if result.matched_count == 0:
            return jsonify({'error': 'User not found'}), 404

        if result.modified_count == 0:
            return jsonify({'error': 'Channel not found in favorites'}), 404

        return jsonify({'message': 'Removed from favorites'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============= FRIENDS ROUTES =============

@app.route('/api/friends', methods=['GET'])
@jwt_required()
def get_friends():
    try:
        user_id = get_jwt_identity()
        print(f"[Friends] get_friends called for user_id: {user_id}")
        user = users_collection.find_one({'_id': ObjectId(user_id)})

        if not user:
            print(f"[Friends] User not found: {user_id}")
            return jsonify({'error': 'User not found'}), 404

        friend_ids = user.get('friends', [])
        if friend_ids is None:
            friend_ids = []
        print(f"[Friends] User {user.get('username')} has friend_ids: {friend_ids}")
        print(f"[Friends] Type of friend_ids: {type(friend_ids)}")
        print(f"[Friends] Is list: {isinstance(friend_ids, list)}")
        print(f"[Friends] Length: {len(friend_ids)}")

        if friend_ids:
            print(f"[Friends] First friend_id: {friend_ids[0]}")
            print(f"[Friends] First friend_id type: {type(friend_ids[0])}")

            # Ensure all friend_ids are ObjectIds
            if not isinstance(friend_ids[0], ObjectId):
                print(f"[Friends] Converting friend_ids to ObjectIds")
                friend_ids = [ObjectId(fid) if not isinstance(fid, ObjectId) else fid for fid in friend_ids]
                print(f"[Friends] After conversion: {friend_ids}")

        print(f"[Friends] Querying for friends with _id in: {friend_ids}")
        friends = list(users_collection.find({'_id': {'$in': friend_ids}}))
        print(f"[Friends] Found {len(friends)} friend documents")
        if friends:
            print(f"[Friends] Friend usernames: {[f.get('username') for f in friends]}")

        friends_list = [{
            'id': str(friend['_id']),
            'username': friend['username']
        } for friend in friends]

        print(f"[Friends] Returning friends: {[f['username'] for f in friends_list]}")
        return jsonify(friends_list), 200

    except Exception as e:
        print(f"[Friends] Error in get_friends: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/friends/search', methods=['GET'])
@jwt_required()
def search_users():
    try:
        query = request.args.get('q', '')
        user_id = get_jwt_identity()

        print(f"[Friends] Search request - Query: '{query}', User ID: {user_id}")

        if not query:
            print("[Friends] Empty query, returning empty array")
            return jsonify([]), 200

        # Get total user count for debugging
        total_users = users_collection.count_documents({})
        print(f"[Friends] Total users in database: {total_users}")

        users = list(users_collection.find({
            'username': {'$regex': query, '$options': 'i'},
            '_id': {'$ne': ObjectId(user_id)}
        }).limit(10))

        print(f"[Friends] Found {len(users)} users matching '{query}'")

        results = [{
            'id': str(user['_id']),
            'username': user['username']
        } for user in users]

        print(f"[Friends] Returning results: {[u['username'] for u in results]}")

        return jsonify(results), 200

    except Exception as e:
        print(f"[Friends] Error in search_users: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/friends/request', methods=['POST'])
@jwt_required()
def send_friend_request():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()
        to_username = data.get('username')

        if not to_username:
            return jsonify({'error': 'Username is required'}), 400

        to_user = users_collection.find_one({'username': to_username})

        if not to_user:
            return jsonify({'error': 'User not found'}), 404

        to_user_id = to_user['_id']

        # Check if already friends
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        if to_user_id in user.get('friends', []):
            return jsonify({'error': 'Already friends'}), 400

        # Check if request already exists
        existing_request = friend_requests_collection.find_one({
            '$or': [
                {'from_user_id': ObjectId(user_id), 'to_user_id': to_user_id, 'status': 'pending'},
                {'from_user_id': to_user_id, 'to_user_id': ObjectId(user_id), 'status': 'pending'}
            ]
        })

        if existing_request:
            return jsonify({'error': 'Friend request already pending'}), 400

        # Create friend request
        friend_request = {
            'from_user_id': ObjectId(user_id),
            'to_user_id': to_user_id,
            'status': 'pending',
            'created_at': datetime.utcnow()
        }

        friend_requests_collection.insert_one(friend_request)

        return jsonify({'message': 'Friend request sent'}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/friends/requests', methods=['GET'])
@jwt_required()
def get_friend_requests():
    try:
        user_id = get_jwt_identity()

        # Get pending requests sent to this user
        requests = list(friend_requests_collection.find({
            'to_user_id': ObjectId(user_id),
            'status': 'pending'
        }))

        # Get sender info
        result = []
        for req in requests:
            sender = users_collection.find_one({'_id': req['from_user_id']})
            if sender:
                result.append({
                    'id': str(req['_id']),
                    'from_user': {
                        'id': str(sender['_id']),
                        'username': sender['username']
                    },
                    'created_at': req['created_at'].isoformat()
                })

        return jsonify(result), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/friends/requests/<request_id>/accept', methods=['POST'])
@jwt_required()
def accept_friend_request(request_id):
    try:
        user_id = get_jwt_identity()

        friend_request = friend_requests_collection.find_one({
            '_id': ObjectId(request_id),
            'to_user_id': ObjectId(user_id),
            'status': 'pending'
        })

        if not friend_request:
            return jsonify({'error': 'Friend request not found'}), 404

        from_user_id = friend_request['from_user_id']

        # Add to friends lists
        users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$addToSet': {'friends': from_user_id}}
        )
        users_collection.update_one(
            {'_id': from_user_id},
            {'$addToSet': {'friends': ObjectId(user_id)}}
        )

        # Update request status
        friend_requests_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {'status': 'accepted'}}
        )

        return jsonify({'message': 'Friend request accepted'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/friends/requests/<request_id>/reject', methods=['POST'])
@jwt_required()
def reject_friend_request(request_id):
    try:
        user_id = get_jwt_identity()

        friend_request = friend_requests_collection.find_one({
            '_id': ObjectId(request_id),
            'to_user_id': ObjectId(user_id),
            'status': 'pending'
        })

        if not friend_request:
            return jsonify({'error': 'Friend request not found'}), 404

        # Update request status
        friend_requests_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {'status': 'rejected'}}
        )

        return jsonify({'message': 'Friend request rejected'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/friends/<friend_id>', methods=['DELETE'])
@jwt_required()
def remove_friend(friend_id):
    try:
        user_id = get_jwt_identity()

        # Remove from both users' friend lists
        users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$pull': {'friends': ObjectId(friend_id)}}
        )
        users_collection.update_one(
            {'_id': ObjectId(friend_id)},
            {'$pull': {'friends': ObjectId(user_id)}}
        )

        return jsonify({'message': 'Friend removed'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============= COMMENTS ROUTES =============

@app.route('/api/comments/<content_id>', methods=['GET'])
@jwt_required()
def get_comments(content_id):
    try:
        user_id = get_jwt_identity()

        # Convert content_id to int (it's stored as integer in DB)
        try:
            content_id_int = int(content_id)
        except ValueError:
            content_id_int = content_id  # Fallback to string if not a number

        # Get current user's friends
        user = users_collection.find_one({'_id': ObjectId(user_id)})

        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Get friend IDs - ensure it's a list
        friend_ids = user.get('friends', [])
        if friend_ids is None:
            friend_ids = []

        # Ensure all friends are ObjectIds
        friend_ids = [ObjectId(fid) if not isinstance(fid, ObjectId) else fid for fid in friend_ids]

        # Always include own comments
        friend_ids.append(ObjectId(user_id))

        print(f"[Comments] Getting comments for content_id: {content_id} (converted to {content_id_int})")
        print(f"[Comments] User ID: {user_id}")
        print(f"[Comments] Friend IDs: {friend_ids}")

        # Helper function to recursively get all nested replies
        def get_nested_replies(parent_id, friend_ids, user_id, depth=0):
            """Recursively fetch all nested replies for a comment"""
            replies = list(comments_collection.find({
                'parent_comment_id': parent_id,
                'user_id': {'$in': friend_ids}
            }).sort('created_at', 1))

            print(f"[Comments] {'  ' * depth}Found {len(replies)} replies for parent {parent_id}")

            # Process each reply and get its nested replies
            for reply in replies:
                reply_id = reply['_id']
                print(f"[Comments] {'  ' * depth}Processing reply {reply_id}: {reply['comment_text'][:30]}")

                # Get like count and user's like status
                reply_like_count = comment_likes_collection.count_documents({'comment_id': reply_id})
                reply['like_count'] = reply_like_count

                reply_user_liked = comment_likes_collection.find_one({
                    'comment_id': reply_id,
                    'user_id': ObjectId(user_id)
                }) is not None
                reply['liked_by_user'] = reply_user_liked

                # Recursively get nested replies for this reply
                nested = get_nested_replies(reply_id, friend_ids, user_id, depth + 1)
                reply['replies'] = nested
                print(f"[Comments] {'  ' * depth}Reply {reply_id} has {len(nested)} nested replies")

                # Convert ObjectIds to strings
                reply['_id'] = str(reply['_id'])
                reply['user_id'] = str(reply['user_id'])
                reply['parent_comment_id'] = str(reply['parent_comment_id'])
                reply['created_at'] = reply['created_at'].isoformat()

            return replies

        # Get comments from user and their friends - use integer content_id
        # Only get top-level comments (no parent_comment_id, or parent_comment_id is None/null)
        comments = list(comments_collection.find({
            'content_id': content_id_int,
            'user_id': {'$in': friend_ids},
            '$or': [
                {'parent_comment_id': {'$exists': False}},
                {'parent_comment_id': None},
                {'parent_comment_id': 'None'}  # Handle string "None" from old data
            ]
        }).sort('created_at', -1))

        print(f"[Comments] Found {len(comments)} top-level comments")

        # For each comment, add like count, user's like status, and replies
        for comment in comments:
            comment_id = comment['_id']

            # Get like count
            like_count = comment_likes_collection.count_documents({'comment_id': comment_id})
            comment['like_count'] = like_count

            # Check if current user liked this comment
            user_liked = comment_likes_collection.find_one({
                'comment_id': comment_id,
                'user_id': ObjectId(user_id)
            }) is not None
            comment['liked_by_user'] = user_liked

            # Get all nested replies recursively
            comment['replies'] = get_nested_replies(comment_id, friend_ids, user_id)
            print(f"[Comments] Top-level comment '{comment['comment_text'][:30]}' has {len(comment['replies'])} direct replies")
            for i, reply in enumerate(comment['replies']):
                print(f"[Comments]   Reply {i}: '{reply['comment_text'][:30]}' has {len(reply.get('replies', []))} nested replies")

            comment['_id'] = str(comment['_id'])
            comment['user_id'] = str(comment['user_id'])
            comment['created_at'] = comment['created_at'].isoformat()

        return jsonify(comments), 200

    except Exception as e:
        print(f"[Comments] Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/comments', methods=['POST'])
@jwt_required()
def add_comment():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        user = users_collection.find_one({'_id': ObjectId(user_id)})

        comment = {
            'user_id': ObjectId(user_id),
            'username': user['username'],
            'content_id': data['content_id'],
            'content_type': data['content_type'],
            'comment_text': data['comment_text'],
            'created_at': datetime.utcnow()
        }

        # If this is a reply, add parent_comment_id
        if data.get('parent_comment_id'):
            comment['parent_comment_id'] = ObjectId(data['parent_comment_id'])
            print(f"[Comments] Adding reply to comment {data['parent_comment_id']}")

        print(f"[Comments] Adding comment: user_id={comment['user_id']}, content_id={comment['content_id']}, text={comment['comment_text'][:50]}...")

        result = comments_collection.insert_one(comment)
        comment['_id'] = str(result.inserted_id)
        comment['user_id'] = str(comment['user_id'])
        if comment.get('parent_comment_id'):
            comment['parent_comment_id'] = str(comment['parent_comment_id'])
        comment['created_at'] = comment['created_at'].isoformat()

        print(f"[Comments] Comment saved with ID: {comment['_id']}")

        return jsonify(comment), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/comments/<comment_id>', methods=['DELETE'])
@jwt_required()
def delete_comment(comment_id):
    try:
        user_id = get_jwt_identity()

        result = comments_collection.delete_one({
            '_id': ObjectId(comment_id),
            'user_id': ObjectId(user_id)
        })

        if result.deleted_count == 0:
            return jsonify({'error': 'Comment not found or unauthorized'}), 404

        # Also delete all likes for this comment
        comment_likes_collection.delete_many({'comment_id': ObjectId(comment_id)})

        # Also delete all replies to this comment
        comments_collection.delete_many({'parent_comment_id': ObjectId(comment_id)})

        return jsonify({'message': 'Comment deleted'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/comments/<comment_id>/like', methods=['POST'])
@jwt_required()
def like_comment(comment_id):
    try:
        user_id = get_jwt_identity()

        # Check if already liked
        existing_like = comment_likes_collection.find_one({
            'comment_id': ObjectId(comment_id),
            'user_id': ObjectId(user_id)
        })

        if existing_like:
            return jsonify({'error': 'Already liked'}), 400

        # Add like
        comment_likes_collection.insert_one({
            'comment_id': ObjectId(comment_id),
            'user_id': ObjectId(user_id),
            'created_at': datetime.utcnow()
        })

        # Get new like count
        like_count = comment_likes_collection.count_documents({'comment_id': ObjectId(comment_id)})

        return jsonify({'like_count': like_count}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/comments/<comment_id>/like', methods=['DELETE'])
@jwt_required()
def unlike_comment(comment_id):
    try:
        user_id = get_jwt_identity()

        # Remove like
        result = comment_likes_collection.delete_one({
            'comment_id': ObjectId(comment_id),
            'user_id': ObjectId(user_id)
        })

        if result.deleted_count == 0:
            return jsonify({'error': 'Like not found'}), 404

        # Get new like count
        like_count = comment_likes_collection.count_documents({'comment_id': ObjectId(comment_id)})

        return jsonify({'like_count': like_count}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============= END AUTHENTICATION & SOCIAL FEATURES =============

# ============= DATABASE MIGRATION ENDPOINT (ONE-TIME USE) =============

@app.route('/api/admin/migrate-to-user-attributes', methods=['POST'])
def migrate_to_user_attributes():
    """
    One-time migration endpoint to move watchlist, continue_watching, and favorites
    from separate collections to user document attributes.

    Usage: POST with header 'X-Migration-Secret' matching MIGRATION_SECRET env var
    """
    try:
        # Check migration secret
        migration_secret = os.environ.get('MIGRATION_SECRET', '')
        provided_secret = request.headers.get('X-Migration-Secret', '')

        if not migration_secret or provided_secret != migration_secret:
            return jsonify({'error': 'Unauthorized'}), 401

        # Check if old collections still exist
        watchlists_collection = db['watchlists']
        continue_watching_collection = db['continue_watching']
        favorites_collection = db['favorites']

        # Get all users
        users = list(users_collection.find({}))

        migrated_users = 0
        total_watchlist_items = 0
        total_continue_watching_items = 0
        total_favorite_items = 0

        for user in users:
            user_id = user['_id']

            # Initialize arrays (use existing if present, otherwise empty)
            watchlist = user.get('watchlist', [])
            continue_watching = user.get('continue_watching', [])
            favorites = user.get('favorites', [])

            # Track if we need to update this user
            needs_update = False

            # Only migrate from old collections if arrays don't exist yet
            if 'watchlist' not in user:
                needs_update = True
            if 'continue_watching' not in user:
                needs_update = True
            if 'favorites' not in user:
                needs_update = True

            # Skip if user already has all arrays
            if not needs_update:
                continue

            # Migrate watchlist items only if array didn't exist
            if 'watchlist' not in user:
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

            # Migrate continue watching items only if array didn't exist
            if 'continue_watching' not in user:
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

            # Migrate favorites items only if array didn't exist
            if 'favorites' not in user:
                favorite_items = list(favorites_collection.find({'user_id': user_id}))
                for item in favorite_items:
                    favorites.append({
                        'channel_id': item['channel_id'],
                        'channel_name': item['channel_name'],
                        'added_at': item.get('added_at', datetime.utcnow())
                    })
                total_favorite_items += len(favorite_items)

            # Update user document
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

        return jsonify({
            'message': 'Migration completed successfully',
            'migrated_users': migrated_users,
            'total_items': {
                'watchlist': total_watchlist_items,
                'continue_watching': total_continue_watching_items,
                'favorites': total_favorite_items
            }
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============= END DATABASE MIGRATION =============

@app.route('/api/admin/drop-old-collections', methods=['POST'])
def drop_old_collections():
    """
    Drop the old watchlists, continue_watching, and favorites collections
    after confirming migration was successful.

    Usage: POST with header 'X-Migration-Secret' matching MIGRATION_SECRET env var
    """
    try:
        # Check migration secret
        migration_secret = os.environ.get('MIGRATION_SECRET', '')
        provided_secret = request.headers.get('X-Migration-Secret', '')

        if not migration_secret or provided_secret != migration_secret:
            return jsonify({'error': 'Unauthorized'}), 401

        # Get collection stats before dropping
        watchlists_collection = db['watchlists']
        continue_watching_collection = db['continue_watching']
        favorites_collection = db['favorites']

        watchlist_count = watchlists_collection.count_documents({})
        continue_watching_count = continue_watching_collection.count_documents({})
        favorites_count = favorites_collection.count_documents({})

        # Drop the collections
        watchlists_collection.drop()
        continue_watching_collection.drop()
        favorites_collection.drop()

        return jsonify({
            'message': 'Old collections dropped successfully',
            'deleted_collections': {
                'watchlists': watchlist_count,
                'continue_watching': continue_watching_count,
                'favorites': favorites_count
            }
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Get port from environment variable (for Render.com/Railway/Heroku)
    port = int(os.environ.get('PORT', 5001))

    print("Starting RetroFlix API Server...")
    print(f"Server running on port {port}")
    print("API Documentation: /api-docs")
    print("Health Check: /health")
    print("Watchparty: WebSocket enabled for real-time sync!")

    # For production, use gunicorn with: gunicorn --worker-class eventlet -w 1 movie_api:app
    socketio.run(app, debug=False, host='0.0.0.0', port=port)