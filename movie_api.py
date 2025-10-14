#!/usr/bin/env python3
"""
Movie API Server - Flask backend for TMDB integration
Provides REST API for movie search and streaming URL generation
"""

# IMPORTANT: Eventlet monkey patching MUST be done before any other imports
import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify, render_template_string, send_file, Response
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
from datetime import datetime
import json
import atexit

# Load API keys from environment variables (secure for production)
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', 'e577f3394a629d69efa3a9414e172237')
OMDB_API_KEY = os.environ.get('OMDB_API_KEY', 'ecbf499d')

app = Flask(__name__)
# Generate a secure random secret key for production
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
CORS(app)  # Enable CORS for frontend access
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

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

    def get_tv_season_details(self, tv_id, season_number):
        """Get detailed season information"""
        try:
            # Check cache first
            cache_key = f"{tv_id}_{season_number}"
            if cache_key in season_cache:
                print(f"Loading season {cache_key} from cache")
                return season_cache[cache_key]

            print(f"Fetching season {cache_key} from API (not in cache)")

            params = {"api_key": self.api_key}
            response = requests.get(f"{self.base_url}/tv/{tv_id}/season/{season_number}", params=params)
            response.raise_for_status()
            season = response.json()

            # Add full poster URLs for episodes and fetch OMDB data
            for episode in season.get('episodes', []):
                if episode.get('still_path'):
                    episode['still_url'] = self.image_base_url + episode['still_path']

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

            # Cache the result
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

            # Calculate date range: today to 90 days in the future
            today = datetime.now()
            min_date = today.strftime('%Y-%m-%d')
            max_date = (today + timedelta(days=90)).strftime('%Y-%m-%d')

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
        return send_file(os.path.join(current_dir, 'movie_tv_player.html'))
    except:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return send_file(os.path.join(current_dir, 'movie_search_player.html'))

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
    season = tmdb.get_tv_season_details(tv_id, season_number)
    return jsonify(season)

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

@app.route('/api/stream/<int:movie_id>')
def generate_stream_url(movie_id):
    """Generate VidKing streaming URL"""
    try:
        # Get movie details first
        movie = tmdb.get_movie_details(movie_id)
        if 'error' in movie:
            return jsonify(movie), 404

        vidking_url = f"https://www.vidking.net/embed/movie/{movie_id}"

        return jsonify({
            "movie_id": movie_id,
            "title": movie.get('title'),
            "year": movie.get('release_date', '')[:4] if movie.get('release_date') else None,
            "vidking_url": vidking_url,
            "vidking_autoplay_url": f"{vidking_url}?color=9146ff&autoPlay=true&quality=720p&sub.default=en",
            "movie_details": movie
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tv/stream/<int:tv_id>')
def generate_tv_stream_url(tv_id):
    """Generate VidKing streaming URL for TV show"""
    season = request.args.get('season', 1, type=int)
    episode = request.args.get('episode', 1, type=int)

    try:
        # Get TV show details first
        show = tmdb.get_tv_details(tv_id)
        if 'error' in show:
            return jsonify(show), 404

        # Generate VidKing TV URLs (trying different possible formats)
        base_vidking_url = f"https://www.vidking.net/embed/tv/{tv_id}"
        episode_vidking_url = f"https://www.vidking.net/embed/tv/{tv_id}/{season}/{episode}"

        return jsonify({
            "tv_id": tv_id,
            "title": show.get('name'),
            "year": show.get('first_air_date', '')[:4] if show.get('first_air_date') else None,
            "season": season,
            "episode": episode,
            "vidking_url": base_vidking_url,
            "vidking_episode_url": episode_vidking_url,
            "vidking_autoplay_url": f"{episode_vidking_url}?color=9146ff&autoPlay=true&quality=720p&sub.default=en",
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

@app.route('/sw.js')
def serve_sw():
    """Serve Monetag service worker for push notifications"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_file(os.path.join(current_dir, 'sw.js'), mimetype='application/javascript')

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

            # Notify others in room
            emit('user_left', {
                'username': username,
                'users': list(room['users'].values())
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
        'created_at': datetime.now().isoformat()
    }

    user_rooms[sid] = room_code
    join_room(room_code)

    print(f"Party created: {room_code} by {username}")

    emit('party_created', {
        'room_code': room_code,
        'is_host': True,
        'content': content,
        'users': [username]
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

    # Notify user who joined
    emit('party_joined', {
        'room_code': room_code,
        'is_host': (sid == room['host']),
        'content': room['content'],
        'users': list(room['users'].values()),
        'state': room['state']
    })

    # Notify others in room
    emit('user_joined', {
        'username': username,
        'users': list(room['users'].values())
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

            # Notify others
            emit('user_left', {
                'username': username,
                'users': list(room['users'].values())
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

            emit('play_sync', {
                'currentTime': current_time,
                'username': room['users'][sid]
            }, room=room_code, skip_sid=sid)

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

            emit('pause_sync', {
                'currentTime': current_time,
                'username': room['users'][sid]
            }, room=room_code, skip_sid=sid)

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

            emit('seek_sync', {
                'currentTime': current_time,
                'username': room['users'][sid]
            }, room=room_code, skip_sid=sid)

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
            room['state']['currentTime'] = 0

            emit('stop_sync', {}, room=room_code, skip_sid=sid)

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