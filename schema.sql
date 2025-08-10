-- schema.sql

-- Drop existing tables (order matters due to foreign keys)
DROP TABLE IF EXISTS game_invitations;
DROP TABLE IF EXISTS game_moves;
DROP TABLE IF EXISTS multiplayer_games;
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS chat_room_members;
DROP TABLE IF EXISTS chat_rooms;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS statuses;
DROP TABLE IF EXISTS members;
DROP TABLE IF EXISTS users;

-- Create users table
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    originalName TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0, -- 0 for regular user, 1 for admin
    theme_preference TEXT DEFAULT 'light',
    chat_background_image_path TEXT,
    unique_key TEXT UNIQUE NOT NULL, -- For password recovery
    password_reset_pending INTEGER DEFAULT 0, -- 1 if admin initiated reset, 0 otherwise
    reset_request_timestamp TIMESTAMP, -- Timestamp of user's reset request (for auto-initiation)
    last_login_at TIMESTAMP, -- NEW: Timestamp of last login
    last_seen_at TIMESTAMP -- NEW: Timestamp of last activity/seen
);

-- Create members table
CREATE TABLE members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fullName TEXT NOT NULL,
    gender TEXT NOT NULL,
    dateOfBirth TEXT, -- YYYY-MM-DD
    maritalStatus TEXT, -- e.g., 'Single', 'Married', 'Engaged', 'Divorced', 'Widowed' (Nullable)
    spouseNames TEXT, -- Comma-separated names (Nullable)
    fianceNames TEXT, -- Comma-separated names (Nullable)
    childrenNames TEXT, -- Comma-separated names (Nullable)
    educationLevel TEXT, -- e.g., 'None', 'Primary', 'High School', 'University' (Nullable)
    schoolName TEXT, -- Name of institution (Nullable)
    whereabouts TEXT, -- Current city, country
    phoneNumber TEXT, -- (Nullable)
    emailContact TEXT, -- (Nullable)
    otherContact TEXT, -- (Nullable)
    bio TEXT,
    profilePhoto TEXT,
    user_id INTEGER UNIQUE, -- Foreign key to users table, NULL if no associated user account
    needs_details_update INTEGER DEFAULT 0, -- 1 if admin needs to add more details
    added_by_user_id INTEGER, -- User ID of the person who added this member
    can_message INTEGER DEFAULT 1, -- NEW: 1 if linked user can message, 0 otherwise
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (added_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Create messages table (for direct/private messages)
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_read INTEGER DEFAULT 0, -- 0 for unread, 1 for read
    is_admin_message INTEGER DEFAULT 0, -- NEW: 1 if message is from admin
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Create statuses table (for temporary video/photo statuses)
CREATE TABLE statuses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL UNIQUE, -- Only one active status per member
    file_path TEXT NOT NULL,
    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_video INTEGER DEFAULT 0, -- 0 for photo, 1 for video
    uploader_user_id INTEGER NOT NULL,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
    FOREIGN KEY (uploader_user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Create chat_rooms table (for group chats and AI chats)
CREATE TABLE chat_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE, -- Name of the chat room (e.g., "Family Chat", "AdminAI")
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_group_chat INTEGER DEFAULT 0 -- 0 for direct/AI chat, 1 for group chat
);

-- Create chat_room_members table (to link users to chat rooms)
CREATE TABLE chat_room_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_room_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_admin INTEGER DEFAULT 0, -- 1 if user is admin of this specific chat room
    FOREIGN KEY (chat_room_id) REFERENCES chat_rooms(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (chat_room_id, user_id) -- Ensures a user can only be in a room once
);

-- Create chat_messages table
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_room_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    content TEXT, -- Message text (can be NULL if only media)
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    media_path TEXT, -- Path to media file (NULL if no media)
    media_type TEXT, -- 'image', 'video', 'audio' (NULL if no media)
    is_ai_message INTEGER DEFAULT 0, -- 1 if message is from AI, 0 otherwise
    FOREIGN KEY (chat_room_id) REFERENCES chat_rooms(id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
);

-- NEW TABLE FOR GAME INVITATIONS
CREATE TABLE game_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    game_name TEXT NOT NULL, -- e.g., 'chess', 'racing'
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'accepted', 'declined'
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    game_uuid TEXT UNIQUE NOT NULL, -- Unique ID for the game instance
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
);

-- NEW TABLE FOR MULTIPLAYER GAMES (e.g., Chess game states)
CREATE TABLE multiplayer_games (
    game_uuid TEXT PRIMARY KEY, -- Matches game_invitations.game_uuid
    game_name TEXT NOT NULL,
    player_white_id INTEGER,
    player_black_id INTEGER,
    current_board_state TEXT NOT NULL, -- JSON string of the board
    current_turn TEXT NOT NULL, -- 'w' or 'b'
    white_captures INTEGER DEFAULT 0,
    black_captures INTEGER DEFAULT 0,
    castling_rights TEXT NOT NULL, -- JSON string of castling rights
    en_passant_target TEXT, -- JSON string of {row, col} or NULL
    last_move TEXT, -- JSON string of {from: {r,c}, to: {r,c}} or NULL
    game_over INTEGER DEFAULT 0, -- 0 for active, 1 for game over
    winner_id INTEGER, -- User ID of the winner, NULL for draw or ongoing
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (player_white_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (player_black_id) REFERENCES users(id) ON DELETE SET NULL
);

-- NEW TABLE FOR GAME MOVES HISTORY
CREATE TABLE game_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_uuid TEXT NOT NULL,
    move_number INTEGER NOT NULL, -- e.g., 1 for White's first move, 1 for Black's first move
    player_id INTEGER NOT NULL,
    move_data TEXT NOT NULL, -- JSON string of move details (e.g., from, to, piece, captured)
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_uuid) REFERENCES multiplayer_games(game_uuid) ON DELETE CASCADE,
    FOREIGN KEY (player_id) REFERENCES users(id) ON DELETE CASCADE
);
