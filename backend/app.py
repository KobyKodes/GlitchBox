from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend communication

# Configuration
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-this-in-production')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=7)

jwt = JWTManager(app)

# MongoDB connection
mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri)
db = client['retroflix']

# Collections
users_collection = db['users']
watchlists_collection = db['watchlists']
continue_watching_collection = db['continue_watching']
favorites_collection = db['favorites']
comments_collection = db['comments']
friend_requests_collection = db['friend_requests']

# Create indexes
users_collection.create_index('username', unique=True)
users_collection.create_index('email', unique=True)
watchlists_collection.create_index([('user_id', 1), ('content_id', 1)])
continue_watching_collection.create_index([('user_id', 1), ('content_id', 1)])
favorites_collection.create_index([('user_id', 1), ('channel_id', 1)])
comments_collection.create_index([('content_id', 1), ('user_id', 1)])

# ============= AUTH ROUTES =============

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
            'friends': []
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
        watchlist_items = list(watchlists_collection.find({'user_id': ObjectId(user_id)}))

        # Convert ObjectId to string for JSON serialization
        for item in watchlist_items:
            item['_id'] = str(item['_id'])
            item['user_id'] = str(item['user_id'])

        return jsonify(watchlist_items), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist', methods=['POST'])
@jwt_required()
def add_to_watchlist():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        # Check if already in watchlist
        existing = watchlists_collection.find_one({
            'user_id': ObjectId(user_id),
            'content_id': data['content_id']
        })

        if existing:
            return jsonify({'error': 'Item already in watchlist'}), 400

        watchlist_item = {
            'user_id': ObjectId(user_id),
            'content_id': data['content_id'],
            'content_type': data['content_type'],
            'title': data['title'],
            'poster_path': data.get('poster_path'),
            'added_at': datetime.utcnow()
        }

        result = watchlists_collection.insert_one(watchlist_item)
        watchlist_item['_id'] = str(result.inserted_id)
        watchlist_item['user_id'] = str(watchlist_item['user_id'])

        return jsonify(watchlist_item), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/watchlist/<content_id>', methods=['DELETE'])
@jwt_required()
def remove_from_watchlist(content_id):
    try:
        user_id = get_jwt_identity()

        result = watchlists_collection.delete_one({
            'user_id': ObjectId(user_id),
            'content_id': content_id
        })

        if result.deleted_count == 0:
            return jsonify({'error': 'Item not found in watchlist'}), 404

        return jsonify({'message': 'Removed from watchlist'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============= CONTINUE WATCHING ROUTES =============

@app.route('/api/continue-watching', methods=['GET'])
@jwt_required()
def get_continue_watching():
    try:
        user_id = get_jwt_identity()
        items = list(continue_watching_collection.find({'user_id': ObjectId(user_id)}).sort('last_watched', -1))

        for item in items:
            item['_id'] = str(item['_id'])
            item['user_id'] = str(item['user_id'])

        return jsonify(items), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/continue-watching', methods=['POST'])
@jwt_required()
def update_continue_watching():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        # Update or insert
        continue_watching_collection.update_one(
            {
                'user_id': ObjectId(user_id),
                'content_id': data['content_id']
            },
            {
                '$set': {
                    'content_type': data['content_type'],
                    'title': data['title'],
                    'poster_path': data.get('poster_path'),
                    'progress': data.get('progress', 0),
                    'season': data.get('season'),
                    'episode': data.get('episode'),
                    'last_watched': datetime.utcnow()
                }
            },
            upsert=True
        )

        return jsonify({'message': 'Continue watching updated'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/continue-watching/<content_id>', methods=['DELETE'])
@jwt_required()
def remove_from_continue_watching(content_id):
    try:
        user_id = get_jwt_identity()

        result = continue_watching_collection.delete_one({
            'user_id': ObjectId(user_id),
            'content_id': content_id
        })

        if result.deleted_count == 0:
            return jsonify({'error': 'Item not found'}), 404

        return jsonify({'message': 'Removed from continue watching'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============= FAVORITES (LIVE CHANNELS) ROUTES =============

@app.route('/api/favorites', methods=['GET'])
@jwt_required()
def get_favorites():
    try:
        user_id = get_jwt_identity()
        favorites = list(favorites_collection.find({'user_id': ObjectId(user_id)}))

        for item in favorites:
            item['_id'] = str(item['_id'])
            item['user_id'] = str(item['user_id'])

        return jsonify(favorites), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/favorites', methods=['POST'])
@jwt_required()
def add_to_favorites():
    try:
        user_id = get_jwt_identity()
        data = request.get_json()

        # Check if already in favorites
        existing = favorites_collection.find_one({
            'user_id': ObjectId(user_id),
            'channel_id': data['channel_id']
        })

        if existing:
            return jsonify({'error': 'Channel already in favorites'}), 400

        favorite_item = {
            'user_id': ObjectId(user_id),
            'channel_id': data['channel_id'],
            'channel_name': data['channel_name'],
            'added_at': datetime.utcnow()
        }

        result = favorites_collection.insert_one(favorite_item)
        favorite_item['_id'] = str(result.inserted_id)
        favorite_item['user_id'] = str(favorite_item['user_id'])

        return jsonify(favorite_item), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/favorites/<path:channel_id>', methods=['DELETE'])
@jwt_required()
def remove_from_favorites(channel_id):
    try:
        user_id = get_jwt_identity()

        result = favorites_collection.delete_one({
            'user_id': ObjectId(user_id),
            'channel_id': channel_id
        })

        if result.deleted_count == 0:
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

        result = favorites_collection.delete_one({
            'user_id': ObjectId(user_id),
            'channel_id': channel_id
        })

        if result.deleted_count == 0:
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
        user = users_collection.find_one({'_id': ObjectId(user_id)})

        if not user:
            return jsonify({'error': 'User not found'}), 404

        friend_ids = user.get('friends', [])
        friends = list(users_collection.find({'_id': {'$in': friend_ids}}))

        friends_list = [{
            'id': str(friend['_id']),
            'username': friend['username']
        } for friend in friends]

        return jsonify(friends_list), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/friends/search', methods=['GET'])
@jwt_required()
def search_users():
    try:
        query = request.args.get('q', '')
        user_id = get_jwt_identity()

        if not query:
            return jsonify([]), 200

        users = list(users_collection.find({
            'username': {'$regex': query, '$options': 'i'},
            '_id': {'$ne': ObjectId(user_id)}
        }).limit(10))

        results = [{
            'id': str(user['_id']),
            'username': user['username']
        } for user in users]

        return jsonify(results), 200

    except Exception as e:
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

        # Get current user's friends
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        friend_ids = user.get('friends', [])
        friend_ids.append(ObjectId(user_id))  # Include own comments

        # Get comments from friends only
        comments = list(comments_collection.find({
            'content_id': content_id,
            'user_id': {'$in': friend_ids}
        }).sort('created_at', -1))

        for comment in comments:
            comment['_id'] = str(comment['_id'])
            comment['user_id'] = str(comment['user_id'])
            comment['created_at'] = comment['created_at'].isoformat()

        return jsonify(comments), 200

    except Exception as e:
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

        result = comments_collection.insert_one(comment)
        comment['_id'] = str(result.inserted_id)
        comment['user_id'] = str(comment['user_id'])
        comment['created_at'] = comment['created_at'].isoformat()

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

        return jsonify({'message': 'Comment deleted'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5001)
