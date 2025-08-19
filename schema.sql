-- schema.sql (COMPATIBLE VERSION)

-- Drop existing tables (order matters due to foreign keys)
DROP TABLE IF EXISTS game_invitations;
DROP TABLE IF EXISTS game_moves;
DROP TABLE IF EXISTS multiplayer_games;
DROP TABLE IF EXISTS messages; -- Keep this for direct messages
DROP TABLE IF EXISTS statuses;
DROP TABLE IF EXISTS members;
DROP TABLE IF EXISTS users;

-- Create users table (compatible with app.py)
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

-- Create members table (compatible with app.py)
CREATE TABLE members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fullName TEXT NOT NULL,
    gender TEXT,
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
    added_by_user_id INTEGER NOT NULL,
    can_message INTEGER DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (added_by_user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Renamed from temporary_videos to statuses (compatible with app.py)
CREATE TABLE statuses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_video INTEGER DEFAULT 0,
    uploader_user_id INTEGER NOT NULL,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (uploader_user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Table for private messages (1-to-1) - This is what app.py uses for direct chats
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    content TEXT, -- Made nullable to allow media-only messages
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_read INTEGER DEFAULT 0,
    media_path TEXT, -- Path to media file
    media_type TEXT, -- 'image', 'video', 'audio'
    is_admin_message INTEGER DEFAULT 0, -- Used for AI messages (though AI itself uses Firestore)
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
);

-- REMOVED: chat_rooms, chat_room_members, chat_messages to avoid conflict
-- These tables are not used by the current app.py's messaging logic for direct chat.


-- NEW TABLE FOR GAME INVITATIONS (compatible with app.py)
CREATE TABLE game_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    game_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    game_uuid TEXT UNIQUE NOT NULL,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
);

-- NEW TABLE FOR MULTIPLAYER GAME STATES (e.g., Chess) - ADDED missing columns
CREATE TABLE multiplayer_games (
    game_uuid TEXT PRIMARY KEY, -- Changed to PRIMARY KEY and removed id for consistency with app.py's GameInvite and Game State handling
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
    game_over INTEGER DEFAULT 0, -- Added missing column
    winner_id INTEGER, -- Added missing column
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (player_white_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (player_black_id) REFERENCES users(id) ON DELETE CASCADE
);

-- NEW TABLE FOR GAME MOVES HISTORY (compatible with app.py)
CREATE TABLE game_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_uuid TEXT NOT NULL,
    move_number INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    move_data TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_uuid) REFERENCES multiplayer_games(game_uuid) ON DELETE CASCADE,
    FOREIGN KEY (player_id) REFERENCES users(id) ON DELETE CASCADE
);
