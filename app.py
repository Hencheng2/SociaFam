import sqlite3
import os
from datetime import datetime, timedelta, timezone
import random
import string
import json
import uuid # Import uuid for generating unique IDs

# Assuming these are set up if you're using Google Gemini API
import google.generativeai as genai

# Assuming these are set up if you're using Firebase for chat background storage
import firebase_admin
from firebase_admin import credentials, initialize_app, firestore


from flask import Flask, render_template, request, redirect, url_for, g, flash, session, abort, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_moment import Moment # Make sure this is imported


import config # Assuming config.py exists for SECRET_KEY

# Gevent imports for SocketIO on PythonAnywhere
import gevent
from gevent import monkey
monkey.patch_all() # Must be called as early as possible after imports

# Flask-SocketIO imports
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)

# Use environment variable for SECRET_KEY or fall back to config.py
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', config.SECRET_KEY)

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'family_tree.db')

app.config['UPLOAD_FOLDER'] = os.path.join('static', 'img', 'profile_photos')
app.config['UPLOAD_VIDEO_FOLDER'] = os.path.join('static', 'videos', 'status_videos')
# NEW: Chat media folders as required by the provided view_conversation route
app.config['UPLOAD_CHAT_PHOTO_FOLDER'] = os.path.join('static', 'chat_media', 'photos')
app.config['UPLOAD_CHAT_VIDEO_FOLDER'] = os.path.join('static', 'chat_media', 'videos')
app.config['UPLOAD_CHAT_AUDIO_FOLDER'] = os.path.join('static', 'chat_media', 'audio') # Added for audio uploads
app.config['UPLOAD_CHAT_BACKGROUND_FOLDER'] = os.path.join('static', 'img', 'chat_backgrounds')

app.config['ADMIN_USERNAME'] = config.ADMIN_USERNAME
app.config['ADMIN_PASS'] = config.ADMIN_PASS
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 # 5 MB limit for uploads

# Ensure upload folders exist (including newly added chat media folders)
os.makedirs(os.path.join(app.root_path, app.config['UPLOAD_FOLDER']), exist_ok=True)
os.makedirs(os.path.join(app.root_path, app.config['UPLOAD_VIDEO_FOLDER']), exist_ok=True)
os.makedirs(os.path.join(app.root_path, app.config['UPLOAD_CHAT_PHOTO_FOLDER']), exist_ok=True)
os.makedirs(os.path.join(app.root_path, app.config['UPLOAD_CHAT_VIDEO_FOLDER']), exist_ok=True)
os.makedirs(os.path.join(app.root_path, app.config['UPLOAD_CHAT_AUDIO_FOLDER']), exist_ok=True) # Ensure audio folder exists
os.makedirs(os.path.join(app.root_path, app.config['UPLOAD_CHAT_BACKGROUND_FOLDER']), exist_ok=True)


# Initialize Flask-Moment
moment = Moment(app)

# Initialize Flask-SocketIO with gevent async mode
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent') # Corrected initialization

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
# --- SQLite3 Database Functions ---
def get_db():
    # Establishes a database connection for the current request
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(
            DATABASE,
            detect_types=sqlite3.PARSE_DECLTYPES # This is what causes datetime objects to be returned
        )
        db.row_factory = sqlite3.Row # Allows accessing columns by name
    return db

@app.teardown_appcontext
def close_connection(exception):
    # Closes the database connection at the end of the request
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    # Initializes the database schema from a SQL script
    with app.app_context():
        db = get_db()
        # Define the schema content directly, strictly removing Raphael-related fields
        # AND ensuring the `messages` table is correctly structured for direct chat.
        schema_sql_content = """
            -- schema.sql (ADAPTED per user's latest strict instructions)

            -- Drop existing tables (order matters due to foreign keys)
            DROP TABLE IF EXISTS game_invitations;
            DROP TABLE IF EXISTS game_moves;
            DROP TABLE IF EXISTS multiplayer_games;
            DROP TABLE IF EXISTS messages; -- Keep this for direct messages
            DROP TABLE IF EXISTS statuses;
            DROP TABLE IF EXISTS members;
            DROP TABLE IF EXISTS users;
            -- Explicitly dropping chat_rooms and chat_room_members as requested by user's prior file
            -- These tables are NOT being recreated as direct messaging will now use the 'messages' table directly.
            DROP TABLE IF EXISTS chat_messages;
            DROP TABLE IF EXISTS chat_room_members;
            DROP TABLE IF EXISTS chat_rooms;


            -- Create users table (REMOVED: relationshipToRaphael)
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                originalName TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                theme_preference TEXT DEFAULT 'light',
                chat_background_image_path TEXT,
                unique_key TEXT UNIQUE NOT NULL,
                password_reset_pending INTEGER DEFAULT 0,
                reset_request_timestamp TIMESTAMP,
                last_login_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                admin_reset_approved INTEGER DEFAULT 0
            );

            -- Create members table (REMOVED: isRaphaelDescendant, association, personalRelationshipDescription)
            CREATE TABLE members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fullName TEXT NOT NULL,
                gender TEXT NOT NULL,
                dateOfBirth TEXT,
                maritalStatus TEXT,
                spouseNames TEXT,
                fianceNames TEXT,
                childrenNames TEXT,
                educationLevel TEXT,
                schoolName TEXT,
                whereabouts TEXT,
                phoneNumber TEXT,
                emailContact TEXT,
                otherContact TEXT,
                bio TEXT,
                profilePhoto TEXT,
                user_id INTEGER UNIQUE,
                needs_details_update INTEGER DEFAULT 0,
                added_by_user_id INTEGER NOT NULL,
                can_message INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (added_by_user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- Create messages table (ADAPTED to store ALL direct messages, including media)
            -- This table replaces chat_messages for direct human-to-human conversations.
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                content TEXT, -- Made nullable to allow media-only messages
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read INTEGER DEFAULT 0,
                is_admin_message INTEGER DEFAULT 0, -- Used for AI messages (though AI will use Firestore in this app)
                media_path TEXT, -- Path to media file
                media_type TEXT, -- 'image', 'video', 'audio'
                FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- Create statuses table (for ephemeral video/image statuses)
            CREATE TABLE statuses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER UNIQUE NOT NULL,
                file_path TEXT NOT NULL,
                upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_video INTEGER DEFAULT 0,
                uploader_user_id INTEGER NOT NULL,
                FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
                FOREIGN KEY (uploader_user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- NEW TABLE FOR GAME INVITATIONS (Retained from app(6).py)
            CREATE TABLE game_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                status TEXT DEFAULT 'pending', -- 'pending', 'accepted', 'declined'
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                game_uuid TEXT UNIQUE, -- Added for game tracking
                FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- NEW TABLE: Multiplayer Games for Chess (Retained from app(6).py)
            CREATE TABLE multiplayer_games (
                game_uuid TEXT PRIMARY KEY,
                game_name TEXT NOT NULL,
                player_white_id INTEGER NOT NULL,
                player_black_id INTEGER NOT NULL,
                current_board_state TEXT NOT NULL,
                current_turn TEXT NOT NULL,
                white_captures INTEGER DEFAULT 0,
                black_captures INTEGER DEFAULT 0,
                castling_rights TEXT,
                en_passant_target TEXT,
                last_move TEXT,
                game_over INTEGER DEFAULT 0,
                winner_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (player_white_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (player_black_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- NEW TABLE: Game Moves (Retained from app(6).py)
            CREATE TABLE game_moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_uuid TEXT NOT NULL,
                move_number INTEGER NOT NULL, -- Fullmove number
                player_id INTEGER NOT NULL,
                move_data TEXT NOT NULL, -- JSON string of move details
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_uuid) REFERENCES multiplayer_games(game_uuid) ON DELETE CASCADE,
                FOREIGN KEY (player_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """
        db.executescript(schema_sql_content)
        db.commit()
        print("Database initialized.")

@app.cli.command('initdb')
def init_db_command():
    """Initializes the database."""
    init_db()
    print('Initialized the database.')

# --- Custom Model Classes (Aligned with the strictly modified schema) ---

class User(UserMixin):
    # Removed relationshipToRaphael from __init__ and properties
    def __init__(self, id, username, originalName, password_hash, is_admin, theme_preference, chat_background_image_path, unique_key, password_reset_pending, reset_request_timestamp, last_login_at, last_seen_at, admin_reset_approved):
        self.id = id
        self.username = username
        self.originalName = originalName
        self.password_hash = password_hash
        self.is_admin = is_admin
        self.theme_preference = theme_preference
        self.chat_background_image_path = chat_background_image_path
        self.unique_key = unique_key
        self.password_reset_pending = password_reset_pending
        self.reset_request_timestamp = reset_request_timestamp
        self.last_login_at = last_login_at
        self.last_seen_at = last_seen_at
        self.admin_reset_approved = admin_reset_approved

    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    # --- Check for the hardcoded Admin user (ID 0) first ---
    if user_id == '0' or user_id == 0:
        return User(
            id=0,
            username=config.ADMIN_USERNAME,
            originalName='Admin User',
            password_hash='not-a-real-hash-for-in-memory-admin',
            is_admin=1,
            theme_preference='dark',
            chat_background_image_path=None,
            unique_key='ADM0',
            password_reset_pending=0,
            reset_request_timestamp=None,
            last_login_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            admin_reset_approved=1
        )

    # --- Existing logic for regular users (fetches from database) ---
    db = get_db()
    try:
        user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if user:
            return User(
                id=user['id'],
                username=user['username'],
                originalName=user['originalName'],
                password_hash=user['password_hash'],
                is_admin=bool(user['is_admin']),
                theme_preference=user['theme_preference'],
                chat_background_image_path=user['chat_background_image_path'],
                unique_key=user['unique_key'],
                password_reset_pending=bool(user['password_reset_pending']),
                reset_request_timestamp=user['reset_request_timestamp'],
                last_login_at=user['last_login_at'],
                last_seen_at=user['last_seen_at'],
                admin_reset_approved=bool(user['admin_reset_approved'])
            )
    except sqlite3.Error as e:
        print(f"Database error in load_user: {e}")
    return None

class Member: # Removed isRaphaelDescendant, association, personalRelationshipDescription from __init__ and properties
    def __init__(self, id, fullName, dateOfBirth, gender, maritalStatus, spouseNames, fianceNames, childrenNames, educationLevel, schoolName, whereabouts, phoneNumber, emailContact, otherContact, bio, profilePhoto, user_id, needs_details_update, added_by_user_id, can_message):
        self.id = id
        self.fullName = fullName
        self.dateOfBirth = dateOfBirth
        self.gender = gender
        self.maritalStatus = maritalStatus
        self.spouseNames = spouseNames
        self.fianceNames = fianceNames
        self.childrenNames = childrenNames
        self.educationLevel = educationLevel
        self.schoolName = schoolName
        self.whereabouts = whereabouts
        self.phoneNumber = phoneNumber
        self.emailContact = emailContact
        self.otherContact = otherContact
        self.bio = bio
        self.profilePhoto = profilePhoto
        self.user_id = user_id
        self.needs_details_update = needs_details_update
        self.added_by_user_id = added_by_user_id
        self.can_message = can_message

    @property
    def user(self):
        if self.user_id:
            return load_user(self.user_id)
        return None

class Status: # Retained from app(6).py
    def __init__(self, id, file_path, upload_time, is_video, member_id, uploader_user_id):
        self.id = id
        self.file_path = file_path
        self.upload_time = upload_time
        self.is_video = is_video
        self.member_id = member_id
        self.uploader_user_id = uploader_user_id

class Message: # ADAPTED: Added media_path, media_type, is_admin_message
    def __init__(self, id, sender_id, recipient_id, content, timestamp, is_read, is_admin_message, media_path=None, media_type=None):
        self.id = id
        self.sender_id = sender_id
        self.recipient_id = recipient_id
        self.content = content
        self.timestamp = timestamp
        self.is_read = is_read
        self.is_admin_message = is_admin_message
        self.media_path = media_path
        self.media_type = media_type

    @property
    def sender(self):
        return load_user(self.sender_id)

    @property
    def recipient(self):
        return load_user(self.recipient_id)

class GameInvitation: # Retained from app(6).py
    def __init__(self, id, sender_id, recipient_id, game_name, status, timestamp, game_uuid):
        self.id = id
        self.sender_id = sender_id
        self.recipient_id = recipient_id
        self.game_name = game_name
        self.status = status
        self.timestamp = timestamp
        self.game_uuid = game_uuid

    @property
    def sender(self):
        return load_user(self.sender_id)

    @property
    def recipient(self):
        return load_user(self.recipient_id)


# --- Firebase Admin SDK Initialization (Retained) ---
firestore_db = None
try:
    if not firebase_admin._apps:
        cred_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.FIREBASE_ADMIN_CREDENTIALS_PATH)
        cred = credentials.Certificate(cred_path)
        initialize_app(cred)
    firestore_db = firestore.client()
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}. Please ensure '{config.FIREBASE_ADMIN_CREDENTIALS_PATH}' is in your project directory and is a valid JSON key file.")


# --- Gemini API Configuration (Retained) ---
# Using GENAI_API_KEY from os.getenv as per user's prompt snippet for view_conversation
# And also falling back to config.GEMINI_API_KEY as per general practice.
GEMINI_API_KEY = os.getenv('GENAI_API_KEY', config.GEMINI_API_KEY)
if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
    print("WARNING: GEMINI_API_KEY is not set in config.py or is still a placeholder. AI chat will not function.")
genai.configure(api_key=GEMINI_API_KEY)


# --- Helper functions (Adapted to remove chat room references, add media allowed types) ---
def allowed_file(filename):
    # Allowed extensions for profile photos and general images
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_video_file(filename):
    # Allowed extensions for status videos
    ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'webm'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS

def allowed_chat_image_file(filename):
    # Allowed extensions for chat images (newly added for clarity)
    ALLOWED_CHAT_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_CHAT_IMAGE_EXTENSIONS

def allowed_chat_video_file(filename):
    # Allowed extensions for chat videos (newly added for clarity)
    ALLOWED_CHAT_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'webm'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_CHAT_VIDEO_EXTENSIONS

def allowed_chat_audio_file(filename):
    # Allowed extensions for chat audio (newly added for clarity)
    ALLOWED_CHAT_AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_CHAT_AUDIO_EXTENSIONS

def allowed_background_image_file(filename):
    # Allowed extensions for chat background images
    ALLOWED_BACKGROUND_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_BACKGROUND_IMAGE_EXTENSIONS

def generate_unique_key():
    # Generates a unique key for user password recovery
    nums = ''.join(random.choices(string.digits, k=2))
    chars = ''.join(random.choices(string.ascii_uppercase, k=2))
    return f"{nums}{chars}"

def calculate_age(dob):
    # Calculates age from a date of birth string or datetime object
    if not dob:
        return None
    try:
        if isinstance(dob, datetime):
            birth_date = dob.date()
        elif isinstance(dob, str):
            if dob.strip() == '':
                return None
            birth_date = datetime.strptime(dob, '%Y-%m-%d').date()
        else:
            return None
        today = datetime.now().date()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        return age
    except ValueError:
        return None
    except Exception as e:
        print(f"Error calculating age for DOB {dob}: {e}")
        return None

def get_current_user_member_profile():
    # Retrieves the member profile linked to the current logged-in user
    if current_user.is_authenticated:
        db = get_db()
        member_data = db.execute('SELECT * FROM members WHERE user_id = ?', (current_user.id,)).fetchone()
        if member_data:
            return Member(
                id=member_data['id'],
                fullName=member_data['fullName'],
                dateOfBirth=member_data['dateOfBirth'],
                gender=member_data['gender'],
                maritalStatus=member_data['maritalStatus'],
                spouseNames=member_data['spouseNames'],
                fianceNames=member_data['fianceNames'],
                childrenNames=member_data['childrenNames'],
                educationLevel=member_data['educationLevel'],
                schoolName=member_data['schoolName'],
                whereabouts=member_data['whereabouts'],
                phoneNumber=member_data['phoneNumber'],
                emailContact=member_data['emailContact'],
                otherContact=member_data['otherContact'],
                bio=member_data['bio'],
                profilePhoto=member_data['profilePhoto'],
                user_id=member_data['user_id'],
                needs_details_update=member_data['needs_details_update'],
                added_by_user_id=member_data['added_by_user_id'],
                can_message=member_data['can_message']
            )
    return None

def get_unread_messages_count():
    # Counts unread direct messages for the current user from the 'messages' table.
    # A message is unread if it was sent by someone else and its 'is_read' flag is 0.
    if current_user.is_authenticated:
        try:
            db = get_db()
            count_messages = db.execute(
                'SELECT COUNT(*) FROM messages WHERE recipient_id = ? AND is_read = 0 AND sender_id != ?',
                (current_user.id, current_user.id)
            ).fetchone()[0]
            return count_messages
        except sqlite3.OperationalError:
            print("Skipping unread message count due to missing 'messages' table or column.")
            return 0
        except Exception as e:
            print(f"Error getting unread messages count: {e}")
            return 0
    return 0

def get_unread_game_invite_count():
    # Counts pending game invitations for the current user from Firestore
    if current_user.is_authenticated and firestore_db:
        try:
            games_ref = firestore_db.collection(f'artifacts/{config.CANVAS_APP_ID}/public/games')

            # Count games where current user is playerWhiteId and game is not over
            invites_as_white_query = games_ref.where('playerWhiteId', '==', str(current_user.id)).where('gameOver', '==', False).where('gameType', '==', 'human_vs_human')
            invites_as_white_docs = invites_as_white_query.stream()
            count_as_white = sum(1 for doc in invites_as_white_docs)

            # Count games where current user is playerBlackId and game is not over
            invites_as_black_query = games_ref.where('playerBlackId', '==', str(current_user.id)).where('gameOver', '==', False).where('gameType', '==', 'human_vs_human')
            invites_as_black_docs = invites_as_black_query.stream()
            count_as_black = sum(1 for doc in invites_as_black_docs)

            # Total unread invites are the sum of games where they are a participant and it's not over
            return count_as_white + count_as_black
        except Exception as e:
            print(f"Error getting unread game invite count from Firestore: {e}")
            return 0
    return 0


def cleanup_expired_videos():
    # Deletes expired status videos and their database entries
    db = get_db()
    now = datetime.utcnow()
    expiration_threshold = now - timedelta(hours=12)

    try:
        expired_videos = db.execute('SELECT id, file_path FROM statuses WHERE upload_time < ?', (expiration_threshold,)).fetchall()

        for video in expired_videos:
            # Ensure path is relative to app.root_path before os.path.join
            video_path = os.path.join(app.root_path, video['file_path'])
            if os.path.exists(video_path):
                try:
                    os.remove(video_path)
                    print(f"Deleted expired status file: {video_path}")
                except OSError as e:
                    print(f"Error deleting status file {video_path}: {e}")
            else:
                print(f"Expired status file not found on disk: {video['file_path']}, removing DB entry anyway.")

            db.execute('DELETE FROM statuses WHERE id = ?', (video['id'],))
            db.commit()
            print(f"Removed expired status DB entry for ID: {video['id']}")
    except sqlite3.OperationalError as e:
        print(f"Skipping cleanup_expired_videos due to missing table or column: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during cleanup_expired_videos: {e}")


def create_ai_user_and_member():
    # Ensures a special AI user and their member profile exist in the database
    db_conn = get_db()
    ai_username = "AdminAI"
    ai_original_name = "Admin AI"
    ai_unique_key = "AI001"
    ai_gender = "Other"

    ai_user_data = db_conn.execute('SELECT id FROM users WHERE username = ?', (ai_username,)).fetchone()

    if not ai_user_data:
        ai_password_hash = generate_password_hash(config.AI_USER_PASSWORD)
        # Removed relationshipToRaphael from users table insert
        db_conn.execute(
            'INSERT INTO users (username, password_hash, originalName, is_admin, unique_key) VALUES (?, ?, ?, ?, ?)',
            (ai_username, ai_password_hash, ai_original_name, 0, ai_unique_key)
        )
        db_conn.commit()
        ai_user_id = db_conn.execute('SELECT id FROM users WHERE username = ?', (ai_username,)).fetchone()[0]
        print(f"Created new AI user with ID: {ai_user_id}")
    else:
        ai_user_id = ai_user_data[0]
        print(f"AI user already exists with ID: {ai_user_id}")

    ai_member_data = db_conn.execute('SELECT id FROM members WHERE user_id = ?', (ai_user_id,)).fetchone()
    if not ai_member_data:
        # Removed association, personalRelationshipDescription, isRaphaelDescendant from members table insert
        db_conn.execute(
            'INSERT INTO members (fullName, user_id, can_message, gender, added_by_user_id, profilePhoto) VALUES (?, ?, ?, ?, ?, ?)',
            (ai_original_name, ai_user_id, 1, ai_gender, ai_user_id, os.path.join(app.config['UPLOAD_FOLDER'], 'ai_icon.png').replace('\\', '/'))
        )
        db_conn.commit()
        print(f"Created member profile for AI user {ai_user_id}.")
    else:
        # Update profile photo and other potential fields if they've changed
        current_ai_photo = db_conn.execute('SELECT profilePhoto FROM members WHERE user_id = ?', (ai_user_id,)).fetchone()
        expected_ai_photo_path = os.path.join(app.config['UPLOAD_FOLDER'], 'ai_icon.png').replace('\\', '/')
        if not current_ai_photo or current_ai_photo['profilePhoto'] != expected_ai_photo_path:
            # Removed association, personalRelationshipDescription from members table update
            db_conn.execute('UPDATE members SET profilePhoto = ? WHERE user_id = ?', (expected_ai_photo_path, ai_user_id))
            db_conn.commit() # Commit after update
            print(f"Updated AI member profile photo and details for AI user {ai_user_id}.")

def generate_ai_response(user_message, chat_history):
    # Generates a response from the Gemini AI model using chat history for context
    try:
        model = genai.GenerativeModel('gemini-pro')
        chat = model.start_chat(history=chat_history)
        response = chat.send_message(user_message)
        return response.text
    except Exception as e:
        print(f"Error generating AI response: {e}")
        return "I'm sorry, I couldn't process that. Please try again later."


# --- Run AI user creation and DB initialization on app startup ---
with app.app_context():
    db_file_exists = os.path.exists(DATABASE)
    db_conn = get_db() # Get a connection

    if not db_file_exists:
        print("Database file not found, initializing fresh DB...")
        init_db() # This will create the DB and all tables
        print("Database initialized.")
    else:
        try:
            # Check for core tables to indicate schema is up-to-date
            db_conn.execute("SELECT id FROM users LIMIT 1")
            db_conn.execute("SELECT id FROM members LIMIT 1")
            db_conn.execute("SELECT id FROM messages LIMIT 1") # Check for messages table
            db_conn.execute("SELECT id FROM statuses LIMIT 1")
            db_conn.execute("SELECT id FROM game_invitations LIMIT 1")
            db_conn.execute("SELECT id FROM multiplayer_games LIMIT 1")
            db_conn.execute("SELECT id FROM game_moves LIMIT 1")
            print("Database file exists and schema appears up-to-date.")
        except sqlite3.OperationalError as e:
            print(f"WARNING: Database schema might be outdated or incomplete ({e}).")
            print("To apply the latest schema, please delete 'family_tree.db' manually from your PythonAnywhere 'Files' tab and then reload your web application.")
            print("Note: This will delete all existing data.")
        except Exception as e:
            print(f"An unexpected error occurred during database schema check: {e}")

    # Ensure AI user is created/updated after DB is confirmed to be initialized
    create_ai_user_and_member()

# --- Global context processor ---
@app.context_processor
def inject_global_template_vars():
    # Makes common variables available to all templates
    client_firebase_config = config.FIREBASE_CLIENT_CONFIG
    initial_auth_token = getattr(g, 'initial_auth_token', '')

    unread_messages_count = 0
    unread_game_invite_count = 0

    if current_user.is_authenticated:
        unread_messages_count = get_unread_messages_count()
        unread_game_invite_count = get_unread_game_invite_count()

    return {
        'moment': moment, # Flask-Moment instance
        'now': datetime.utcnow(), # Python datetime object
        'config': app.config,
        'firebase_config_json': json.dumps(client_firebase_config),
        'initial_auth_token': initial_auth_token,
        'current_user': current_user,
        'unread_messages_count': unread_messages_count,
        'unread_game_invite_count': unread_game_invite_count,
        'canvas_app_id': config.CANVAS_APP_ID
    }

# --- Before Request Hook ---
@app.before_request
def before_request_hook():
    # Updates last_seen_at for logged-in users and fetches global data
    db = get_db()

    g.user_member = get_current_user_member_profile()
    g.unread_messages_count = get_unread_messages_count()
    g.unread_game_invite_count = get_unread_game_invite_count()
    cleanup_expired_videos()

    if current_user.is_authenticated:
        db.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', (datetime.utcnow(), current_user.id))
        db.commit()

        g.user_theme = current_user.theme_preference
        g.user_chat_background = current_user.chat_background_image_path
        g.user_unique_key = current_user.unique_key
    else:
        g.user_theme = request.cookies.get('theme', 'light')
        g.user_chat_background = None
        g.user_unique_key = None

# --- API Route for AI Chat (Retained from `app (6).py`'s API logic as per its `app.route('/api/send_ai_message')` route) ---
@app.route('/api/send_ai_message', methods=['POST'])
@login_required
def send_ai_message():
    # Handles sending messages to the AI and getting a response via Firestore
    if not firestore_db:
        print("Firestore DB not initialized, cannot send AI message.")
        return jsonify({'error': 'AI service not available. Firestore not initialized.'}), 500

    data = request.get_json()
    user_message = data.get('message')
    human_user_id = data.get('humanUserId')

    if not user_message or not human_user_id:
        return jsonify({'error': 'Message or human user ID missing.'}), 400

    db_conn = get_db()
    ai_user_data = db_conn.execute('SELECT id FROM users WHERE username = ?', ('AdminAI',)).fetchone()
    if not ai_user_data:
        print("AdminAI user not found in SQLite DB.")
        return jsonify({'error': 'AI user not configured.'}), 500
    ai_user_id = str(ai_user_data[0])

    human_user_id_str = str(human_user_id)

    # Firestore path for AI conversation with a specific user
    chat_collection_path = f'artifacts/{config.CANVAS_APP_ID}/users/{human_user_id_str}/conversations/{ai_user_id}/messages'

    try:
        # Save user's message to Firestore
        firestore_db.collection(chat_collection_path).add({
            'senderId': human_user_id_str,
            'message': user_message,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'isAI': False
        })

        # Fetch recent messages for context
        history = []
        messages_ref = firestore_db.collection(chat_collection_path).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(5)
        docs = messages_ref.get()
        for doc in docs:
            msg = doc.to_dict()
            role = "model" if msg.get('isAI') else "user"
            history.append({"role": role, "parts": [{"text": msg.get('message', '')}]})
        history.reverse() # Oldest messages first for Gemini

        # Get AI response
        model = genai.GenerativeModel('gemini-pro')
        gemini_response = model.generate_content(history)
        ai_response_text = gemini_response.text

        # Save AI's response to Firestore
        firestore_db.collection(chat_collection_path).add({
            'senderId': ai_user_id,
            'message': ai_response_text,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'isAI': True
        })

        return jsonify({'success': True, 'response': ai_response_text})

    except Exception as e:
        print(f"Error communicating with AI or Firestore: {e}")
        return jsonify({'error': f'Failed to get AI response: {e}'}), 500


# --- ROUTES (Adapted as per instructions) ---

@app.route('/')
def root_redirect():
    # Redirects to login if not authenticated, else to home
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return redirect(url_for('home'))

# Retained as per app(6).py's original structure
@app.route('/home')
@login_required
def home():
    # Renders the home page with dynamic content
    background_image = url_for('static', filename='img/Nyangabackground.jpg')
    return render_template('index.html',
                           background_image=background_image,
                           member=g.user_member,
                           unread_messages_count=g.unread_messages_count,
                           unread_game_invite_count=g.unread_game_invite_count)

# Retained for serving uploaded profile photos and general images
# NOTE: Changed from /uploaded_file/<filename> to /uploads/<path:filename> for consistency in routes
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    # Ensure the file is served from the correct, secure upload folder
    return send_from_directory(os.path.join(app.root_path, app.config['UPLOAD_FOLDER']), os.path.basename(filename))

# Retained for serving uploaded status videos
# NOTE: Changed from /uploaded_video/<filename> to /videos/<path:filename> for consistency in routes
@app.route('/videos/<path:filename>')
def uploaded_video(filename):
    # Ensure the file is served from the correct, secure video folder
    return send_from_directory(os.path.join(app.root_path, app.config['UPLOAD_VIDEO_FOLDER']), os.path.basename(filename))

# NEW: Route to serve uploaded chat photos
@app.route('/chat_media/photos/<path:filename>')
def chat_photo(filename):
    return send_from_directory(os.path.join(app.root_path, app.config['UPLOAD_CHAT_PHOTO_FOLDER']), os.path.basename(filename))

# NEW: Route to serve uploaded chat videos
@app.route('/chat_media/videos/<path:filename>')
def chat_video(filename):
    return send_from_directory(os.path.join(app.root_path, app.config['UPLOAD_CHAT_VIDEO_FOLDER']), os.path.basename(filename))

# NEW: Route to serve uploaded chat audio
@app.route('/chat_media/audio/<path:filename>')
def chat_audio(filename):
    return send_from_directory(os.path.join(app.root_path, app.config['UPLOAD_CHAT_AUDIO_FOLDER']), os.path.basename(filename))

# Retained for serving chat backgrounds
@app.route('/chat_backgrounds/<path:filename>')
def chat_backgrounds(filename):
    return send_from_directory(os.path.join(app.root_path, app.config['UPLOAD_CHAT_BACKGROUND_FOLDER']), os.path.basename(filename))

# NEW: Route to handle downloading chat media securely
@app.route('/download_chat_media/<path:filename>')
@login_required
def download_chat_media(filename):
    db = get_db()

    # Extract just the basename from the full path provided in 'filename'
    actual_filename = os.path.basename(filename)

    # Determine the correct directory based on the file extension
    file_extension = os.path.splitext(actual_filename)[1].lower()

    directory = None
    # Use helper functions for allowed types, ensuring consistency
    if file_extension in allowed_chat_image_file(actual_filename):
        directory = os.path.join(app.root_path, app.config['UPLOAD_CHAT_PHOTO_FOLDER'])
    elif file_extension in allowed_chat_video_file(actual_filename):
        directory = os.path.join(app.root_path, app.config['UPLOAD_CHAT_VIDEO_FOLDER'])
    elif file_extension in allowed_chat_audio_file(actual_filename):
        directory = os.path.join(app.root_path, app.config['UPLOAD_CHAT_AUDIO_FOLDER'])
    else:
        abort(404) # Or handle unsupported type

    # Security check: Ensure the user is part of the conversation for this media
    # Note: `filename` in the DB should be the full path stored when uploaded (e.g., 'static/chat_media/photos/...')
    # The `filename` argument here will be the full path like 'static/chat_media/photos/xyz.jpg'
    media_record = db.execute('''
        SELECT id FROM messages
        WHERE media_path = ? AND (sender_id = ? OR recipient_id = ?)
    ''', (filename, current_user.id, current_user.id)).fetchone()

    if not media_record:
        flash('You do not have permission to download this file.', 'danger')
        abort(403)

    try:
        return send_from_directory(directory, actual_filename, as_attachment=True)
    except FileNotFoundError:
        abort(404)


# --- User Authentication Routes (from app(6).py logic) ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Check if password reset is pending for this user after login
        if current_user.password_reset_pending and (current_user.admin_reset_approved or (current_user.reset_request_timestamp and (datetime.utcnow() - current_user.reset_request_timestamp) >= timedelta(seconds=10))):
            flash('Your password reset request has been approved or automatically activated. Please set your new password.', 'info')
            return redirect(url_for('set_new_password')) # Redirect to set_new_password
        return redirect(url_for('home'))

    form_data = {}

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        is_admin_attempt = request.form.get('admin_login_checkbox')

        db = get_db()

        if is_admin_attempt:
            if username == app.config['ADMIN_USERNAME'] and password == app.config['ADMIN_PASS']:
                admin_user = load_user(0) # Load in-memory admin user
                login_user(admin_user)
                flash('Logged in as Admin successfully!', 'success')
                return redirect(url_for('home'))
            else:
                flash('Invalid admin username or password.', 'danger')
                form_data['username'] = username
                return render_template('login.html', form_data=form_data)

        user_data = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user_data:
            user = load_user(user_data['id']) # Use load_user to get the full User object

            # Password reset pending logic
            if user.password_reset_pending:
                # IMPORTANT: Use the datetime object directly, no strptime needed here.
                reset_timestamp_dt = user.reset_request_timestamp # This is already a datetime object from DB

                if user.admin_reset_approved or (reset_timestamp_dt and (datetime.utcnow() - reset_timestamp_dt) >= timedelta(seconds=10)):
                    flash('Your password reset request has been approved or automatically activated. Please set your new password.', 'info')
                    # Store username in session so set_new_password knows who to reset
                    session['reset_username'] = user.username
                    return redirect(url_for('set_new_password'))
                else:
                    flash('Your password reset request is pending admin approval or automatic activation. Please wait.', 'warning')
                    form_data['username'] = username
                    return render_template('login.html', form_data=form_data)


            if check_password_hash(user_data['password_hash'], password):
                member_profile = db.execute('SELECT can_message FROM members WHERE user_id = ?', (user.id,)).fetchone()
                if not member_profile or member_profile['can_message'] == 0:
                    flash('Your account is not yet enabled for login. Please contact an administrator.', 'danger')
                    form_data['username'] = username
                    return render_template('login.html', form_data=form_data)

                login_user(user)

                db.execute('UPDATE users SET last_login_at = ?, last_seen_at = ? WHERE id = ?', (datetime.utcnow(), datetime.utcnow(), user.id))
                db.commit()

                member_exists = db.execute('SELECT id FROM members WHERE user_id = ?', (user.id,)).fetchone()
                if not member_exists:
                    flash('Welcome! Please add your personal details to complete your family profile.', 'info')
                    return redirect(url_for('add_my_details'))
                else:
                    flash('Logged in successfully.', 'success')
                    return redirect(url_for('home'))
            else:
                flash('Incorrect username or password.', 'danger')
                form_data['username'] = username
                return render_template('login.html', form_data=form_data)
        else:
            flash('Incorrect username or password.', 'danger')
            form_data['username'] = username
            return render_template('login.html', form_data=form_data)

    return render_template('login.html', form_data=form_data)


@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    Handles user registration, creating a new user account and an associated member profile.
    Ensures all required fields are provided, passwords match, username is unique,
    and handles secure password hashing.
    """
    # If the user is already authenticated, redirect them to the dashboard
    if current_user.is_authenticated:
        return redirect(url_for('home')) # Or 'dashboard' if you prefer that as the default logged-in page

    # Initialize form_data to pass back to the template in case of an error
    form_data = {}

    if request.method == 'POST':
        # Retrieve form data, stripping whitespace for text inputs
        # Use .get() for optional fields like gender to avoid KeyError if not present
        username = request.form.get('username', '').strip()
        original_name = request.form.get('original_name', '').strip()
        gender = request.form.get('gender') # This is optional, so no strip needed here unless you want to normalize " " to ""
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        db = get_db()
        error = None # Initialize an error variable to store validation messages

        # --- Server-side validation ---
        # Check if essential fields are empty (Gender is intentionally excluded here as per discussions)
        if not username or not original_name or not password or not confirm_password:
            error = 'Please fill in all required fields (Username, Full Name, Password, Confirm Password).'
        elif password != confirm_password:
            error = 'Passwords do not match.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters long.'
        else:
            # Check if username already exists in the database
            existing_user = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
            if existing_user is not None:
                error = f'Username "{username}" is already taken. Please choose a different one.'

        # If no validation errors occurred, proceed with database operations
        if error is None:
            hashed_password = generate_password_hash(password)
            # Generate a unique key for password recovery
            unique_key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))

            try:
                # Insert new user into the users table
                # Ensure column names match your current schema.sql
                db.execute(
                    '''INSERT INTO users (username, originalName, password_hash, is_admin, unique_key, theme_preference,
                                       last_login_at, last_seen_at, admin_reset_approved, password_reset_pending, reset_request_timestamp,
                                       chat_background_image_path)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (username, original_name, hashed_password, 0, unique_key, 'light',
                     datetime.utcnow(), datetime.utcnow(), 0, 0, None,
                     None) # Set chat_background_image_path to NULL initially
                )
                db.commit()

                # Get the ID of the newly created user
                new_user_id = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()['id']

                # Assign admin status to the first registered user
                total_users = db.execute('SELECT COUNT(id) FROM users').fetchone()[0]
                if total_users == 1:
                    db.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (new_user_id,))
                    db.commit()

                # Create a corresponding member profile for the new user
                # Ensure column names and number of placeholders match your current members table schema
                db.execute(
                    '''INSERT INTO members (fullName, gender, dateOfBirth, maritalStatus, spouseNames, fianceNames,
                                       childrenNames, educationLevel, schoolName, whereabouts, phoneNumber,
                                       emailContact, otherContact, bio, profilePhoto, user_id, added_by_user_id, can_message)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (original_name, gender, None, 'Single', '', '', # Default values for fields not collected at registration
                     '', 'None', '', '', '', '', '', '', # Empty strings for contact/bio if not provided
                     'static/img/default_profile.png', # Default profile photo path
                     new_user_id, # Link to the newly created user
                     new_user_id, # User who added this member (themselves)
                     1) # Messaging is enabled by default
                )
                db.commit()

                flash(f'Registration successful! Your unique key is: <strong>{unique_key}</strong>. Please keep it safe for password recovery. You can now log in.', 'success')
                return redirect(url_for('login'))
            except Exception as e:
                # Catch any database or other unexpected errors during the process
                db.rollback() # Rollback any changes if an error occurs
                error = f'An unexpected error occurred during registration: {e}'
                flash(error, 'danger')
        else:
            # If there was a validation error, flash the message
            flash(error, 'danger')

        # If there was an error, re-populate form_data to retain user input
        form_data = {
            'username': username,
            'original_name': original_name,
            'gender': gender,
            # Pass passwords back as empty for security
            'password': '',
            'confirm_password': ''
        }

    # For GET requests or if a POST request had an error, render the registration form
    return render_template('register.html', form_data=form_data)


@app.route('/logout')
@login_required
def logout():
    db = get_db()
    db.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', (datetime.utcnow(), current_user.id))
    db.commit()
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username')
        unique_key = request.form.get('unique_key', '').upper()

        db = get_db()

        user_data = db.execute('SELECT id, username, unique_key FROM users WHERE LOWER(username) = LOWER(?)', (username,)).fetchone()

        if not user_data:
            flash('Username not found.', 'danger')
            return render_template('forgot_password.html', username=username, unique_key=unique_key)
        if user_data['unique_key'] != unique_key:
            flash('Incorrect unique key.', 'danger')
            return render_template('forgot_password.html', username=username, unique_key=unique_key)

        ai_user_data = db.execute('SELECT id FROM users WHERE username = ?', ('AdminAI',)).fetchone()
        if not ai_user_data:
            flash('Admin AI account not found. Cannot process password reset request.', 'danger')
            return render_template('forgot_password.html', username=username, unique_key=unique_key)
        admin_ai_user_id = ai_user_data['id']

        message_body = f"Password reset request for user: {username}. Unique Key provided: {unique_key}. Please verify this key and initiate a password reset for this user from the Manage Users page if correct."
        try:
            db.execute(
                'INSERT INTO messages (sender_id, recipient_id, content, timestamp, is_read, is_admin_message) VALUES (?, ?, ?, ?, ?, ?)',
                (user_data['id'], admin_ai_user_id, message_body, datetime.utcnow(), 0, 1)
            )
            db.execute('UPDATE users SET password_reset_pending = 1, reset_request_timestamp = ? WHERE id = ?',
                       (datetime.utcnow(), user_data['id']))
            db.commit()

            flash('Your password reset request has been sent to the administrator. You will be redirected to set a new password in 10 seconds if the admin does not act sooner.', 'success')
            # Store username in session for set_new_password route to pick up
            session['reset_username'] = username
            return redirect(url_for('login')) # Redirect to login, user can set new password from there

        except sqlite3.Error as e:
            db.rollback()
            flash(f"Database error during request: {e}", 'danger')
        except Exception as e:
            db.rollback()
            flash(f"An unexpected error occurred: {e}", 'danger')

    return render_template('forgot_password.html')


@app.route('/set_new_password', methods=['GET', 'POST'])
def set_new_password():
    # Route for users to set a new password after a reset has been initiated/approved
    if 'reset_username' not in session:
        flash('No pending password reset request. Please use the forgot password link.', 'warning')
        return redirect(url_for('login'))

    username = session['reset_username']
    db = get_db()
    user_data = db.execute('SELECT id, password_reset_pending, admin_reset_approved, reset_request_timestamp FROM users WHERE username = ?', (username,)).fetchone()

    if not user_data or user_data['password_reset_pending'] == 0:
        flash('No pending password reset request for this user.', 'warning')
        session.pop('reset_username', None)
        return redirect(url_for('login'))

    can_reset = False
    # IMPORTANT: Use the datetime object directly, no strptime needed here.
    reset_timestamp_dt = user_data['reset_request_timestamp']

    if user_data['admin_reset_approved'] == 1:
        can_reset = True
    elif reset_timestamp_dt: # Check if timestamp is not None
        if (datetime.utcnow() - reset_timestamp_dt) >= timedelta(seconds=10):
            can_reset = True
            # Auto-approve if time has passed and not already approved by admin
            db.execute('UPDATE users SET admin_reset_approved = 1 WHERE id = ?', (user_data['id'],))
            db.commit()

    if not can_reset:
        flash("Password reset is not yet approved or the automatic approval time has not passed. Please wait.", "danger")
        return redirect(url_for('login')) # Redirect back to login if not ready

    form_data = {}
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not new_password or len(new_password) < 6:
            flash('New password must be at least 6 characters long.', 'danger')
            form_data = request.form.to_dict()
            return render_template('set_new_password.html', username=username, form_data=form_data)
        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            form_data = request.form.to_dict()
            return render_template('set_new_password.html', username=username, form_data=form_data)
        else:
            hashed_password = generate_password_hash(new_password)
            db.execute('UPDATE users SET password_hash = ?, password_reset_pending = 0, reset_request_timestamp = NULL, admin_reset_approved = 0 WHERE id = ?',
                       (hashed_password, user_data['id']))
            db.commit()
            session.pop('reset_username', None) # Clear reset session
            flash('Your password has been reset successfully. You can now log in.', 'success')
            return redirect(url_for('login'))

    return render_template('set_new_password.html', username=username, form_data=form_data)

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    # Allows a logged-in user to change their password
    form_data = {}
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_new_password = request.form['confirm_new_password']

        db = get_db()
        user = db.execute('SELECT password_hash FROM users WHERE id = ?', (current_user.id,)).fetchone()

        if not user:
            flash('User not found. Please log in again.', 'danger')
            return redirect(url_for('logout'))

        if not check_password_hash(user['password_hash'], current_password):
            flash('Current password is incorrect.', 'danger')
            form_data = request.form.to_dict()
            return render_template('change_password.html', form_data=form_data)

        if len(new_password) < 6:
            flash('New password must be at least 6 characters long.', 'danger')
            form_data = request.form.to_dict()
            return render_template('change_password.html', form_data=form_data)
        if new_password != confirm_new_password:
            flash('New password and confirmation do not match.', 'danger')
            form_data = request.form.to_dict()
            return render_template('change_password.html', form_data=form_data)

        new_password_hash = generate_password_hash(new_password)
        db.execute('UPDATE users SET password_hash = ?, password_reset_pending = 0, reset_request_timestamp = NULL, admin_reset_approved = 0 WHERE id = ?',
                   (new_password_hash, current_user.id))
        db.commit()

        flash('Your password has been changed successfully! Please log in with your new password.', 'success')
        return redirect(url_for('login'))

    return render_template('change_password.html', form_data=form_data)


# --- Admin Panel & User Management Routes (from app(6).py logic, with Raphael removal) ---

@app.route('/admin/manage_users', methods=['GET', 'POST'])
@login_required
def admin_manage_users():
    if not current_user.is_admin:
        flash("Unauthorized access. Admins only.", "danger")
        return redirect(url_for('home'))

    db = get_db()

    # Handle POST requests for admin actions
    if request.method == 'POST':
        action = request.form.get('action')
        user_id = request.form.get('user_id') # For user-related actions
        member_id = request.form.get('member_id') # For member-related actions

        # ... (lines before 1241)

        if action == 'toggle_admin':
            target_user = load_user(user_id)
            if target_user and target_user.username != app.config['ADMIN_USERNAME']: # Cannot change super admin
                new_admin_status = 0 if target_user.is_admin else 1
                db.execute('UPDATE users SET is_admin = ? WHERE id = ?', (new_admin_status, user_id))
                db.commit()
                # CORRECTED LINE: Ensure proper ternary operator syntax for f-string
                flash(f"Admin status for {target_user.username} {'enabled' if new_admin_status == 1 else 'disabled'}.", 'success')
            else:
                flash('Cannot change admin status for this user or yourself.', 'danger')

# ... (lines after 1241)


        elif action == 'reset_password_direct': # Direct password reset by admin
            target_user_id = request.form.get('user_id')
            new_password = request.form.get(f'new_password_{target_user_id}')
            confirm_password = request.form.get(f'confirm_password_{target_user_id}')

            if not new_password or not confirm_password or new_password != confirm_password:
                flash('New passwords do not match or are empty.', 'danger')
            elif len(new_password) < 6:
                flash('New password must be at least 6 characters long.', 'danger')
            else:
                hashed_password = generate_password_hash(new_password)
                db.execute('UPDATE users SET password_hash = ?, password_reset_pending = 0, reset_request_timestamp = NULL, admin_reset_approved = 0 WHERE id = ?', (hashed_password, target_user_id))
                db.commit()
                flash(f'Password for user ID {target_user_id} has been reset.', 'success')

        elif action == 'initiate_password_reset': # Initiate reset for user to complete
            target_user_id = request.form.get('user_id')
            db.execute('UPDATE users SET password_reset_pending = 1, reset_request_timestamp = ?, admin_reset_approved = 1 WHERE id = ?', (datetime.utcnow(), target_user_id))
            db.commit()
            flash(f'Password reset initiated for user ID {target_user_id}. They will be prompted to set a new password on next login.', 'info')

        elif action == 'delete_user':
            user_id_to_delete = request.form.get('user_id')
            if not user_id_to_delete:
                flash("User ID not provided for deleting user.", 'danger')
                return redirect(url_for('admin_manage_users'))

            try:
                user_to_delete_data = db.execute('SELECT id, username FROM users WHERE id = ?', (user_id_to_delete,)).fetchone()
                if not user_to_delete_data:
                    flash(f"User with ID {user_id_to_delete} not found.", 'danger')
                    return redirect(url_for('admin_manage_users'))

                if user_to_delete_data['username'] == app.config['ADMIN_USERNAME'] or user_to_delete_data['username'] == 'AdminAI':
                    flash("Cannot delete this special account.", 'danger')
                    return redirect(url_for('admin_manage_users'))

                # Delete associated member profile (if exists)
                member_data = db.execute('SELECT id, profilePhoto FROM members WHERE user_id = ?', (user_id_to_delete,)).fetchone()
                if member_data:
                    if member_data['profilePhoto'] and os.path.exists(os.path.join(app.root_path, member_data['profilePhoto'])):
                        os.remove(os.path.join(app.root_path, member_data['profilePhoto']))
                    db.execute('DELETE FROM members WHERE user_id = ?', (user_id_to_delete,))

                # Delete associated statuses
                db.execute('DELETE FROM statuses WHERE uploader_user_id = ?', (user_id_to_delete,))

                # Delete messages (direct messages) where this user is sender or recipient
                db.execute('DELETE FROM messages WHERE sender_id = ? OR recipient_id = ?', (user_id_to_delete, user_id_to_delete))

                # Delete game invitations (SQLite) where this user is sender or recipient
                db.execute('DELETE FROM game_invitations WHERE sender_id = ? OR recipient_id = ?', (user_id_to_delete, user_id_to_delete))

                # Delete game documents from Firestore where this user is player
                if firestore_db:
                    games_ref = firestore_db.collection(f'artifacts/{config.CANVAS_APP_ID}/public/games')
                    games_as_white = games_ref.where('playerWhiteId', '==', user_id_to_delete).stream()
                    for game_doc in games_as_white:
                        game_doc.reference.delete()
                        print(f"Deleted Firestore game {game_doc.id} (user was white).")

                    games_as_black = games_ref.where('playerBlackId', '==', user_id_to_delete).stream()
                    for game_doc in games_as_black:
                        game_doc.reference.delete()
                        print(f"Deleted Firestore game {game_doc.id} (user was black).")

                # Finally, delete the user itself
                db.execute('DELETE FROM users WHERE id = ?', (user_id_to_delete,))
                db.commit()
                flash(f"User '{user_to_delete_data['username']}' and all associated data deleted successfully.", 'success')

            except sqlite3.Error as e:
                db.rollback()
                flash(f"Database error deleting user: {e}", 'danger')
            except Exception as e:
                flash(f"An unexpected error occurred deleting user: {e}", 'danger')

            return redirect(url_for('admin_manage_users'))

        elif action == 'link_member_to_user':
            new_username = request.form.get('new_username')
            new_password = request.form.get('new_password')
            member_id_to_link = request.form.get('member_id')

            if not new_username or not new_password or not member_id_to_link:
                flash('Missing data for linking user.', 'danger')
            else:
                existing_user = db.execute('SELECT id FROM users WHERE username = ?', (new_username,)).fetchone()
                if existing_user:
                    flash('Username already exists. Please choose a different one.', 'danger')
                else:
                    try:
                        member_to_link = db.execute('SELECT fullName, gender FROM members WHERE id = ?', (member_id_to_link,)).fetchone() # Removed association from select
                        if not member_to_link:
                            flash('Member not found for linking.', 'danger')
                        else:
                            hashed_password = generate_password_hash(new_password)
                            cursor = db.execute(
                                # Removed relationshipToRaphael from users table insert
                                'INSERT INTO users (username, originalName, password_hash, theme_preference, unique_key, password_reset_pending, reset_request_timestamp, last_login_at, last_seen_at, admin_reset_approved) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                (new_username, member_to_link['fullName'], hashed_password, 'light', generate_unique_key(), 0, None, datetime.utcnow(), datetime.utcnow(), 0)
                            )
                            new_user_id = cursor.lastrowid
                            db.execute('UPDATE members SET user_id = ?, can_message = 1 WHERE id = ?', (new_user_id, member_id_to_link))
                            db.commit()
                            flash(f'User {new_username} created and linked to {member_to_link["fullName"]}. Login access enabled.', 'success')
                    except sqlite3.IntegrityError:
                        flash('Username already exists or database error.', 'danger')
                    except Exception as e:
                        flash(f'An error occurred during linking: {e}', 'danger')

        elif action == 'toggle_login_access': # This toggles can_message which impacts login and messaging
            member_id_toggle = request.form.get('member_id')
            member_data = db.execute('SELECT user_id, can_message FROM members WHERE id = ?', (member_id_toggle,)).fetchone()
            if member_data and member_data['user_id']:
                new_access_status = not member_data['can_message']
                db.execute('UPDATE members SET can_message = ? WHERE id = ?', (1 if new_access_status else 0, member_id_toggle))
                db.commit()
                flash(f"Login & Messaging access for member ID {member_id_toggle} {'enabled' if new_access_status else 'disabled'}.", 'success')
            else:
                flash('Cannot toggle login/messaging access for unlinked member or member not found.', 'danger')

        return redirect(url_for('admin_manage_users'))

    # Fetch users (excluding the special admin user and AdminAI)
    users_data = db.execute(
        "SELECT id, username, originalName, unique_key, is_admin, password_reset_pending, reset_request_timestamp, admin_reset_approved FROM users WHERE username != ? AND username != 'AdminAI' ORDER BY username ASC",
        (app.config['ADMIN_USERNAME'],)
    ).fetchall()

    users_for_template = []
    for user in users_data:
        user_dict = dict(user)
        # Check if reset_request_timestamp is already a datetime object
        if isinstance(user_dict['reset_request_timestamp'], datetime):
            user_dict['reset_request_timestamp'] = user_dict['reset_request_timestamp'] # Already datetime
        elif user_dict['reset_request_timestamp']: # It's a string, parse it
            try:
                user_dict['reset_request_timestamp'] = datetime.strptime(user_dict['reset_request_timestamp'], '%Y-%m-%d %H:%M:%S.%f' if '.' in user_dict['reset_request_timestamp'] else '%Y-%m-%d %H:%M:%S')
            except ValueError:
                user_dict['reset_request_timestamp'] = None
        else:
            user_dict['reset_request_timestamp'] = None # Handle None case
        users_for_template.append(user_dict)


    # Fetch members with their associated user username if available (Removed Raphael-related fields from SELECT)
    members_with_status_data = db.execute('''
        SELECT m.id, m.fullName, m.profilePhoto, m.user_id, m.can_message,
               u.username AS linked_username, u.last_seen_at AS user_last_seen_at
        FROM members m
        LEFT JOIN users u ON m.user_id = u.id
        ORDER BY m.fullName ASC
    ''').fetchall()

    members_with_status_for_template = []
    now = datetime.utcnow()
    for row in members_with_status_data:
        member_dict = dict(row)
        member_dict['can_message'] = bool(member_dict['can_message'])

        # Add profile photo URL
        if member_dict['profilePhoto']:
            member_dict['profilePhotoUrl'] = url_for('uploaded_file', filename=os.path.basename(member_dict['profilePhoto']))
        else:
            member_dict['profilePhotoUrl'] = url_for('static', filename='img/default_profile.png')

        # Initialize status fields to JSON-serializable defaults
        member_dict['has_active_status'] = False
        member_dict['status_file_url'] = None
        member_dict['status_expires_at'] = None

        # Process status data if available (need to fetch separately if not joined)
        # Fetching status for this member for the admin view
        member_status_data = db.execute('SELECT id, file_path, is_video, upload_time FROM statuses WHERE member_id = ? ORDER BY upload_time DESC LIMIT 1', (member_dict['id'],)).fetchone()

        if member_status_data:
            status_dict_current = dict(member_status_data)
            status_dict_current['upload_time'] = member_status_data['upload_time'] # Ensure it's the datetime object

            if isinstance(status_dict_current['upload_time'], datetime):
                upload_time_dt = status_dict_current['upload_time']
            else:
                try:
                    upload_time_dt = datetime.strptime(status_dict_current['upload_time'], '%Y-%m-%d %H:%M:%S.%f' if '.' in str(status_dict_current['upload_time']) else '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    upload_time_dt = None

            if upload_time_dt:
                expires_at_dt = upload_time_dt + timedelta(hours=12)
                if (now - upload_time_dt) < timedelta(hours=12): # Status is active
                    member_dict['has_active_status'] = True
                    member_dict['status_id'] = status_dict_current['id'] # Pass status ID
                    member_dict['status_file_path'] = status_dict_current['file_path'] # Full path from DB
                    member_dict['status_is_video'] = bool(status_dict_current['is_video']) # Boolean
                    member_dict['status_expires_at'] = expires_at_dt # Pass datetime object directly for moment.js

        # Determine online status for linked users
        member_dict['is_online'] = False # Default to false
        if member_dict['user_id'] and member_dict['user_last_seen_at']:
            last_seen_dt = member_dict['user_last_seen_at']
            # Ensure last_seen_dt is a datetime object
            if isinstance(last_seen_dt, datetime):
                pass # Already datetime
            elif isinstance(last_seen_dt, str):
                try:
                    last_seen_dt = datetime.strptime(last_seen_dt, '%Y-%m-%d %H:%M:%S.%f' if '.' in last_seen_dt else '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    last_seen_dt = None # Set to None if parsing fails
            else:
                last_seen_dt = None # Handle unexpected type

            if last_seen_dt: # Only compare if it's a valid datetime object
                if (now - last_seen_dt) < timedelta(minutes=5):
                    member_dict['is_online'] = True

        # Remove raw datetime objects from the dictionary before JSON serialization IF they are not used on frontend
        # For admin_manage_users, we might use them directly, or pass them in a format Moment.js can consume.
        # It's better to pass datetime objects directly to template if Moment.js is expected to format them.
        # Only pop if they're purely internal intermediate values.
        # member_dict.pop('status_upload_time', None) # Keep if needed for debug/Moment.js
        # member_dict.pop('user_last_seen_at', None) # Keep if needed for debug/Moment.js


        members_with_status_for_template.append(member_dict)

    return render_template('admin_manage_users.html', users=users_for_template, members_with_status=members_with_status_for_template)


# --- Dashboard & Member Management (Routes from app(6).py logic, with Raphael removal) ---

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    my_members = db.execute(
        'SELECT id, fullName, profilePhoto, dateOfBirth FROM members WHERE added_by_user_id = ? ORDER BY fullName ASC',
        (current_user.id,)
    ).fetchall()

    members_with_age = []
    for member in my_members:
        member_dict = dict(member)
        # Check if dateOfBirth is already a datetime object
        if isinstance(member_dict['dateOfBirth'], datetime):
            dob = member_dict['dateOfBirth'].date()
        elif member_dict['dateOfBirth']: # It's a string, parse it
            try:
                dob = datetime.strptime(member_dict['dateOfBirth'], '%Y-%m-%d').date()
            except ValueError:
                dob = None
        else:
            dob = None

        if dob:
            today = datetime.now().date()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            member_dict['age'] = age
        else:
            member_dict['age'] = 'N/A'

        if member_dict['profilePhoto']:
            member_dict['profilePhotoUrl'] = url_for('uploaded_file', filename=os.path.basename(member_dict['profilePhoto']))
        else:
            member_dict['profilePhotoUrl'] = url_for('static', filename='img/default_profile.png')

        members_with_age.append(member_dict)

    total_users_count = db.execute('SELECT COUNT(id) FROM users').fetchone()[0]
    total_members_count = db.execute('SELECT COUNT(id) FROM members').fetchone()[0]

    return render_template('dashboard.html', my_members=members_with_age,
                           total_users_count=total_users_count,
                           total_members_count=total_members_count)

@app.route('/add_member', methods=['GET'])
@login_required
def add_member_form():
    if not current_user.is_admin:
        flash('Only admins can add new members.', 'danger')
        return redirect(url_for('home'))
    return render_template('add-member.html', form_data={})

@app.route('/add_member', methods=['POST'])
@login_required
def add_member():
    if not current_user.is_admin:
        flash('Only admins can add new members.', 'danger')
        return redirect(url_for('home'))

    fullName = request.form['fullName'].strip()
    dateOfBirth = request.form['dateOfBirth']
    gender = request.form['gender']
    maritalStatus = request.form.get('maritalStatus')
    spouseNames = request.form.get('spouseNames', '').strip()
    fianceNames = request.form.get('fianceNames', '').strip()
    childrenNames = request.form.get('childrenNames', '').strip()
    educationLevel = request.form.get('educationLevel', 'None')
    schoolName = request.form.get('schoolName', '').strip()
    whereabouts = request.form['whereabouts'].strip()
    phoneNumber = request.form.get('phoneNumber', '').strip()
    emailContact = request.form.get('emailContact', '').strip()
    otherContact = request.form.get('otherContact', '').strip()
    bio = request.form.get('bio', '').strip()
    # Removed association, personalRelationshipDescription, isRaphaelDescendant from form parsing

    profilePhoto_file = request.files.get('profilePhoto')

    db = get_db()
    error = None

    if not fullName or not dateOfBirth or not gender or not whereabouts: # Simplified validation
        error = 'Please fill in all required fields: Full Name, Date of Birth, Gender, and Current Whereabouts.'

    if error is None:
        profile_photo_filename = None
        if profilePhoto_file and profilePhoto_file.filename:
            filename = secure_filename(profilePhoto_file.filename)
            profile_photo_filename = f"{uuid.uuid4()}_{filename}"
            # Save to the UPLOAD_FOLDER (profile_photos)
            profilePhoto_file.save(os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], profile_photo_filename))
            # Store path relative to static/img/profile_photos for database
            profile_photo_filename = os.path.join('static', 'img', 'profile_photos', profile_photo_filename).replace('\\', '/')
        else:
            # Set default if no photo uploaded
            profile_photo_filename = 'static/img/default_profile.png'


        try:
            # Removed association, personalRelationshipDescription, isRaphaelDescendant from insert
            db.execute(
                '''INSERT INTO members (fullName, gender, dateOfBirth, maritalStatus, spouseNames, fianceNames, childrenNames, educationLevel, schoolName, whereabouts, phoneNumber, emailContact, otherContact, bio, profilePhoto, user_id, added_by_user_id, can_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (fullName, gender, dateOfBirth, maritalStatus, spouseNames, fianceNames, childrenNames, educationLevel, schoolName, whereabouts, phoneNumber, emailContact, otherContact, bio, profile_photo_filename, None, current_user.id, 0)
            )
            db.commit()
            flash(f'{fullName} added successfully!', 'success')
            return redirect(url_for('dashboard'))
        except sqlite3.Error as e:
            error = f"Database error: {e}"
            flash(error, 'danger')
            form_data = request.form.to_dict()
            return render_template('add-member.html', form_data=form_data)
    else:
        flash(error, 'danger')
        form_data = request.form.to_dict()
        return render_template('add-member.html', form_data=form_data)


# ADAPTED ROUTE: members_list from user's prompt (REMOVED Raphael-related fields)
@app.route('/members_list')
@login_required
def members_list():
    db = get_db()
    # Fetch all members, including their linked user's username and last_seen_at for online status
    # Exclude the current user themselves and AdminAI. REMOVED Raphael-related fields from SELECT.
    members_with_status_data = db.execute('''
        SELECT m.id, m.fullName, m.profilePhoto, m.user_id, m.can_message,
               u.username as user_username, u.originalName as user_originalName, u.last_seen_at as user_last_seen_at,
               s.file_path as status_file_path, s.is_video as status_is_video, s.upload_time as status_upload_time
        FROM members m
        LEFT JOIN users u ON m.user_id = u.id
        LEFT JOIN statuses s ON m.id = s.member_id
        WHERE (u.username IS NULL OR u.username != 'AdminAI')
          AND (m.user_id IS NULL OR m.user_id != ?)
        ORDER BY m.fullName ASC
    ''', (current_user.id,)).fetchall()

    members_for_template = []
    now = datetime.utcnow()
    for row in members_with_status_data:
        member_dict = dict(row)
        member_dict['can_message'] = bool(member_dict['can_message'])

        # Profile photo URL
        if member_dict['profilePhoto']:
            member_dict['profilePhotoUrl'] = url_for('uploaded_file', filename=os.path.basename(member_dict['profilePhoto']))
        else:
            member_dict['profilePhotoUrl'] = url_for('static', filename='img/default_profile.png')

        # Initialize status fields to JSON-serializable defaults
        member_dict['has_active_status'] = False
        member_dict['status_file_url'] = None
        member_dict['status_expires_at'] = None

        # Process status data if available
        if member_dict['status_file_path'] and member_dict['status_upload_time']:
            upload_time_dt = member_dict['status_upload_time']
            # Ensure upload_time_dt is a datetime object, converting from string if necessary
            if isinstance(upload_time_dt, datetime):
                pass # Already datetime
            else: # It's a string, parse it
                try:
                    upload_time_dt = datetime.strptime(upload_time_dt, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError: # Fallback for formats without microseconds
                    upload_time_dt = datetime.strptime(upload_time_dt, '%Y-%m-%d %H:%M:%S')

            expires_at_dt = upload_time_dt + timedelta(hours=12)
            # Check if status is still active based on upload time
            if (now - upload_time_dt) < timedelta(hours=12):
                member_dict['has_active_status'] = True
                if member_dict['status_is_video']:
                    member_dict['status_file_url'] = url_for('uploaded_video', filename=os.path.basename(member_dict['status_file_path']))
                else:
                    member_dict['status_file_url'] = url_for('uploaded_file', filename=os.path.basename(member_dict['status_file_path']))
                member_dict['status_expires_at'] = expires_at_dt.isoformat() # Convert datetime to ISO string

        # Determine online status for linked users
        member_dict['is_online'] = False # Default to false
        if member_dict['user_id'] and member_dict['user_last_seen_at']:
            last_seen_dt = member_dict['user_last_seen_at']
            # Ensure last_seen_dt is a datetime object, converting from string if necessary
            if isinstance(last_seen_dt, datetime):
                pass # Already datetime
            elif isinstance(last_seen_dt, str):
                try:
                    last_seen_dt = datetime.strptime(last_seen_dt, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError: # Fallback for formats without microseconds
                    last_seen_dt = datetime.strptime(last_seen_dt, '%Y-%m-%d %H:%M:%S')

            if (now - last_seen_dt) < timedelta(minutes=5):
                member_dict['is_online'] = True

        # Remove raw datetime objects from the dictionary before JSON serialization
        # These are not needed in the frontend JavaScript for direct use.
        # member_dict.pop('status_upload_time', None)
        # member_dict.pop('user_last_seen_at', None)

        members_for_template.append(member_dict)

    return render_template('members_list.html', members=members_for_template, current_user_id=current_user.id)



@app.route('/my_profile', methods=['GET'])
@login_required
def my_profile():
    # Displays the current user's personal profile details
    db = get_db()
    # Removed relationshipToRaphael from users table select
    member_data = db.execute(
        'SELECT m.*, u.username, u.unique_key FROM members m JOIN users u ON m.user_id = u.id WHERE m.user_id = ?',
        (current_user.id,)
    ).fetchone()

    if not member_data:
        flash('Your member profile details are not yet added. Please add them.', 'info')
        return redirect(url_for('add_my_details'))

    member_profile = Member( # Create a Member object for rendering (Removed Raphael-related fields)
        id=member_data['id'],
        fullName=member_data['fullName'],
        dateOfBirth=member_data['dateOfBirth'],
        gender=member_data['gender'],
        maritalStatus=member_data['maritalStatus'],
        spouseNames=member_data['spouseNames'],
        fianceNames=member_data['fianceNames'],
        childrenNames=member_data['childrenNames'],
        educationLevel=member_data['educationLevel'],
        schoolName=member_data['schoolName'],
        whereabouts=member_data['whereabouts'],
        phoneNumber=member_data['phoneNumber'],
        emailContact=member_data['emailContact'],
        otherContact=member_data['otherContact'],
        bio=member_data['bio'],
        profilePhoto=member_data['profilePhoto'],
        user_id=member_data['user_id'],
        needs_details_update=member_data['needs_details_update'],
        added_by_user_id=member_data['added_by_user_id'],
        can_message=member_data['can_message']
    )

    # Convert comma-separated strings to lists for display
    member_profile.spouseNames = [s.strip() for s in (member_profile.spouseNames or '').split(',') if s.strip()]
    member_profile.fianceNames = [s.strip() for s in (member_profile.fianceNames or '').split(',') if s.strip()]
    member_profile.childrenNames = [s.strip() for s in (member_profile.childrenNames or '').split(',') if s.strip()]


    age = calculate_age(member_profile.dateOfBirth)

    temp_video_data_for_template = None
    latest_status_data = db.execute('SELECT * FROM statuses WHERE member_id = ? ORDER BY upload_time DESC LIMIT 1', (member_profile.id,)).fetchone()
    if latest_status_data:
        try:
            upload_time_dt = latest_status_data['upload_time']
            # Ensure upload_time_dt is a datetime object
            if isinstance(upload_time_dt, str):
                upload_time_dt = datetime.strptime(upload_time_dt, '%Y-%m-%d %H:%M:%S.%f' if '.' in upload_time_dt else '%Y-%m-%d %H:%M:%S')

            expires_at_dt = upload_time_dt + timedelta(hours=12)
            is_active_status = (datetime.utcnow() < expires_at_dt) # Use utcnow for consistency

            if is_active_status: # Only show if active
                temp_video_data_for_template = {
                    'file_path': latest_status_data['file_path'],
                    'upload_time': upload_time_dt,
                    'expires_at': expires_at_dt,
                    'is_active': is_active_status,
                    'is_video': bool(latest_status_data['is_video'])
                }
        except Exception as e:
            print(f"Error processing status for my_profile: {e}")
            temp_video_data_for_template = None

    return render_template('my_profile.html', member=member_profile, age=age, temp_video=temp_video_data_for_template, form_data={})

@app.route('/member/<int:member_id>')
@login_required
def member_detail(member_id):
    # Displays details for a specific member profile
    db = get_db()

    if g.user_member and member_id == g.user_member.id:
        return redirect(url_for('my_profile'))

    # Removed Raphael-related fields from select
    member_data = db.execute(
        'SELECT m.*, u.username, u.last_seen_at FROM members m LEFT JOIN users u ON m.user_id = u.id WHERE m.id = ?',
        (member_id,)
    ).fetchone()

    if not member_data:
        abort(404)

    member_obj = Member( # Create a Member object for rendering (Removed Raphael-related fields)
        id=member_data['id'],
        fullName=member_data['fullName'],
        dateOfBirth=member_data['dateOfBirth'],
        gender=member_data['gender'],
        maritalStatus=member_data['maritalStatus'],
        spouseNames=member_data['spouseNames'],
        fianceNames=member_data['fianceNames'],
        childrenNames=member_data['childrenNames'],
        educationLevel=member_data['educationLevel'],
        schoolName=member_data['schoolName'],
        whereabouts=member_data['whereabouts'],
        phoneNumber=member_data['phoneNumber'],
        emailContact=member_data['emailContact'],
        otherContact=member_data['otherContact'],
        bio=member_data['bio'],
        profilePhoto=member_data['profilePhoto'],
        user_id=member_data['user_id'],
        needs_details_update=member_data['needs_details_update'],
        added_by_user_id=member_data['added_by_user_id'],
        can_message=member_data['can_message']
    )

    # Convert comma-separated strings to lists for display
    member_obj.spouseNames = [s.strip() for s in (member_obj.spouseNames or '').split(',') if s.strip()]
    member_obj.fianceNames = [s.strip() for s in (member_obj.fianceNames or '').split(',') if s.strip()]
    member_obj.childrenNames = [s.strip() for s in (member_obj.childrenNames or '').split(',') if s.strip()]


    age = calculate_age(member_obj.dateOfBirth)

    # Handle profile photo URL
    if member_obj.profilePhoto:
        member_obj.profilePhoto = url_for('uploaded_file', filename=os.path.basename(member_obj.profilePhoto))
    else:
        member_obj.profilePhoto = url_for('static', filename='img/default_profile.png')

    temp_video_data_for_template = None
    latest_status_data = db.execute('SELECT * FROM statuses WHERE member_id = ? ORDER BY upload_time DESC LIMIT 1', (member_obj.id,)).fetchone()
    if latest_status_data:
        try:
            upload_time_dt = latest_status_data['upload_time']
            # Ensure upload_time_dt is a datetime object
            if isinstance(upload_time_dt, str):
                upload_time_dt = datetime.strptime(upload_time_dt, '%Y-%m-%d %H:%M:%S.%f' if '.' in upload_time_dt else '%Y-%m-%d %H:%M:%S')

            expires_at_dt = upload_time_dt + timedelta(hours=12)
            is_active_status = (datetime.utcnow() < expires_at_dt) # Use utcnow for consistency

            if is_active_status: # Only show if active
                temp_video_data_for_template = {
                    'file_path': latest_status_data['file_path'],
                    'upload_time': upload_time_dt,
                    'expires_at': expires_at_dt,
                    'is_active': is_active_status,
                    'is_video': bool(latest_status_data['is_video'])
                }
        except Exception as e:
            print(f"Error processing status for member_detail: {e}")
            temp_video_data_for_template = None

    # Determine online status
    is_online = False
    last_seen_at = member_data['last_seen_at']
    if last_seen_at:
        try:
            # Check if last_seen_at is already a datetime object
            if isinstance(last_seen_at, datetime):
                last_seen_dt = last_seen_at
            elif isinstance(last_seen_at, str):
                if '.' in last_seen_at:
                    last_seen_dt = datetime.strptime(last_seen_at, '%Y-%m-%d %H:%M:%S.%f')
                else:
                    last_seen_dt = datetime.strptime(last_seen_at, '%Y-%m-%d %H:%M:%S')
            else:
                last_seen_dt = None # Handle unexpected type

            if last_seen_dt: # Only compare if it's a valid datetime object
                now_utc = datetime.utcnow() # Always get current time in UTC

                if (now_utc - last_seen_dt) < timedelta(minutes=5):
                    is_online = True
        except (ValueError, TypeError):
            pass


    return render_template('member_detail.html', member=member_obj, age=age, temp_video=temp_video_data_for_template, is_online=is_online)


@app.route('/add_my_details', methods=['GET', 'POST'])
@login_required
def add_my_details():
    # Allows a logged-in user to add their own member profile details or update them
    db = get_db()
    member_profile = db.execute('SELECT * FROM members WHERE user_id = ?', (current_user.id,)).fetchone()
    is_editing = member_profile is not None

    form_data = {}
    if is_editing:
        form_data = dict(member_profile)
        # Convert comma-separated strings to lists for form rendering
        for key in ['spouseNames', 'fianceNames', 'childrenNames']:
            if form_data.get(key) and isinstance(form_data[key], str):
                form_data[key] = [item.strip() for item in form_data[key].split(',') if item.strip()]
            else:
                form_data[key] = []
        # Remap keys for consistency with template expected names if different
        form_data['full_name'] = form_data['fullName']
        form_data['date_of_birth'] = form_data['dateOfBirth']
        form_data['marital_status'] = form_data['maritalStatus']
        form_data['spouse_names'] = form_data['spouseNames']
        form_data['fiance_names'] = form_data['fianceNames']
        form_data['children_names'] = form_data['childrenNames']
        form_data['education_level'] = form_data['educationLevel']
        form_data['school_name'] = form_data['schoolName']
        form_data['phone_number'] = form_data['phoneNumber']
        form_data['email_contact'] = form_data['emailContact']
        form_data['other_contact'] = form_data['otherContact']
        form_data['biography'] = form_data['bio']
        # Removed personal_relationship_description, is_raphael_descendant, association from form_data

    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        date_of_birth = request.form.get('date_of_birth')
        gender = request.form['gender']
        marital_status = request.form.get('marital_status')
        spouse_names_list = [name.strip() for name in request.form.get('spouse_names', '').split(',') if name.strip()]
        spouse_names = ', '.join(spouse_names_list) if spouse_names_list else None
        fiance_names_list = [name.strip() for name in request.form.get('fiance_names', '').split(',') if name.strip()]
        fiance_names = ', '.join(fiance_names_list) if fiance_names_list else None
        children_names_list = [name.strip() for name in request.form.get('children_names', '').split(',') if name.strip()]
        children_names = ', '.join(children_names_list) if children_names_list else None
        education_level = request.form.get('education_level')
        school_name = request.form.get('school_name', '').strip()
        whereabouts = request.form.get('whereabouts').strip()
        phone_number = request.form.get('phone_number', '').strip()
        email_contact = request.form.get('email_contact', '').strip()
        other_contact = request.form.get('other_contact', '').strip()
        biography_text = request.form.get('biography').strip()
        # Removed personal_relationship_description, is_raphael_descendant, association from form parsing


        profile_photo_file = request.files.get('profile_photo')
        remove_profile_photo = request.form.get('remove_profile_photo')

        current_photo_path_db = form_data.get('profilePhoto', None) if is_editing else None
        profile_photo_filename = current_photo_path_db # Start with current if not changing

        # Handle profile photo upload/removal
        if remove_profile_photo:
            if current_photo_path_db and os.path.exists(os.path.join(app.root_path, current_photo_path_db)) and current_photo_path_db != 'static/img/default_profile.png':
                os.remove(os.path.join(app.root_path, current_photo_path_db))
            profile_photo_filename = 'static/img/default_profile.png' # Set to default after removal
        elif profile_photo_file and allowed_file(profile_photo_file.filename):
            if current_photo_path_db and os.path.exists(os.path.join(app.root_path, current_photo_path_db)) and current_photo_path_db != 'static/img/default_profile.png':
                os.remove(os.path.join(app.root_path, current_photo_path_db)) # Delete old photo if not default
            filename = secure_filename(profile_photo_file.filename)
            unique_filename = f"{uuid.uuid4()}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            profile_photo_file.save(file_path)
            profile_photo_filename = os.path.relpath(file_path, app.root_path).replace('\\', '/')
        elif not is_editing and not profile_photo_file:
            profile_photo_filename = 'static/img/default_profile.png' # Default for new member if no upload


        # Basic validation
        if not all([full_name, date_of_birth, gender, whereabouts]): # Simplified validation
            flash('Please fill in all required fields (Full Name, Date of Birth, Gender, Current Whereabouts).', 'danger')
            form_data = request.form.to_dict() # Repopulate form_data with submitted values
            form_data['spouse_names'] = spouse_names_list # Ensure lists are passed back
            form_data['fiance_names'] = fiance_names_list
            form_data['children_names'] = children_names_list
            form_data['profile_photo_current'] = profile_photo_filename # Pass back current photo path
            # Removed is_raphael_descendant, association from form_data
            return render_template('add_my_details.html', form_data=form_data, is_editing=is_editing)


        if is_editing:
            db.execute('''
                UPDATE members SET
                    fullName = ?, dateOfBirth = ?, gender = ?, maritalStatus = ?,
                    spouseNames = ?, fianceNames = ?, childrenNames = ?,
                    educationLevel = ?, schoolName = ?, whereabouts = ?,
                    phoneNumber = ?, emailContact = ?, otherContact = ?, bio = ?,
                    profilePhoto = ?, needs_details_update = ?
                WHERE user_id = ?
            ''', (
                full_name, date_of_birth, gender, marital_status,
                spouse_names, fiance_names, children_names,
                education_level, school_name, whereabouts,
                phone_number, email_contact, other_contact, biography_text,
                profile_photo_filename, 0,
                current_user.id
            ))
            # Also update the user's originalName if they changed their full_name
            db.execute('UPDATE users SET originalName = ? WHERE id = ?', (full_name, current_user.id)) # Removed relationshipToRaphael update
            flash('Your personal details have been updated!', 'success')
        else:
            db.execute('''
                INSERT INTO members (
                    fullName, gender, dateOfBirth, maritalStatus, spouseNames, fianceNames, childrenNames,
                    educationLevel, schoolName, whereabouts, phoneNumber, emailContact, otherContact, bio,
                    profilePhoto, user_id, added_by_user_id, can_message, needs_details_update
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    full_name, gender, date_of_birth, marital_status, spouse_names, fiance_names, children_names,
                    education_level, school_name, whereabouts, phone_number, email_contact, other_contact, biography_text,
                    profile_photo_filename, current_user.id, current_user.id, 1, 0
                )
            )
            flash('Your personal details have been added!', 'success')
        db.commit()

        return redirect(url_for('my_profile'))

    return render_template('add_my_details.html', form_data=form_data, is_editing=is_editing)

@app.route('/edit_member/<int:member_id>', methods=['GET', 'POST'])
@login_required
def edit_member(member_id):
    db = get_db()
    member_data = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()

    if not member_data:
        flash("Member not found.", "danger")
        return redirect(url_for('admin_manage_users'))

    member = Member( # Create a Member object for rendering (Removed Raphael-related fields)
        id=member_data['id'],
        fullName=member_data['fullName'],
        dateOfBirth=member_data['dateOfBirth'],
        gender=member_data['gender'],
        maritalStatus=member_data['maritalStatus'],
        spouseNames=member_data['spouseNames'],
        fianceNames=member_data['fianceNames'],
        childrenNames=member_data['childrenNames'],
        educationLevel=member_data['educationLevel'],
        schoolName=member_data['schoolName'],
        whereabouts=member_data['whereabouts'],
        phoneNumber=member_data['phoneNumber'],
        emailContact=member_data['emailContact'],
        otherContact=member_data['otherContact'],
        bio=member_data['bio'],
        profilePhoto=member_data['profilePhoto'],
        user_id=member_data['user_id'],
        needs_details_update=member_data['needs_details_update'],
        added_by_user_id=member_data['added_by_user_id'],
        can_message=member_data['can_message']
    )

    if not current_user.is_admin and (member.user_id is None or current_user.id != member.user_id):
        flash("You do not have permission to edit this profile.", "danger")
        return redirect(url_for('home'))

    form_data = {}
    if request.method == 'GET':
        form_data = dict(member_data)
        for key in ['spouseNames', 'fianceNames', 'childrenNames']:
            if form_data.get(key) and isinstance(form_data[key], str):
                form_data[key] = [item.strip() for item in form_data[key].split(',') if item.strip()]
            else:
                form_data[key] = []
        form_data['profile_photo_current'] = member_data['profilePhoto']
        # Removed is_raphael_descendant, association from form_data

    if request.method == 'POST':
        member.fullName = request.form.get('fullName').strip()
        member.dateOfBirth = request.form.get('dateOfBirth')
        member.gender = request.form.get('gender')
        member.maritalStatus = request.form.get('maritalStatus')
        member.spouseNames = ', '.join([s.strip() for s in request.form.get('spouseNames', '').split(',') if s.strip()]) or None
        member.fianceNames = ', '.join([s.strip() for s in request.form.get('fianceNames', '').split(',') if s.strip()]) or None
        member.childrenNames = ', '.join([c.strip() for c in request.form.get('childrenNames', '').split(',') if c.strip()]) or None
        member.educationLevel = request.form.get('educationLevel')
        member.schoolName = request.form.get('schoolName').strip()
        member.whereabouts = request.form.get('whereabouts').strip()
        member.phoneNumber = request.form.get('phoneNumber').strip()
        member.emailContact = request.form.get('emailContact').strip()
        member.otherContact = request.form.get('otherContact').strip()
        member.bio = request.form.get('bio').strip()
        # Removed personalRelationshipDescription, isRaphaelDescendant, association from form parsing

        profile_photo_file = request.files.get('profilePhoto')
        remove_profile_photo = request.form.get('remove_profile_photo')
        can_message = request.form.get('can_message') == '1'


        current_photo_path_db = member_data['profilePhoto']
        profile_photo_filename = current_photo_path_db # Start with current if not changing

        if remove_profile_photo:
            if current_photo_path_db and os.path.exists(os.path.join(app.root_path, current_photo_path_db)) and current_photo_path_db != 'static/img/default_profile.png':
                os.remove(os.path.join(app.root_path, current_photo_path_db))
            profile_photo_filename = 'static/img/default_profile.png' # Set to default after removal
        elif profile_photo_file and allowed_file(profile_photo_file.filename):
            if current_photo_path_db and os.path.exists(os.path.join(app.root_path, current_photo_path_db)) and current_photo_path_db != 'static/img/default_profile.png':
                os.remove(os.path.join(app.root_path, current_photo_path_db))
            filename = secure_filename(profile_photo_file.filename)
            unique_filename = f"{uuid.uuid4()}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            profile_photo_file.save(file_path)
            profile_photo_filename = os.path.relpath(file_path, app.root_path).replace('\\', '/')
        # If no new file and not removed, keep existing path

        # Update needs_details_update based on completeness of required fields
        needs_details_update = 0
        if not all([member.fullName, member.dateOfBirth, member.gender, member.whereabouts]): # Simplified validation
            needs_details_update = 1
            flash('Please fill in all required fields.', 'danger')
            # Repopulate form_data with submitted values
            form_data = request.form.to_dict()
            form_data['spouse_names'] = member.spouseNames.split(', ') if member.spouseNames else []
            form_data['fiance_names'] = member.fianceNames.split(', ') if member.fianceNames else []
            form_data['children_names'] = member.childrenNames.split(', ') if member.childrenNames else []
            form_data['profile_photo_current'] = profile_photo_filename # Pass back current photo path
            # Removed is_raphael_descendant, association from form_data
            return render_template('edit_member.html', member=member, form_data=form_data)


        db.execute('''
            UPDATE members SET
                fullName = ?, dateOfBirth = ?, gender = ?, maritalStatus = ?,
                spouseNames = ?, fianceNames = ?, childrenNames = ?,
                educationLevel = ?, schoolName = ?, whereabouts = ?,
                phoneNumber = ?, emailContact = ?, otherContact = ?, bio = ?,
                profilePhoto = ?, needs_details_update = ?, can_message = ?
            WHERE id = ?
        ''', (
            member.fullName, member.dateOfBirth, member.gender, maritalStatus,
            member.spouseNames, member.fianceNames, member.childrenNames,
            member.educationLevel, member.schoolName, member.whereabouts,
            member.phoneNumber, member.emailContact, member.otherContact, member.bio,
            profile_photo_filename, needs_details_update, can_message,
            member.id
        ))
        # Update user's originalName if the member is linked to a user
        if member.user_id:
            db.execute('UPDATE users SET originalName = ? WHERE id = ?', (member.fullName, member.user_id)) # Removed relationshipToRaphael update
        db.commit()
        flash('Member profile updated successfully!', 'success')
        return redirect(url_for('member_detail', member_id=member.id))

    member_dict = dict(member_data) # Use member_data from DB for GET request
    member_dict.update(form_data) # Overlay any POST data if validation failed
    # Ensure lists for template display on GET
    for key in ['spouseNames', 'fianceNames', 'childrenNames']:
        if member_dict.get(key) and isinstance(member_dict[key], str):
            member_dict[key] = [item.strip() for item in member_dict[key].split(',') if item.strip()]
        else:
            member_dict[key] = []
    # Ensure current photo path for display
    member_dict['profilePhoto'] = url_for('uploaded_file', filename=os.path.basename(member_dict['profilePhoto'])) if member_dict['profilePhoto'] else url_for('static', filename='img/default_profile.png')


    return render_template('edit_member.html', member=member_dict, form_data=form_data)


@app.route('/delete_member/<int:member_id>', methods=['POST'])
@login_required
def delete_member(member_id):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()

    if not member:
        flash("Member not found.", "danger")
        return redirect(url_for('dashboard'))

    if not current_user.is_admin and (member['user_id'] is None or current_user.id != member['user_id']):
        flash("You are not authorized to delete this member's profile.", "danger")
        return redirect(url_for('dashboard'))

    try:
        if member['profilePhoto'] and os.path.exists(os.path.join(app.root_path, member['profilePhoto'])):
            os.remove(os.path.join(app.root_path, member['profilePhoto']))

        db.execute('DELETE FROM members WHERE id = ?', (member_id,))
        db.commit()
        flash(f'{member["fullName"]} has been deleted.', 'success')
    except sqlite3.Error as e:
        flash(f"Database error during deletion: {e}", 'danger')

    return redirect(url_for('dashboard'))

@app.route('/upload_status', methods=['GET', 'POST'])
@login_required
def upload_status():
    db = get_db()
    member_profile = db.execute('SELECT id FROM members WHERE user_id = ?', (current_user.id,)).fetchone()

    if not member_profile:
        flash("You need to add your personal details before uploading a status.", "danger")
        return redirect(url_for('add_my_details'))

    member_id = member_profile['id']

    if request.method == 'POST':
        status_file = request.files.get('status_file')

        if not status_file or not status_file.filename:
            flash('No file selected for upload.', 'danger')
            return render_template('status_feed.html')

        filename = secure_filename(status_file.filename)
        file_extension = os.path.splitext(filename)[1].lower()
        is_video = 0
        upload_folder_path = app.config['UPLOAD_FOLDER']

        if file_extension in allowed_video_file(filename):
            is_video = 1
            upload_folder_path = app.config['UPLOAD_VIDEO_FOLDER']
        elif file_extension in allowed_file(filename): # Check general allowed images
            is_video = 0
            upload_folder_path = app.config['UPLOAD_FOLDER']
        else:
            flash('Unsupported file type. Please upload an image or video.', 'danger')
            return render_template('upload_status.html')

        unique_filename = f"{uuid.uuid4()}_{filename}"
        file_path = os.path.join(upload_folder_path, unique_filename)
        status_file.save(file_path)

        db_file_path = os.path.relpath(file_path, app.root_path)
        db_file_path = db_file_path.replace('\\', '/')

        db.execute('DELETE FROM statuses WHERE member_id = ?', (member_id,))

        db.execute(
            'INSERT INTO statuses (member_id, file_path, upload_time, is_video, uploader_user_id) VALUES (?, ?, ?, ?, ?)',
            (member_id, db_file_path, datetime.utcnow(), is_video, current_user.id)
        )
        db.commit()
        flash('Status uploaded successfully!', 'success')
        return redirect(url_for('my_profile'))

    return render_template('status_feed.html')


@app.route('/status_feed')
@login_required
def status_feed():
    db = get_db()
    statuses = db.execute('''
        SELECT s.*, m.fullName, m.profilePhoto, u.username, u.id as user_id
        FROM statuses s
        JOIN members m ON s.member_id = m.id
        LEFT JOIN users u ON m.user_id = u.id
        WHERE (DATETIME(s.upload_time) > DATETIME('now', '-12 hours'))
          AND (u.username IS NULL OR u.username != 'AdminAI')
        ORDER BY s.upload_time DESC
    ''').fetchall()

    statuses_for_template = []
    for status in statuses:
        status_dict = dict(status)

        if status_dict['is_video']:
            status_dict['file_url'] = url_for('uploaded_video', filename=os.path.basename(status_dict['file_path']))
        else:
            status_dict['file_url'] = url_for('uploaded_file', filename=os.path.basename(status_dict['file_path']))

        if status_dict['profilePhoto']:
            status_dict['profile_photo_url'] = url_for('uploaded_file', filename=os.path.basename(status_dict['profilePhoto']))
        else:
            status_dict['profile_photo_url'] = url_for('static', filename='img/default_profile.png')

        statuses_for_template.append(status_dict)

    return render_template('status_feed.html', statuses=statuses_for_template)


@app.route('/delete_status/<int:member_id>', methods=['POST'])
@login_required
def delete_status(member_id):
    db = get_db()
    status_entry = db.execute(
        'SELECT id, file_path, is_video, uploader_user_id FROM statuses WHERE member_id = ? ORDER BY upload_time DESC LIMIT 1',
        (member_id,)
    ).fetchone()

    if not status_entry:
        flash("No active status found to delete.", "info")
        return redirect(url_for('my_profile'))

    if not current_user.is_admin and current_user.id != status_entry['uploader_user_id']:
        flash("You are not authorized to delete this status.", "danger")
        return redirect(url_for('my_profile'))

    try:
        # Full path to delete file from disk
        full_file_path = os.path.join(app.root_path, status_entry['file_path'])

        if os.path.exists(full_file_path):
            os.remove(full_file_path)

        db.execute('DELETE FROM statuses WHERE id = ?', (status_entry['id'],))
        db.commit()
        flash('Status deleted successfully!', 'success')
    except Exception as e:
        flash(f"Error deleting status: {e}", 'danger')

    return redirect(url_for('my_profile'))

@app.route('/admin/delete_status_by_admin/<int:status_id>', methods=['POST'])
@login_required
def delete_status_by_admin(status_id):
    if not current_user.is_admin:
        flash("Unauthorized access. Admins only.", "danger")
        return redirect(url_for('dashboard'))

    db = get_db()
    status_entry = db.execute(
        'SELECT id, file_path, is_video FROM statuses WHERE id = ?',
        (status_id,)
    ).fetchone()

    if not status_entry:
        flash("Status not found.", "info")
        return redirect(url_for('admin_manage_users'))

    try:
        full_file_path = os.path.join(app.root_path, status_entry['file_path'])

        if os.path.exists(full_file_path):
            os.remove(full_file_path)

        db.execute('DELETE FROM statuses WHERE id = ?', (status_id,))
        db.commit()
        flash('Status deleted by admin successfully!', 'success')
    except Exception as e:
        flash(f"Error deleting status by admin: {e}", 'danger')

    return redirect(url_for('admin_manage_users'))

# --- Settings Page & Theme/Chat Background ---

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    db = get_db()
    user_member_profile = db.execute('SELECT profilePhoto FROM members WHERE user_id = ?', (current_user.id,)).fetchone()
    profile_photo_path = user_member_profile['profilePhoto'] if user_member_profile else None
    if profile_photo_path:
        profile_photo_path = url_for('uploaded_file', filename=os.path.basename(profile_photo_path))

    current_chat_background = current_user.chat_background_image_path
    if current_chat_background:
        current_chat_background = url_for('chat_backgrounds', filename=os.path.basename(current_chat_background))


    if request.method == 'POST':
        new_theme = request.form.get('theme_preference')
        if new_theme in ['light', 'dark']:
            db.execute('UPDATE users SET theme_preference = ? WHERE id = ?', (new_theme, current_user.id))
            db.commit()
            current_user.theme_preference = new_theme
            flash('Theme updated successfully!', 'success')

        chat_background_file = request.files.get('chat_background_file')
        if chat_background_file and chat_background_file.filename:
            filename = secure_filename(chat_background_file.filename)
            unique_filename = f"{uuid.uuid4()}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_CHAT_BACKGROUND_FOLDER'], unique_filename)

            if current_user.chat_background_image_path and os.path.exists(os.path.join(app.root_path, current_user.chat_background_image_path)):
                os.remove(os.path.join(app.root_path, current_user.chat_background_image_path))

            chat_background_file.save(file_path)
            db_file_path = os.path.relpath(file_path, app.root_path).replace('\\', '/')

            db.execute('UPDATE users SET chat_background_image_path = ? WHERE id = ?', (db_file_path, current_user.id))
            db.commit()
            current_user.chat_background_image_path = db_file_path
            flash('Chat background updated successfully!', 'success')

        remove_background = request.form.get('remove_chat_background')
        if remove_background == '1':
            if current_user.chat_background_image_path and os.path.exists(os.path.join(app.root_path, current_user.chat_background_image_path)):
                os.remove(os.path.join(app.root_path, current_user.chat_background_image_path))
            db.execute('UPDATE users SET chat_background_image_path = NULL WHERE id = ?', (current_user.id,))
            db.commit()
            current_user.chat_background_image_path = None
            flash('Chat background removed successfully!', 'success')

        return redirect(url_for('settings'))

    return render_template('settings.html',
                           profile_photo_path=profile_photo_path,
                           current_chat_background=current_chat_background)


# --- Games Hub & Multiplayer Chess ---

@app.route('/games')
@login_required
def games_hub():
    return render_template('game_page.html')

@app.route('/invite_game/<game_name>/<int:recipient_id>', methods=['POST'])
@login_required
def invite_game(game_name, recipient_id):
    db = get_db()
    recipient = db.execute('SELECT id, username FROM users WHERE id = ?', (recipient_id,)).fetchone()
    if not recipient:
        flash("Recipient not found.", "danger")
        return redirect(url_for('members_list'))

    if current_user.id == recipient_id:
        flash("You cannot invite yourself to a game.", "danger")
        return redirect(url_for('members_list'))

    existing_invite = db.execute('''
        SELECT id FROM game_invitations
        WHERE (sender_id = ? AND recipient_id = ?)
           OR (sender_id = ? AND recipient_id = ?)
           AND game_name = ? AND status = 'pending'
    ''', (current_user.id, recipient_id, recipient_id, current_user.id, game_name)).fetchone()

    if existing_invite:
        flash(f"A pending {game_name.capitalize()} game invitation already exists with {recipient['username']}.", "warning")
        return redirect(url_for('members_list'))

    game_uuid = str(uuid.uuid4())

    db.execute(
        'INSERT INTO game_invitations (sender_id, recipient_id, game_name, status, timestamp, game_uuid) VALUES (?, ?, ?, ?, ?, ?)',
        (current_user.id, recipient_id, game_name, 'pending', datetime.utcnow(), game_uuid)
    )
    db.commit()
    flash(f'Invitation sent to {recipient["username"]} for a {game_name} game!', 'success')

    if game_name == 'chess':
        return redirect(url_for('play_game', game_name='chess', gameId=game_uuid))
    else:
        return redirect(url_for('inbox'))


@app.route('/accept_invite/<game_uuid>', methods=['POST'])
@login_required
def accept_invite(game_uuid):
    db = get_db()
    invite = db.execute(
        'SELECT * FROM game_invitations WHERE game_uuid = ? AND recipient_id = ? AND status = "pending"',
        (game_uuid, current_user.id)
    ).fetchone()

    if not invite:
        flash("Game invitation not found or already accepted/declined.", "danger")
        return redirect(url_for('inbox'))

    db.execute('UPDATE game_invitations SET status = "accepted" WHERE id = ?', (invite['id'],))

    if invite['game_name'] == 'chess':
        player_white_id = invite['sender_id']
        player_black_id = invite['recipient_id']
        if random.random() < 0.5: # Randomly assign colors
            player_white_id, player_black_id = player_black_id, player_white_id

        INITIAL_BOARD = json.dumps([
            ['r', 'n', 'b', 'q', 'k', 'b', 'n', 'r'],
            ['p', 'p', 'p', 'p', 'p', 'p', 'p', 'p'],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            ['P', 'P', 'P', 'P', 'P', 'P', 'P', 'P'],
            ['R', 'N', 'B', 'Q', 'K', 'B', 'N', 'R']
        ])
        initial_castling_rights = json.dumps({ 'wK': True, 'wQ': True, 'bK': True, 'bQ': True })

        db.execute(
            '''INSERT INTO multiplayer_games (game_uuid, game_name, player_white_id, player_black_id,
                       current_board_state, current_turn, white_captures, black_captures,
                       castling_rights, en_passant_target, last_move, game_over, winner_id, created_at, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (game_uuid, invite['game_name'], player_white_id, player_black_id,
             INITIAL_BOARD, 'w', 0, 0,
             initial_castling_rights, None, None, 0, None, datetime.utcnow(), datetime.utcnow())
        )
        db.commit()
        flash(f'You accepted the {invite["game_name"]} invitation! Game started.', 'success')
        return redirect(url_for('play_game', game_name=invite['game_name'], gameId=game_uuid))
    else:
        flash(f'You accepted the {invite["game_name"]} invitation!', 'success')
        return redirect(url_for('games_hub'))


@app.route('/decline_invite/<game_uuid>', methods=['POST'])
@login_required
def decline_invite(game_uuid):
    db = get_db()
    invite = db.execute(
        'SELECT * FROM game_invitations WHERE game_uuid = ? AND recipient_id = ? AND status = "pending"',
        (game_uuid, current_user.id)
    ).fetchone()

    if not invite:
        flash("Game invitation not found or already accepted/declined.", "danger")
    else:
        db.execute('UPDATE game_invitations SET status = "declined" WHERE id = ?', (invite['id'],))
        db.commit()
        flash(f'You declined the {invite["game_name"]} invitation.', 'info')
    return redirect(url_for('inbox'))


@app.route('/play_game/<game_name>', methods=['GET'])
@login_required
def play_game(game_name):
    game_uuid = request.args.get('gameId')

    if game_name == 'chess':
        if game_uuid:
            db = get_db()
            game_data = db.execute('SELECT * FROM multiplayer_games WHERE game_uuid = ?', (game_uuid,)).fetchone()
            if not game_data:
                flash("Multiplayer chess game not found.", "danger")
                return redirect(url_for('games_hub'))

            game_state_for_json = dict(game_data)

            for key, value in game_state_for_json.items():
                if isinstance(value, datetime):
                    game_state_for_json[key] = value.isoformat()
                # Parse JSON strings back into Python objects
                elif key in ['current_board_state', 'castling_rights', 'en_passant_target', 'last_move'] and isinstance(value, str):
                    try:
                        game_state_for_json[key] = json.loads(value)
                    except json.JSONDecodeError:
                        game_state_for_json[key] = None

            player_color = None
            if str(current_user.id) == str(game_state_for_json['player_white_id']):
                player_color = 'w'
            elif str(current_user.id) == str(game_state_for_json['player_black_id']):
                player_color = 'b'
            else:
                flash("You are not a participant in this game.", "danger")
                return redirect(url_for('games_hub'))

            return render_template(
                'multichess_game.html',
                game_uuid=game_uuid,
                player_color=player_color,
                initial_game_state=json.dumps(game_state_for_json),
                player_white_id=str(game_state_for_json['player_white_id']),
                player_black_id=str(game_state_for_json['player_black_id'])
            )
        else:
            return render_template('chess_game.html', game_id=None)
    elif game_name == 'racing':
        return render_template('racing_game.html', game_name='racing')
    elif game_name == 'board_games':
        return render_template('game_placeholder.html', game_name='board_games')
    else:
        return render_template('game_placeholder.html', game_name=game_name)


@socketio.on('make_move')
def handle_make_move(data):
    game_uuid = data['game_uuid']
    player_id = data['player_id']
    move_data = data['move_data']
    new_game_state = data['new_game_state']

    db = get_db()

    game_row = db.execute('SELECT * FROM multiplayer_games WHERE game_uuid = ?', (game_uuid,)).fetchone()
    if not game_row:
        emit('game_error', {'message': 'Game not found.'}, room=game_uuid)
        return

    # Check if it's the correct player's turn based on the game state (before update)
    # The 'current_turn' in new_game_state is the *next* turn, so we need the *previous* turn to validate the player.
    # The game_row['current_turn'] is the current turn *before* the move.
    current_turn_color = game_row['current_turn']

    is_white_player = (str(player_id) == str(game_row['player_white_id']))
    is_black_player = (str(player_id) == str(game_row['player_black_id']))

    if (current_turn_color == 'w' and not is_white_player) or \
       (current_turn_color == 'b' and not is_black_player):
        emit('game_error', {'message': 'It is not your turn.'}, room=game_uuid)
        return

    try:
        db.execute(
            '''UPDATE multiplayer_games SET
               current_board_state = ?, current_turn = ?, white_captures = ?, black_captures = ?,
               castling_rights = ?, en_passant_target = ?, last_move = ?, game_over = ?, winner_id = ?, last_updated = ?
               WHERE game_uuid = ?''',
            (json.dumps(new_game_state['current_board_state']), # Store as JSON string
             new_game_state['current_turn'],
             new_game_state['white_captures'],
             new_game_state['black_captures'],
             json.dumps(new_game_state['castling_rights']), # Store as JSON string
             json.dumps(new_game_state['en_passant_target']) if new_game_state['en_passant_target'] else None, # Store as JSON string or None
             json.dumps(new_game_state['last_move']) if new_game_state['last_move'] else None, # Store as JSON string or None
             1 if new_game_state['game_over'] else 0, # Convert boolean to int
             new_game_state['winner_id'],
             datetime.utcnow(),
             game_uuid)
        )

        move_number_row = db.execute('SELECT COUNT(*) FROM game_moves WHERE game_uuid = ?', (game_uuid,)).fetchone()
        move_number = (move_number_row[0] // 2) + 1

        db.execute(
            '''INSERT INTO game_moves (game_uuid, move_number, player_id, move_data, timestamp)
               VALUES (?, ?, ?, ?, ?)''',
            (game_uuid, move_number, player_id, json.dumps(move_data), datetime.utcnow())
        )
        db.commit()

        # Emit the new game state (parsed back to Python objects for client)
        emit('game_state_update', {
            'current_board_state': new_game_state['current_board_state'],
            'current_turn': new_game_state['current_turn'],
            'white_captures': new_game_state['white_captures'],
            'black_captures': new_game_state['black_captures'],
            'castling_rights': new_game_state['castling_rights'],
            'en_passant_target': new_game_state['en_passant_target'],
            'last_move': new_game_state['last_move'],
            'game_over': new_game_state['game_over'],
            'winner_id': new_game_state['winner_id']
        }, room=game_uuid)
        print(f"Move made in game {game_uuid} by {player_id}. New turn: {new_game_state['current_turn']}")

    except sqlite3.Error as e:
        print(f"Database error saving move for game {game_uuid}: {e}")
        emit('game_error', {'message': f'Database error: {e}'}, room=game_uuid)
    except Exception as e:
        print(f"Unexpected error saving move for game {game_uuid}: {e}")
        emit('game_error', {'message': f'An unexpected error occurred: {e}'}, room=game_uuid)

@socketio.on('request_game_state')
def handle_request_game_state(data):
    game_uuid = data['game_uuid']
    db = get_db()
    game_data = db.execute('SELECT * FROM multiplayer_games WHERE game_uuid = ?', (game_uuid,)).fetchone()

    if game_data:
        game_state_for_json = dict(game_data)

        for key, value in game_state_for_json.items():
            if isinstance(value, datetime):
                game_state_for_json[key] = value.isoformat()
            # Parse JSON strings back into Python objects
            elif key in ['current_board_state', 'castling_rights', 'en_passant_target', 'last_move'] and isinstance(value, str):
                try:
                    game_state_for_json[key] = json.loads(value)
                except json.JSONDecodeError:
                    game_state_for_json[key] = None

        emit('game_state_update', game_state_for_json, room=request.sid) # Emit only to the requesting client
    else:
        emit('game_error', {'message': 'Game state not found.'}, room=request.sid)


# --- Messaging Functions (UPDATED to use 'messages' table directly) ---

def get_user_conversations(user_id):
    db = get_db()
    conversations = []

    # Get distinct pairs of sender/recipient IDs involving the current user from the messages table
    # This identifies all direct chats the user is part of.
    distinct_chat_partners = db.execute(f'''
        SELECT DISTINCT
            CASE
                WHEN sender_id = ? THEN recipient_id
                ELSE sender_id
            END AS other_user_id
        FROM messages
        WHERE sender_id = ? OR recipient_id = ?
    ''', (user_id, user_id, user_id)).fetchall()

    for row in distinct_chat_partners:
        other_user_id = row['other_user_id']

        # Fetch other user's details
        other_user_data = db.execute('SELECT id, username, originalName FROM users WHERE id = ?', (other_user_id,)).fetchone()
        if not other_user_data:
            continue # Skip if other user not found (e.g., deleted account)

        other_user = dict(other_user_data)

        # Get the latest message for this conversation pair
        latest_message = db.execute(f'''
            SELECT sender_id, recipient_id, content, timestamp, media_path, media_type, is_read
            FROM messages
            WHERE (sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?)
            ORDER BY timestamp DESC LIMIT 1
        ''', (user_id, other_user_id, other_user_id, user_id)).fetchone()

        snippet = "No messages yet."
        is_unread = False
        message_timestamp = datetime.min # Initialize with minimum datetime

        if latest_message:
            sender_name_in_snippet = "You: " if latest_message['sender_id'] == user_id else f"{db.execute('SELECT originalName FROM users WHERE id = ?', (latest_message['sender_id'],)).fetchone()['originalName']}: "
            snippet = sender_name_in_snippet

            if latest_message['content']:
                snippet += latest_message['content']
            elif latest_message['media_type'] == 'image':
                snippet += "🖼️ Image"
            elif latest_message['media_type'] == 'video':
                snippet += "🎥 Video"
            elif latest_message['media_type'] == 'audio':
                snippet += "🎵 Audio"

            # FIX: Only call strptime if it's actually a string.
            # Otherwise, assume it's already a datetime object due to PARSE_DECLTYPES.
            if isinstance(latest_message['timestamp'], datetime):
                message_timestamp = latest_message['timestamp']
            else: # It's a string, try to parse
                try:
                    message_timestamp = datetime.strptime(str(latest_message['timestamp']), '%Y-%m-%d %H:%M:%S.%f' if '.' in str(latest_message['timestamp']) else '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    message_timestamp = datetime.min # Fallback if parsing fails

            # Determine unread status: if it's a message from the other person and it's not read
            if latest_message['recipient_id'] == user_id and latest_message['is_read'] == 0:
                is_unread = True

        conversations.append({
            'other_user': other_user,
            'latest_message_snippet': snippet,
            'timestamp': message_timestamp,
            'is_unread': is_unread,
            'other_user_id': other_user['id'] # Important for linking to chat route
        })

    # Sort conversations by latest message timestamp
    conversations.sort(key=lambda x: x['timestamp'], reverse=True)
    return conversations

def get_game_invitations(user_id):
    db = get_db()
    invites = db.execute('''
        SELECT gi.*, u.username AS sender_username, u.originalName AS sender_originalName
        FROM game_invitations gi
        JOIN users u ON gi.sender_id = u.id
        WHERE gi.recipient_id = ? AND gi.status = 'pending'
        ORDER BY gi.timestamp DESC
    ''', (user_id,)).fetchall()
    return [dict(invite) for invite in invites]


@app.route('/inbox')
@login_required
def inbox():
    conversations = get_user_conversations(current_user.id)
    game_invitations = get_game_invitations(current_user.id)
    return render_template('inbox.html', conversations=conversations, game_invitations=game_invitations)

# Import necessary modules if not already at the top of app.py
# from datetime import datetime, ti

@app.route('/message_member')
@login_required
def message_member():
    """
    Renders the page to start a new conversation, listing all messageable members.
    Prepares member data for JavaScript, including profile photos and online status.
    """
    db = get_db()

    # Fetch all members who have a linked user account and are not the current user
    # Order by fullName for consistent display
    messageable_members_raw = db.execute('''
        SELECT m.id, m.fullName, m.profilePhoto, m.user_id,
               u.username as user_username, u.originalName as user_originalName, u.last_seen_at as user_last_seen_at
        FROM members m
        JOIN users u ON m.user_id = u.id
        WHERE m.user_id != ? AND u.username != 'AdminAI' -- Exclude current user and AdminAI from this list initially
        ORDER BY m.fullName ASC
    ''', (current_user.id,)).fetchall()

    messageable_members_for_template = []
    now_utc = datetime.utcnow()

    for row in messageable_members_raw:
        member_dict = dict(row)

        # Ensure fullName is always a string
        member_dict['fullName'] = member_dict.get('fullName', '')

        # Username (use alias from query, default to empty string)
        member_dict['username'] = member_dict.get('user_username', '')

        # Profile photo URL
        profile_photo_filename = os.path.basename(member_dict.get('profilePhoto', '')) if member_dict.get('profilePhoto') else None
        member_dict['profilePhotoUrl'] = url_for('uploaded_file', filename=profile_photo_filename) if profile_photo_filename else url_for('static', filename='img/default_profile.png')

        # Online Status for linked users
        member_dict['is_online'] = False
        member_dict['last_seen_at_display'] = None # To store human-readable time

        user_last_seen_at_raw = member_dict.get('user_last_seen_at')
        if user_last_seen_at_raw:
            # FIX: Check if already datetime before strptime
            if isinstance(user_last_seen_at_raw, datetime):
                last_seen_dt = user_last_seen_at_raw
            else: # It's a string, parse it
                try:
                    last_seen_dt = datetime.strptime(str(user_last_seen_at_raw), '%Y-%m-%d %H:%M:%S.%f' if '.' in str(user_last_seen_at_raw) else '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    last_seen_dt = None # Set to None if parsing fails

            if last_seen_dt: # Only proceed if a valid datetime object was obtained
                # Check if last seen within the last 5 minutes (adjust as desired)
                if (now_utc - last_seen_dt) < timedelta(minutes=5):
                    member_dict['is_online'] = True

                # Convert to ISO format string for moment.js on frontend
                member_dict['last_seen_at_display'] = last_seen_dt.isoformat()
            # else: last_seen_at_display remains None and is_online remains False


        # Remove raw datetime objects and unused aliases before JSON serialization
        member_dict.pop('user_username', None)
        member_dict.pop('user_originalName', None)
        member_dict.pop('user_last_seen_at', None) # We've processed it, don't need raw

        messageable_members_for_template.append(member_dict)

    # Fetch AdminAI user_id separately, as it's typically an option regardless of member status
    admin_ai_user = db.execute("SELECT id FROM users WHERE username = 'AdminAI'").fetchone()
    ai_user_id = admin_ai_user['id'] if admin_ai_user else None

    # Pass the prepared list of messageable members and the AI user ID to the template
    return render_template('message_member.html',
                           members=messageable_members_for_template, # Renamed to 'members' to match template
                           ai_user_id=ai_user_id,
                           current_user_id=current_user.id) # Ensure current_user_id is available for JS



# UPDATED ROUTE: Initiate chat, now directly links to view_direct_chat
@app.route('/initiate_chat/<int:recipient_id>', methods=['GET'])
@login_required
def initiate_chat(recipient_id):
    """
    Initiates or continues a direct chat with a specific user.
    Redirects to the direct_chat route.
    """
    db = get_db()

    # Prevent self-chatting
    if current_user.id == recipient_id:
        flash("You cannot chat with yourself.", "danger")
        return redirect(url_for('inbox'))

    # Verify recipient exists
    recipient_user = db.execute('SELECT id, username, originalName FROM users WHERE id = ?', (recipient_id,)).fetchone()
    if not recipient_user:
        flash("Recipient user not found.", "danger")
        return redirect(url_for('inbox'))

    # Redirect to the direct_chat route
    return redirect(url_for('view_direct_chat', other_user_id=recipient_id))


@app.route('/direct_chat/<int:other_user_id>', methods=['GET'], endpoint='message_with')
@login_required
def view_direct_chat(other_user_id):
    db = get_db()

    if current_user.id == other_user_id:
        flash("You cannot view a direct chat with yourself.", "danger")
        return redirect(url_for('inbox'))

    # CORRECTED SQL QUERY: Join 'users' with 'members' to get profilePhoto
    other_user_data_raw = db.execute('''
        SELECT u.id, u.username, u.originalName, u.last_seen_at, m.profilePhoto, m.can_message
        FROM users u
        LEFT JOIN members m ON u.id = m.user_id
        WHERE u.id = ?
    ''', (other_user_id,)).fetchone()

    if not other_user_data_raw:
        flash("Other user not found.", "danger")
        return redirect(url_for('inbox'))

    other_user = dict(other_user_data_raw) # Convert Row object to dict

    # Process profile photo path for URL
    if other_user['profilePhoto']:
        # Ensure only the filename is passed to url_for, as your 'uploaded_file' expects a filename
        other_user['profilePhoto'] = url_for('uploaded_file', filename=os.path.basename(other_user['profilePhoto']))
    else:
        other_user['profilePhoto'] = url_for('static', filename='img/default_profile.png')

    # Fetch messages between current_user and other_user
    messages = db.execute(
        '''SELECT m.*, u.username AS sender_username, u.originalName AS sender_originalName
           FROM messages m
           LEFT JOIN users u ON m.sender_id = u.id
           WHERE (m.sender_id = ? AND m.recipient_id = ?)
              OR (m.sender_id = ? AND m.recipient_id = ?)
           ORDER BY m.timestamp ASC''',
        (current_user.id, other_user_id, other_user_id, current_user.id)
    ).fetchall()

    messages_for_template = []
    for msg in messages:
        msg_dict = dict(msg)
        # FIX: Check if timestamp is already a datetime object (from PARSE_DECLTYPES)
        if isinstance(msg_dict['timestamp'], datetime):
            msg_dict['timestamp_dt'] = msg_dict['timestamp']
        elif msg_dict['timestamp']: # It's a string, parse it
            try:
                msg_dict['timestamp_dt'] = datetime.strptime(msg_dict['timestamp'], '%Y-%m-%d %H:%M:%S.%f' if '.' in str(msg_dict['timestamp']) else '%Y-%m-%d %H:%M:%S')
            except ValueError:
                msg_dict['timestamp_dt'] = None # Set to None if parsing fails
        else:
            msg_dict['timestamp_dt'] = None
        messages_for_template.append(msg_dict)

    chat_background_image_path = current_user.chat_background_image_path
    if chat_background_image_path:
        chat_background_image_path = url_for('chat_backgrounds', filename=os.path.basename(chat_background_image_path))

    # Mark messages sent to current_user from other_user as read
    db.execute(
        'UPDATE messages SET is_read = 1 WHERE sender_id = ? AND recipient_id = ? AND is_read = 0',
        (other_user_id, current_user.id)
    )
    db.commit()

    # Calculate is_online for other_user
    now_utc = datetime.utcnow() # Use UTC for consistent comparison
    is_online = False
    last_seen_at = other_user['last_seen_at']
    if last_seen_at:
        # FIX: Check if last_seen_at is already a datetime object
        if isinstance(last_seen_at, datetime):
            last_seen_dt = last_seen_at
        elif isinstance(last_seen_at, str):
            try:
                last_seen_dt = datetime.strptime(last_seen_at, '%Y-%m-%d %H:%M:%S.%f' if '.' in last_seen_at else '%Y-%m-%d %H:%M:%S')
            except ValueError:
                last_seen_dt = None # Handle unexpected type
        else:
            last_seen_dt = None # Handle unexpected type

        if last_seen_dt: # Only compare if it's a valid datetime object
            if (now_utc - last_seen_dt) < timedelta(minutes=5):
                is_online = True
    # else: is_online remains False if last_seen_at is None or invalid

    other_user['is_online'] = is_online
    # Pass raw last_seen_at string if it was a string initially, or isoformat if it's a datetime for Moment.js
    # Ensure other_user['last_seen_at'] is consistently what Moment.js expects
    # If it was a datetime object, isoformat it. If it was None or a string, pass as is.
    if isinstance(other_user_data_raw['last_seen_at'], datetime):
        other_user['last_seen_at'] = other_user_data_raw['last_seen_at'].isoformat()
    else:
        other_user['last_seen_at'] = other_user_data_raw['last_seen_at']


    # Fetch ai_user_id
    admin_ai_user = db.execute("SELECT id FROM users WHERE username = 'AdminAI'").fetchone()
    ai_user_id = admin_ai_user['id'] if admin_ai_user else None

    # Pass the other_user dictionary directly
    return render_template('view_conversation.html',
                           other_user=other_user,
                           messages_for_template=messages_for_template, # Corrected variable name
                           current_user_id=current_user.id,
                           chat_background_image_path=chat_background_image_path,
                           recipient_username=other_user.get('originalName', other_user.get('username')), # Defensive access
                           ai_user_id=ai_user_id)


# UPDATED ROUTE: Send direct message (replaces /send_chat_message/<int:chat_room_id>)
@app.route('/send_direct_message/<int:recipient_id>', methods=['POST'])
@login_required
def send_direct_message(recipient_id):
    db = get_db()

    if current_user.id == recipient_id:
        return jsonify({'status': 'error', 'message': 'You cannot send a message to yourself.'}), 400

    recipient_user = db.execute('SELECT id FROM users WHERE id = ?', (recipient_id,)).fetchone()
    if not recipient_user:
        return jsonify({'status': 'error', 'message': 'Recipient user not found.'}), 404

    content = request.form.get('message', '').strip()
    media_file = request.files.get('media_file') # Corrected: 'media' to 'media_file' to match form name

    media_path = None
    media_type = None

    if media_file and media_file.filename:
        filename = secure_filename(media_file.filename)
        file_extension = os.path.splitext(filename)[1].lower()

        if file_extension in allowed_chat_image_file(filename):
            media_type = 'image'
            upload_folder = os.path.join(app.root_path, app.config['UPLOAD_CHAT_PHOTO_FOLDER'])
        elif file_extension in allowed_chat_video_file(filename):
            media_type = 'video'
            upload_folder = os.path.join(app.root_path, app.config['UPLOAD_CHAT_VIDEO_FOLDER'])
        elif file_extension in allowed_chat_audio_file(filename):
            media_type = 'audio'
            upload_folder = os.path.join(app.root_path, app.config['UPLOAD_CHAT_AUDIO_FOLDER'])
        else:
            return jsonify({'status': 'error', 'message': 'Unsupported media file type.'}), 400

        unique_filename = f"{uuid.uuid4()}_{filename}"
        file_full_path = os.path.join(upload_folder, unique_filename)
        media_file.save(file_full_path)
        media_path = os.path.relpath(file_full_path, app.root_path).replace('\\', '/')


    if not content and not media_path:
        return jsonify({'status': 'error', 'message': 'Message cannot be empty.'}), 400

    try:
        timestamp = datetime.utcnow()
        is_admin_message = 0 # Direct messages are not admin messages

        db.execute(
            'INSERT INTO messages (sender_id, recipient_id, content, timestamp, media_path, media_type, is_admin_message) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (current_user.id, recipient_id, content, timestamp, media_path, media_type, is_admin_message)
        )
        db.commit()

        sender_info = db.execute('SELECT username, originalName FROM users WHERE id = ?', (current_user.id,)).fetchone()
        sender_username = sender_info['username'] if sender_info else 'Unknown User'
        sender_originalName = sender_info['originalName'] if sender_info else 'Unknown User'

        message_to_emit = {
            'sender_id': current_user.id,
            'sender_username': sender_username,
            'sender_originalName': sender_originalName,
            'content': content,
            'media_path': media_path,
            'media_type': media_type,
            'timestamp': timestamp.isoformat(),
            'is_ai_message': is_admin_message, # Renamed to align with client expectations
            'recipient_id': recipient_id # Add recipient_id for client-side routing of messages
        }

        # Emit to the sender's own session and the recipient's session
        # To ensure real-time updates for both parties, we need to know their SIDs.
        # This can be done by joining rooms named after user IDs.
        # Ensure that users join their own ID room on connect.
        socketio.emit('receive_direct_message', message_to_emit, room=str(current_user.id))
        socketio.emit('receive_direct_message', message_to_emit, room=str(recipient_id))


        # Note: AI response logic from previous send_chat_message is handled by /api/send_ai_message
        # and not part of send_direct_message.

        return jsonify({'status': 'success'}), 200

    except sqlite3.Error as e:
        print(f"Database error sending direct message: {e}")
        return jsonify({'status': 'error', 'message': f'Database error: {e}'}), 500
    except Exception as e:
        print(f"Unexpected error sending direct message: {e}")
        return jsonify({'status': 'error', 'message': f'An unexpected error occurred: {e}'}), 500


# SocketIO event for joining personal rooms for direct messaging
@socketio.on('join_personal_room')
@login_required
def on_join_personal_room(data):
    user_id_room = str(current_user.id)
    join_room(user_id_room)
    print(f"User {current_user.id} joined personal room: {user_id_room}")

# SocketIO event for leaving personal rooms
@socketio.on('leave_personal_room')
@login_required
def on_leave_personal_room(data):
    user_id_room = str(current_user.id)
    leave_room(user_id_room)
    print(f"User {current_user.id} left personal room: {user_id_room}")

# Function to generate AI response (placeholder - AI chat uses Firestore API route)
def generate_ai_response(user_message):
    # This function is retained for completeness but AI chat now primarily goes through /api/send_ai_message
    try:
        if not hasattr(config, 'GEMINI_API_KEY') or not config.GEMINI_API_KEY:
            print("GEMINI_API_KEY not found in config.py")
            return "Sorry, AI is not configured. Please contact support."

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')

        response = model.generate_content(user_message)
        return response.text
    except Exception as e:
        print(f"Error generating AI response: {e}")
        return "Sorry, I'm having trouble responding right now. (AI error)"


if __name__ == '__main__':
    # When running directly, ensure DB is initialized and AI user exists
    with app.app_context():
        db_file_exists = os.path.exists(DATABASE)
        if not db_file_exists:
            init_db()
        create_ai_user_and_member()

    # Run the Flask app with SocketIO
    socketio.run(app, debug=True)
