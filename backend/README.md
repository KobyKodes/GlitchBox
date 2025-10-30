# RetroFlix Backend

Flask backend API for RetroFlix with user authentication, watchlists, friends, and comments.

## Setup

1. Install Python 3.8 or higher

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Install and start MongoDB:
```bash
# On macOS with Homebrew
brew tap mongodb/brew
brew install mongodb-community
brew services start mongodb-community

# On Ubuntu/Debian
sudo apt-get install mongodb
sudo systemctl start mongodb

# On Windows
# Download and install from https://www.mongodb.com/try/download/community
```

4. Create a `.env` file:
```bash
cp .env.example .env
```

5. Edit `.env` and set a secure JWT secret key

6. Run the server:
```bash
python app.py
```

The server will start on `http://localhost:5000`

## API Endpoints

### Authentication
- `POST /api/auth/register` - Register new user
- `POST /api/auth/login` - Login
- `GET /api/auth/me` - Get current user info

### Watchlist
- `GET /api/watchlist` - Get user's watchlist
- `POST /api/watchlist` - Add to watchlist
- `DELETE /api/watchlist/<content_id>` - Remove from watchlist

### Continue Watching
- `GET /api/continue-watching` - Get continue watching list
- `POST /api/continue-watching` - Update progress
- `DELETE /api/continue-watching/<content_id>` - Remove item

### Favorites (Live Channels)
- `GET /api/favorites` - Get favorites
- `POST /api/favorites` - Add favorite
- `DELETE /api/favorites/<channel_id>` - Remove favorite

### Friends
- `GET /api/friends` - Get friends list
- `GET /api/friends/search?q=<username>` - Search users
- `POST /api/friends/request` - Send friend request
- `GET /api/friends/requests` - Get pending requests
- `POST /api/friends/requests/<id>/accept` - Accept request
- `POST /api/friends/requests/<id>/reject` - Reject request
- `DELETE /api/friends/<friend_id>` - Remove friend

### Comments
- `GET /api/comments/<content_id>` - Get comments for content (friends only)
- `POST /api/comments` - Add comment
- `DELETE /api/comments/<comment_id>` - Delete comment

All endpoints except `/api/auth/register` and `/api/auth/login` require JWT authentication via Bearer token in Authorization header.
