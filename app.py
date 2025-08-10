import sqlite3
import os
from datetime import datetime, timedelta, timezone
import random
import string
import json
import uuid # Import uuid for generating unique game IDs

# Assuming these are set up if you're using Google Gemini API
import google.generativeai as genai

# Assuming these are set up if you're using Firebase for chat background storage
import firebase_admin
from firebase_admin import credentials, initialize_app, firestore


from flask import Flask, render_template, Blueprint, request, redirect, url_for, g, flash, session, abort, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_moment import Moment
from flask_socketio import SocketIO, emit, join_room, leave_room # Flask-SocketIO imports

import config # Assuming config.py exists for SECRET_KEY

app = Flask(__name__)
# Use environment variable for SECRET_KEY or fall back to config.py
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', config.SECRET_KEY)

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'family_tree.db')

app.config['UPLOAD_FOLDER'] = os.path.join('static', 'img', 'profile_photos')
app.config['UPLOAD_VIDEO_FOLDER'] = os.path.join('static', 'videos', 'status_videos')
app.config['UPLOAD_CHAT_PHOTO_FOLDER'] = os.path.join('static', 'chat_media', 'photos')
app.config['UPLOAD_CHAT_VIDEO_FOLDER'] = os.path.join('static', 'chat_media', 'videos')
app.config['UPLOAD_CHAT_BACKGROUND_FOLDER'] = os.path.join('static', 'img', 'chat_backgrounds') # NEW folder for chat backgrounds

# Ensure upload folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['UPLOAD_VIDEO_FOLDER'], exist_ok=True)
os.makedirs(app.config['UPLOAD_CHAT_PHOTO_FOLDER'], exist_ok=True)
os.makedirs(app.config['UPLOAD_CHAT_VIDEO_FOLDER'], exist_ok=True)
os.makedirs(app.config['UPLOAD_CHAT_BACKGROUND_FOLDER'], exist_ok=True)


# Initialize Flask-Moment
moment = Moment(app)

# Initialize Flask-SocketIO
socketio = SocketIO(app)

# Configure Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Route to redirect to if user is not logged in

class User(UserMixin):
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
        self.admin_reset_approved = admin_reset_approved # NEW: admin approval for reset

    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
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
            admin_reset_approved=bool(user['admin_reset_approved']) # NEW
        )
    return None

# Database helper functions
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()

@app.cli.command('initdb')
def init_db_command():
    """Initializes the database."""
    init_db()
    print('Initialized the database.')

# --- User Authentication and Management ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        original_name = request.form['original_name'].strip()
        gender = request.form.get('gender') # Gender is now optional
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        db = get_db()
        error = None

        if not username or not original_name or not password or not confirm_password:
            error = 'Please fill in all required fields (Username, Full Name, Password, Confirm Password).'
        elif password != confirm_password:
            error = 'Passwords do not match.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters long.'
        elif db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone() is not None:
            error = 'Username {} is already taken.'.format(username)

        if error is None:
            hashed_password = generate_password_hash(password)
            unique_key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10)) # Generate unique key

            db.execute(
                'INSERT INTO users (username, originalName, password_hash, is_admin, unique_key, theme_preference, last_login_at, last_seen_at, admin_reset_approved) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', # Added admin_reset_approved
                (username, original_name, hashed_password, 0, unique_key, 'light', datetime.utcnow(), datetime.utcnow(), 0) # Default to 0
            )
            db.commit()

            user_id = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()['id']

            # Create a corresponding member profile for the new user
            db.execute(
                '''INSERT INTO members (fullName, gender, dateOfBirth, maritalStatus, spouseNames, fianceNames, childrenNames, educationLevel, schoolName, whereabouts, phoneNumber, emailContact, otherContact, bio, profilePhoto, user_id, added_by_user_id, can_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (original_name, gender, None, 'Single', '', '', '', 'None', '', '', '', '', '', '', None, user_id, user_id, 1)
            )
            db.commit()
            flash('Registration successful! Your unique key is: {}. Please keep it safe.'.format(unique_key), 'success')
            return redirect(url_for('login'))
        else:
            flash(error, 'danger')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Check if password reset is pending for this user after login
        if current_user.password_reset_pending and (current_user.admin_reset_approved or (datetime.utcnow() - datetime.strptime(str(current_user.reset_request_timestamp), '%Y-%m-%d %H:%M:%S.%f' if '.' in str(current_user.reset_request_timestamp) else '%Y-%m-%d %H:%M:%S')) >= timedelta(seconds=10)):
            flash('Your password reset request has been approved or automatically activated. Please set your new password.', 'info')
            return redirect(url_for('reset_password_route', username=current_user.username, unique_key=current_user.unique_key)) # Pass username and key for validation
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        user = db.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone()

        if user is None:
            flash('Incorrect username or password.', 'danger')
        elif not check_password_hash(user['password_hash'], password):
            flash('Incorrect username or password.', 'danger')
        else:
            user_obj = User(
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
                admin_reset_approved=bool(user['admin_reset_approved']) # NEW
            )
            login_user(user_obj)
            # Update last_login_at and last_seen_at
            db.execute('UPDATE users SET last_login_at = ?, last_seen_at = ? WHERE id = ?',
                       (datetime.utcnow(), datetime.utcnow(), current_user.id))
            db.commit()
            flash('Logged in successfully!', 'success')
            # Check for password reset status immediately after login
            if user_obj.password_reset_pending and (user_obj.admin_reset_approved or (datetime.utcnow() - datetime.strptime(str(user_obj.reset_request_timestamp), '%Y-%m-%d %H:%M:%S.%f' if '.' in str(user_obj.reset_request_timestamp) else '%Y-%m-%d %H:%M:%S')) >= timedelta(seconds=10)):
                flash('Your password reset request has been approved or automatically activated. Please set your new password.', 'info')
                return redirect(url_for('reset_password_route', username=user_obj.username, unique_key=user_obj.unique_key))
            return redirect(url_for('dashboard'))

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    db = get_db()
    # Update last_seen_at on logout
    db.execute('UPDATE users SET last_seen_at = ? WHERE id = ?',
               (datetime.utcnow(), current_user.id))
    db.commit()
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated: # Prevent logged-in users from initiating reset for others directly
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        unique_key = request.form['unique_key'].strip()

        db = get_db()
        user = db.execute('SELECT id, username FROM users WHERE username = ? AND unique_key = ?', (username, unique_key)).fetchone()

        if user:
            # Set password_reset_pending to 1 and record timestamp
            db.execute('UPDATE users SET password_reset_pending = 1, reset_request_timestamp = ?, admin_reset_approved = 0 WHERE id = ?', # Reset admin_reset_approved to 0
                       (datetime.utcnow(), user['id']))
            db.commit()
            flash(f'Password reset initiated for {user["username"]}. An admin will review it, or it will be automatically approved in 10 seconds. You can now login with your old password to set a new one.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Invalid username or unique key.', 'danger')
    return render_template('forgot_password.html')


@app.route('/reset_password', methods=['GET', 'POST']) # Renamed route from set_new_password
def reset_password_route():
    # This route no longer requires @login_required. It validates access via query params/session.
    username_param = request.args.get('username')
    unique_key_param = request.args.get('unique_key') # This is for initial access from login redirect

    db = get_db()
    user = None

    # Try to get user from current_user if logged in (e.g., after initial login redirect)
    if current_user.is_authenticated:
        user = db.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()
    # If not logged in, try to get user from query parameters (for direct access from forgotten password flow)
    elif username_param and unique_key_param:
        user = db.execute('SELECT * FROM users WHERE username = ? AND unique_key = ?', (username_param, unique_key_param)).fetchone()

    if not user:
        flash("Unauthorized access to password reset. Please use the 'Forgot Password' link.", "danger")
        return redirect(url_for('forgot_password'))

    # Parse timestamp safely
    reset_timestamp = user['reset_request_timestamp']
    if reset_timestamp:
        try:
            reset_timestamp = datetime.strptime(str(reset_timestamp), '%Y-%m-%d %H:%M:%S.%f' if '.' in str(reset_timestamp) else '%Y-%m-%d %H:%M:%S')
        except ValueError:
            reset_timestamp = datetime.min # Fallback for old/malformed timestamps
    else:
        reset_timestamp = datetime.min # No timestamp means not initiated

    # Check reset conditions:
    # 1. password_reset_pending must be 1
    # 2. Either admin_reset_approved is 1 OR 10 seconds have passed since reset_request_timestamp
    can_reset = False
    if user['password_reset_pending'] == 1:
        if user['admin_reset_approved'] == 1:
            can_reset = True
        elif (datetime.utcnow() - reset_timestamp) >= timedelta(seconds=10):
            can_reset = True # Auto-approve after 10 seconds

    if not can_reset:
        flash("Password reset is not yet approved by an admin or the automatic approval time has not passed. Please wait.", "danger")
        # If user is logged in and trying to access, redirect them to profile
        if current_user.is_authenticated and current_user.id == user['id']:
            return redirect(url_for('my_profile'))
        # Otherwise, redirect to forgot password
        return redirect(url_for('forgot_password'))


    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_new_password = request.form['confirm_new_password']

        if new_password != confirm_new_password:
            flash('New password and confirmation do not match.', 'danger')
            return render_template('reset_password.html')
        elif len(new_password) < 6:
            flash('New password must be at least 6 characters long.', 'danger')
            return render_template('reset_password.html')
        else:
            hashed_password = generate_password_hash(new_password)
            # Reset all flags after successful reset
            db.execute('UPDATE users SET password_hash = ?, password_reset_pending = 0, reset_request_timestamp = NULL, admin_reset_approved = 0 WHERE id = ?',
                       (hashed_password, user['id']))
            db.commit()
            flash('Your password has been changed successfully! You can now log in with your new password.', 'success')

            # Log out the user if they were logged in during the reset,
            # so they have to log in with the new password.
            if current_user.is_authenticated and current_user.id == user['id']:
                logout_user()
            return redirect(url_for('login'))

    return render_template('reset_password.html')


# Middleware to update last_seen_at for authenticated users
@app.before_request
def update_last_seen():
    if current_user.is_authenticated:
        db = get_db()
        db.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', (datetime.utcnow(), current_user.id))
        db.commit()

# --- Admin Panel & User Management ---

@app.route('/admin/manage_users', methods=['GET', 'POST'])
@login_required
def admin_manage_users():
    if not current_user.is_admin:
        flash("Unauthorized access. Admins only.", "danger")
        return redirect(url_for('dashboard'))

    db = get_db()

    # Fetch users, excluding the super admin and AdminAI
    users = db.execute(
        "SELECT id, username, originalName, unique_key, is_admin, password_reset_pending, reset_request_timestamp, admin_reset_approved FROM users WHERE username != ? AND username != 'AdminAI' ORDER BY username ASC",
        (config.ADMIN_USERNAME,)
    ).fetchall()

    # Process timestamps for display
    users_for_template = []
    for user in users:
        user_dict = dict(user)
        if user_dict['reset_request_timestamp']:
            try:
                user_dict['reset_request_timestamp'] = datetime.strptime(user_dict['reset_request_timestamp'], '%Y-%m-%d %H:%M:%S.%f' if '.' in user_dict['reset_request_timestamp'] else '%Y-%m-%d %H:%M:%S')
            except ValueError:
                user_dict['reset_request_timestamp'] = None # Invalid format
        users_for_template.append(user_dict)


    # Fetch member profiles
    members = db.execute(
        'SELECT m.*, u.username FROM members m LEFT JOIN users u ON m.user_id = u.id ORDER BY m.fullName ASC'
    ).fetchall()

    members_with_status = []
    for member_row in members:
        member_dict = dict(member_row)
        temp_status = db.execute(
            'SELECT id, file_path, upload_time, is_video FROM statuses WHERE member_id = ? ORDER BY upload_time DESC LIMIT 1',
            (member_dict['id'],)
        ).fetchone()

        if temp_status:
            upload_time_dt = datetime.strptime(temp_status['upload_time'], '%Y-%m-%d %H:%M:%S.%f')
            if (datetime.utcnow() - upload_time_dt) < timedelta(hours=12): # Status active for 12 hours
                member_dict['status_id'] = temp_status['id']
                # Determine correct URL path for media
                if temp_status['is_video']:
                    member_dict['status_file_path'] = url_for('uploaded_video', filename=os.path.basename(temp_status['file_path']))
                else:
                    member_dict['status_file_path'] = url_for('uploaded_file', filename=os.path.basename(temp_status['file_path']))
                member_dict['status_is_video'] = temp_status['is_video']
                member_dict['status_expires_at'] = upload_time_dt + timedelta(hours=12)
            else:
                member_dict['status_file_path'] = None # Status expired
        else:
            member_dict['status_file_path'] = None # No status

        members_with_status.append(member_dict)

    return render_template('admin_manage_users.html', users=users_for_template, members_with_status=members_with_status)


@app.route('/admin/initiate_reset/<int:user_id>', methods=['POST'])
@login_required
def admin_initiate_reset(user_id):
    if not current_user.is_admin:
        flash("Unauthorized access.", "danger")
        return redirect(url_for('dashboard'))

    db = get_db()
    user = db.execute('SELECT id, username FROM users WHERE id = ?', (user_id,)).fetchone()

    if not user:
        flash("User not found.", "danger")
    elif user['username'] == config.ADMIN_USERNAME or user['username'] == 'AdminAI':
        flash("Cannot initiate password reset for this special account.", "danger")
    else:
        # Set password_reset_pending and admin_reset_approved, update timestamp
        db.execute('UPDATE users SET password_reset_pending = 1, reset_request_timestamp = ?, admin_reset_approved = 1 WHERE id = ?',
                   (datetime.utcnow(), user_id))
        db.commit()
        flash(f'Password reset initiated for {user["username"]}. User can now proceed to reset their password.', 'success')

    return redirect(url_for('admin_manage_users'))

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if not current_user.is_admin:
        flash("Unauthorized access.", "danger")
        return redirect(url_for('dashboard'))

    if user_id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for('admin_manage_users'))

    db = get_db()
    user = db.execute('SELECT username FROM users WHERE id = ?', (user_id,)).fetchone()

    if not user:
        flash("User not found.", "danger")
    elif user['username'] == config.ADMIN_USERNAME or user['username'] == 'AdminAI':
        flash("Cannot delete this special account.", "danger")
    else:
        try:
            # Delete associated member profile first (due to foreign key constraint)
            member = db.execute('SELECT id, profilePhoto FROM members WHERE user_id = ?', (user_id,)).fetchone()
            if member:
                # Delete profile photo if exists
                if member['profilePhoto']:
                    photo_path = os.path.join(app.config['UPLOAD_FOLDER'], member['profilePhoto'])
                    if os.path.exists(photo_path):
                        os.remove(photo_path)
                db.execute('DELETE FROM members WHERE user_id = ?', (user_id,))

            # Delete any statuses uploaded by this user (if not tied to a member_id which would be CASCADE deleted)
            db.execute('DELETE FROM statuses WHERE uploader_user_id = ?', (user_id,))
            
            # Delete user itself
            db.execute('DELETE FROM users WHERE id = ?', (user_id,))
            db.commit()
            flash(f'User {user["username"]} and associated profile have been deleted.', 'success')
        except sqlite3.Error as e:
            flash(f"Database error during user deletion: {e}", 'danger')

    return redirect(url_for('admin_manage_users'))


@app.route('/admin/toggle_messaging/<int:member_id>', methods=['POST'])
@login_required
def admin_toggle_messaging(member_id):
    if not current_user.is_admin:
        flash("Unauthorized access. Admins only.", "danger")
        return redirect(url_for('dashboard'))

    db = get_db()
    member = db.execute('SELECT id, fullName, can_message FROM members WHERE id = ?', (member_id,)).fetchone()

    if not member:
        flash("Member profile not found.", "danger")
    else:
        new_can_message_status = 0 if member['can_message'] == 1 else 1
        db.execute('UPDATE members SET can_message = ? WHERE id = ?', (new_can_message_status, member_id))
        db.commit()
        status_text = "enabled" if new_can_message_status == 1 else "disabled"
        flash(f'Messaging for {member["fullName"]} has been {status_text}.', 'success')

    return redirect(url_for('admin_manage_users'))


# --- Dashboard & Member Management (Routes from previous updates) ---

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    # Fetch members added by the current user
    my_members = db.execute(
        'SELECT id, fullName, profilePhoto, dateOfBirth FROM members WHERE added_by_user_id = ? ORDER BY fullName ASC',
        (current_user.id,)
    ).fetchall()

    # Calculate age for each member and store in a list of dicts
    members_with_age = []
    for member in my_members:
        member_dict = dict(member)
        if member_dict['dateOfBirth']:
            dob = datetime.strptime(member_dict['dateOfBirth'], '%Y-%m-%d').date()
            today = datetime.now().date()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            member_dict['age'] = age
        else:
            member_dict['age'] = 'N/A'

        # Ensure profilePhoto path is correct for display
        if member_dict['profilePhoto']:
            member_dict['profilePhotoUrl'] = url_for('uploaded_file', filename=os.path.basename(member_dict['profilePhoto']))
        else:
            member_dict['profilePhotoUrl'] = url_for('static', filename='img/default_profile.png')

        members_with_age.append(member_dict)

    # Fetch total number of users (registered accounts)
    total_users_count = db.execute('SELECT COUNT(id) FROM users').fetchone()[0]
    # Fetch total number of members (profiles, including those without accounts)
    total_members_count = db.execute('SELECT COUNT(id) FROM members').fetchone()[0]

    return render_template('dashboard.html', my_members=members_with_age,
                           total_users_count=total_users_count,
                           total_members_count=total_members_count)

@app.route('/add_member', methods=['GET'])
@login_required # Route for admin to add new members (GET)
def add_member_form():
    if not current_user.is_admin:
        flash('Only admins can add new members.', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('add-member.html', form_data={}) # Pass empty form_data initially

@app.route('/add_member', methods=['POST'])
@login_required # Route for admin to add new members (POST)
def add_member():
    if not current_user.is_admin:
        flash('Only admins can add new members.', 'danger')
        return redirect(url_for('dashboard'))

    fullName = request.form['fullName'].strip()
    dateOfBirth = request.form['dateOfBirth']
    gender = request.form['gender']
    maritalStatus = request.form.get('maritalStatus') # Optional, will be None if not sent
    spouseNames = request.form.get('spouseNames', '').strip() # Optional, default to empty string
    fianceNames = request.form.get('fianceNames', '').strip() # Optional, default to empty string
    childrenNames = request.form.get('childrenNames', '').strip() # Optional, default to empty string
    educationLevel = request.form.get('educationLevel', 'None') # Optional, default 'None'
    schoolName = request.form.get('schoolName', '').strip() # Optional, default to empty string
    whereabouts = request.form['whereabouts'].strip()
    # Contact info fields
    phoneNumber = request.form.get('phoneNumber', '').strip()
    emailContact = request.form.get('emailContact', '').strip()
    otherContact = request.form.get('otherContact', '').strip()
    bio = request.form.get('bio', '').strip()

    profilePhoto_file = request.files.get('profilePhoto')

    db = get_db()
    error = None

    if not fullName or not dateOfBirth or not gender or not whereabouts:
        error = 'Please fill in all required fields: Full Name, Date of Birth, Gender, and Current Whereabouts.'

    if error is None:
        profile_photo_filename = None
        if profilePhoto_file and profilePhoto_file.filename:
            filename = secure_filename(profilePhoto_file.filename)
            profile_photo_filename = f"{uuid.uuid4()}_{filename}" # Unique filename
            profilePhoto_file.save(os.path.join(app.config['UPLOAD_FOLDER'], profile_photo_filename))

        try:
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
            # If error, re-render form with previous data
            form_data = request.form.to_dict()
            return render_template('add-member.html', form_data=form_data)
    else:
        flash(error, 'danger')
        form_data = request.form.to_dict()
        return render_template('add-member.html', form_data=form_data)


@app.route('/my_profile', methods=['GET'])
@login_required
def my_profile():
    db = get_db()
    member = db.execute(
        'SELECT m.*, u.username, u.unique_key FROM members m JOIN users u ON m.user_id = u.id WHERE m.user_id = ?', # Added u.unique_key
        (current_user.id,)
    ).fetchone()

    if not member:
        flash('Your member profile details are not yet added. Please add them.', 'info')
        return redirect(url_for('add_my_details'))

    # Prepare member data for template, including splitting comma-separated fields
    member_dict = dict(member)
    member_dict['spouseNames'] = [s.strip() for s in member_dict['spouseNames'].split(',')] if member_dict['spouseNames'] else [] # Stripping spaces
    member_dict['fianceNames'] = [s.strip() for s in member_dict['fianceNames'].split(',')] if member_dict['fianceNames'] else [] # Stripping spaces
    member_dict['childrenNames'] = [s.strip() for s in member_dict['childrenNames'].split(',')] if member_dict['childrenNames'] else [] # Stripping spaces

    # Check for active status video/photo
    temp_video = db.execute(
        'SELECT file_path, upload_time, is_video FROM statuses WHERE member_id = ? ORDER BY upload_time DESC LIMIT 1',
        (member['id'],)
    ).fetchone()

    active_status = None
    if temp_video:
        upload_time_dt = datetime.strptime(temp_video['upload_time'], '%Y-%m-%d %H:%M:%S.%f')
        # Status active for 12 hours
        if (datetime.utcnow() - upload_time_dt) < timedelta(hours=12):
            active_status = dict(temp_video)
            active_status['expires_at'] = upload_time_dt + timedelta(hours=12)

    return render_template('my_profile.html', member=member_dict, temp_video=active_status, form_data={})

@app.route('/member/<int:member_id>')
@login_required
def member_detail(member_id):
    db = get_db()
    member = db.execute(
        'SELECT m.*, u.username FROM members m LEFT JOIN users u ON m.user_id = u.id WHERE m.id = ?',
        (member_id,)
    ).fetchone()

    if not member:
        abort(404) # Member not found

    # Prepare member data for template
    member_dict = dict(member)
    member_dict['spouseNames'] = [s.strip() for s in member_dict['spouseNames'].split(',')] if member_dict['spouseNames'] else []
    member_dict['fianceNames'] = [s.strip() for s in member_dict['fianceNames'].split(',')] if member_dict['fianceNames'] else []
    member_dict['childrenNames'] = [s.strip() for s in member_dict['childrenNames'].split(',')] if member_dict['childrenNames'] else []


    # Check for active status video/photo
    temp_video = db.execute(
        'SELECT file_path, upload_time, is_video FROM statuses WHERE member_id = ? ORDER BY upload_time DESC LIMIT 1',
        (member['id'],)
    ).fetchone()

    active_status = None
    if temp_video:
        upload_time_dt = datetime.strptime(temp_video['upload_time'], '%Y-%m-%d %H:%M:%S.%f')
        if (datetime.utcnow() - upload_time_dt) < timedelta(hours=12):
            active_status = dict(temp_video)
            active_status['expires_at'] = upload_time_dt + timedelta(hours=12)

    return render_template('member_detail.html', member=member_dict, temp_video=active_status)


@app.route('/add_my_details', methods=['GET', 'POST'])
@login_required
def add_my_details():
    db = get_db()
    # Check if a member profile already exists for the current user
    user_member_profile = db.execute('SELECT * FROM members WHERE user_id = ?', (current_user.id,)).fetchone()

    is_editing = False
    user_details = {} # This will hold data to pre-populate form fields

    if user_member_profile:
        is_editing = True
        user_details = dict(user_member_profile)
        # Handle comma-separated fields for pre-population in template
        user_details['spouse_names'] = user_details['spouseNames']
        user_details['fiance_names'] = user_details['fianceNames']
        user_details['children_names'] = user_details['childrenNames']
        user_details['education_level'] = user_details['educationLevel']
        user_details['institution_name'] = user_details['schoolName'] # Map to new name
        user_details['phone_number'] = user_details['phoneNumber']
        user_details['email_contact'] = user_details['emailContact']
        user_details['other_contact'] = user_details['otherContact']
        user_details['biography'] = user_details['bio'] # Map to new name
        user_details['profile_photo'] = user_details['profilePhoto']

    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        date_of_birth = request.form.get('date_of_birth')
        gender = request.form.get('gender')
        marital_status = request.form.get('marital_status') # Now optional
        spouse_names = request.form.get('spouse_names', '').strip() # Optional
        fiance_names = request.form.get('fiance_names', '').strip() # Optional
        children_names = request.form.get('children_names', '').strip() # Optional
        education_level = request.form.get('education_level', 'None') # Optional
        institution_name = request.form.get('institution_name', '').strip() # Optional
        whereabouts = request.form.get('whereabouts', '').strip()
        phone_number = request.form.get('phone_number', '').strip()
        email_contact = request.form.get('email_contact', '').strip()
        other_contact = request.form.get('other_contact', '').strip()
        biography = request.form.get('biography', '').strip()
        profile_photo_file = request.files.get('profile_photo')

        error = None
        if not full_name or not gender:
            error = 'Full Name and Gender are required.'

        # Additional validation for marital status fields if selected
        if marital_status == 'Married' and not spouse_names:
            pass # Spouse names can be empty
        elif marital_status == 'Engaged' and not fiance_names:
            pass # Fiance names can be empty


        if error is None:
            profile_photo_filename = user_details.get('profilePhoto') # Keep existing if not uploaded new
            if profile_photo_file and profile_photo_file.filename:
                filename = secure_filename(profile_photo_file.filename)
                profile_photo_filename = f"{uuid.uuid4()}_{filename}"
                profile_photo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], profile_photo_filename))

            try:
                if is_editing:
                    db.execute(
                        '''UPDATE members SET
                           fullName = ?, gender = ?, dateOfBirth = ?, maritalStatus = ?,
                           spouseNames = ?, fianceNames = ?, childrenNames = ?,
                           educationLevel = ?, schoolName = ?, whereabouts = ?,
                           phoneNumber = ?, emailContact = ?, otherContact = ?, bio = ?, profilePhoto = ?
                           WHERE user_id = ?''',
                        (full_name, gender, date_of_birth, marital_status,
                         spouse_names, fiance_names, children_names,
                         education_level, institution_name, whereabouts,
                         phoneNumber, email_contact, other_contact, biography, profile_photo_filename,
                         current_user.id)
                    )
                    flash('Your details have been updated successfully!', 'success')
                else:
                    db.execute(
                        '''INSERT INTO members (fullName, gender, dateOfBirth, maritalStatus, spouseNames, fianceNames, childrenNames, educationLevel, schoolName, whereabouts, phoneNumber, emailContact, otherContact, bio, profilePhoto, user_id, added_by_user_id, can_message)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (full_name, gender, date_of_birth, marital_status,
                         spouse_names, fiance_names, children_names,
                         education_level, institution_name, whereabouts,
                         phoneNumber, email_contact, other_contact, biography, profile_photo_filename,
                         current_user.id, current_user.id, 1) # User's own member profile can message
                    )
                    flash('Your personal details have been added successfully!', 'success')
                db.commit()
                return redirect(url_for('my_profile'))
            except sqlite3.Error as e:
                error = f"Database error: {e}"
                flash(error, 'danger')
        else:
            flash(error, 'danger')

        # If there's an error, repopulate user_details for rendering (used as form_data in template)
        user_details = {
            'full_name': full_name,
            'date_of_birth': date_of_birth,
            'gender': gender,
            'marital_status': marital_status,
            'spouse_names': spouse_names,
            'fiance_names': fiance_names,
            'children_names': children_names,
            'education_level': education_level,
            'institution_name': institution_name,
            'whereabouts': whereabouts,
            'phone_number': phoneNumber,
            'email_contact': email_contact,
            'other_contact': other_contact,
            'biography': biography,
            'profile_photo': profile_photo_filename # Pass back the existing/new photo if there was a text error
        }

    return render_template('add_my_details.html', is_editing=is_editing, user_details=user_details)

@app.route('/edit_member/<int:member_id>', methods=['GET', 'POST'])
@login_required
def edit_member(member_id):
    db = get_db()
    member = db.execute(
        'SELECT * FROM members WHERE id = ?', (member_id,)
    ).fetchone()

    if not member:
        abort(404) # Member not found

    # Check authorization: current user must be admin or the linked user of this member profile
    if not current_user.is_admin and (member['user_id'] is None or current_user.id != member['user_id']):
        flash("You are not authorized to edit this member's profile.", "danger")
        return redirect(url_for('dashboard'))

    form_data = {} # To hold data from POST if validation fails

    if request.method == 'POST':
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
        profilePhoto_file = request.files.get('profilePhoto')
        remove_profile_photo = request.form.get('remove_profile_photo') # Checkbox for removing photo
        can_message = request.form.get('can_message') == '1' # For admin to toggle messaging

        error = None
        if not fullName or not dateOfBirth or not gender or not whereabouts:
            error = 'Please fill in all required fields: Full Name, Date of Birth, Gender, and Current Whereabouts.'

        if error is None:
            profile_photo_filename = member['profilePhoto'] # Keep existing by default
            if remove_profile_photo:
                # Delete old file if it exists
                if profile_photo_filename and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], profile_photo_filename)):
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], profile_photo_filename))
                profile_photo_filename = None # Set to None after removal
            elif profilePhoto_file and profilePhoto_file.filename:
                # New photo uploaded, delete old one if exists
                if profile_photo_filename and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], profile_photo_filename)):
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], profile_photo_filename))
                filename = secure_filename(profilePhoto_file.filename)
                profile_photo_filename = f"{uuid.uuid4()}_{filename}"
                profilePhoto_file.save(os.path.join(app.config['UPLOAD_FOLDER'], profile_photo_filename))

            try:
                db.execute(
                    '''UPDATE members SET
                       fullName = ?, gender = ?, dateOfBirth = ?, maritalStatus = ?,
                       spouseNames = ?, fianceNames = ?, childrenNames = ?,
                       educationLevel = ?, schoolName = ?, whereabouts = ?,
                       phoneNumber = ?, emailContact = ?, otherContact = ?, bio = ?, profilePhoto = ?, can_message = ?
                       WHERE id = ?''',
                    (fullName, gender, dateOfBirth, maritalStatus,
                     spouseNames, fianceNames, childrenNames,
                     educationLevel, schoolName, whereabouts,
                     phoneNumber, emailContact, otherContact, bio, profile_photo_filename,
                     can_message, member_id)
                )
                db.commit()
                flash(f'{fullName} updated successfully!', 'success')
                return redirect(url_for('member_detail', member_id=member_id))
            except sqlite3.Error as e:
                error = f"Database error: {e}"
                flash(error, 'danger')
        else:
            flash(error, 'danger')

        # If error, repopulate form_data with submitted data
        form_data = request.form.to_dict()
        form_data['profilePhoto'] = profile_photo_filename # Retain photo path for re-render if text error

    # For GET request or POST with error, fetch member data to pre-fill form
    member_dict = dict(member)
    # Apply form_data to override if there was a POST error
    member_dict.update(form_data) # This ensures submitted data is shown even on error

    return render_template('edit_member.html', member=member_dict, form_data=form_data)


@app.route('/delete_member/<int:member_id>', methods=['POST'])
@login_required
def delete_member(member_id):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()

    if not member:
        flash("Member not found.", "danger")
        return redirect(url_for('dashboard'))

    # Authorization: only admin or the user linked to this member profile can delete
    if not current_user.is_admin and (member['user_id'] is None or current_user.id != member['user_id']):
        flash("You are not authorized to delete this member's profile.", "danger")
        return redirect(url_for('dashboard'))

    try:
        # Delete associated profile photo if it exists
        if member['profilePhoto']:
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], member['profilePhoto'])
            if os.path.exists(photo_path):
                os.remove(photo_path)

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
            return render_template('upload_status.html')

        filename = secure_filename(status_file.filename)
        file_extension = os.path.splitext(filename)[1].lower()
        is_video = 0
        upload_folder_path = app.config['UPLOAD_FOLDER'] # Default to image folder

        if file_extension in ['.mp4', '.mov', '.avi', '.wmv', '.flv']:
            is_video = 1
            upload_folder_path = app.config['UPLOAD_VIDEO_FOLDER']
        elif file_extension in ['.jpg', '.jpeg', '.png', '.gif']:
            is_video = 0
            upload_folder_path = app.config['UPLOAD_FOLDER']
        else:
            flash('Unsupported file type. Please upload an image or video.', 'danger')
            return render_template('upload_status.html')

        unique_filename = f"{uuid.uuid4()}_{filename}"
        file_path = os.path.join(upload_folder_path, unique_filename)
        status_file.save(file_path)

        # Relative path for DB storage
        db_file_path = os.path.relpath(file_path, app.root_path)
        db_file_path = db_file_path.replace('\\', '/') # Ensure forward slashes for URL

        # Delete any existing status for this member
        db.execute('DELETE FROM statuses WHERE member_id = ?', (member_id,))

        db.execute(
            'INSERT INTO statuses (member_id, file_path, upload_time, is_video, uploader_user_id) VALUES (?, ?, ?, ?, ?)',
            (member_id, db_file_path, datetime.utcnow(), is_video, current_user.id)
        )
        db.commit()
        flash('Status uploaded successfully!', 'success')
        return redirect(url_for('my_profile'))

    return render_template('upload_status.html')

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

    # Ensure only the uploader or an admin can delete the status
    if not current_user.is_admin and current_user.id != status_entry['uploader_user_id']:
        flash("You are not authorized to delete this status.", "danger")
        return redirect(url_for('my_profile'))

    try:
        # Determine correct folder based on is_video flag
        upload_folder = app.config['UPLOAD_VIDEO_FOLDER'] if status_entry['is_video'] else app.config['UPLOAD_FOLDER']
        # Construct full path to the file
        full_file_path = os.path.join(app.root_path, status_entry['file_path'])

        if os.path.exists(full_file_path):
            os.remove(full_file_path) # Delete the actual file
            # flash(f"File removed: {full_file_path}", "success") # For debugging
        # else:
            # flash(f"File not found on disk, but removing DB entry: {full_file_path}", "warning") # For debugging

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
        upload_folder = app.config['UPLOAD_VIDEO_FOLDER'] if status_entry['is_video'] else app.config['UPLOAD_FOLDER']
        full_file_path = os.path.join(app.root_path, status_entry['file_path'])

        if os.path.exists(full_file_path):
            os.remove(full_file_path)
        
        db.execute('DELETE FROM statuses WHERE id = ?', (status_id,))
        db.commit()
        flash('Status deleted by admin successfully!', 'success')
    except Exception as e:
        flash(f"Error deleting status by admin: {e}", 'danger')

    return redirect(url_for('admin_manage_users'))

@app.route('/uploaded_file/<filename>')
def uploaded_file(filename):
    # This route serves profile photos (from UPLOAD_FOLDER) and potentially status images
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/uploaded_video/<filename>')
def uploaded_video(filename):
    # This route serves status videos (from UPLOAD_VIDEO_FOLDER)
    return send_from_directory(app.config['UPLOAD_VIDEO_FOLDER'], filename)

# --- Chat Functionality (Existing) ---

# Mock function for getting chat conversations (replace with actual DB calls later)
def get_user_conversations(user_id):
    db = get_db()
    # Find all chat rooms where the user is a member
    user_rooms = db.execute('SELECT chat_room_id FROM chat_room_members WHERE user_id = ?', (user_id,)).fetchall()
    user_room_ids = [room['chat_room_id'] for room in user_rooms]

    conversations = []
    for room_id in user_room_ids:
        chat_room = db.execute('SELECT * FROM chat_rooms WHERE id = ?', (room_id,)).fetchone()
        if not chat_room:
            continue

        latest_message = db.execute(
            'SELECT sender_id, content, timestamp FROM chat_messages WHERE chat_room_id = ? ORDER BY timestamp DESC LIMIT 1',
            (room_id,)
        ).fetchone()

        # Determine the "other user" for 1-to-1 chats (if not a group or AI chat)
        other_user = None
        if not chat_room['is_group_chat']:
            members_in_room = db.execute('SELECT user_id FROM chat_room_members WHERE chat_room_id = ? AND user_id != ?', (room_id, user_id)).fetchone()
            if members_in_room:
                other_user_id = members_in_room['user_id']
                other_user_data = db.execute('SELECT id, username, originalName FROM users WHERE id = ?', (other_user_id,)).fetchone()
                if other_user_data:
                    other_user = dict(other_user_data)
            # Handle AI chat
            elif chat_room['name'] == 'AdminAI':
                other_user = {'id': -1, 'username': 'AdminAI', 'originalName': 'AdminAI'} # Use -1 for AI sender_id


        if other_user: # Only include if there's a clear 'other user' or it's AdminAI
            snippet = "No messages yet."
            is_unread = False
            message_timestamp = datetime.min # Default to min datetime

            if latest_message:
                snippet = latest_message['content'] if latest_message['content'] else "Media message"
                message_timestamp = datetime.strptime(latest_message['timestamp'], '%Y-%m-%d %H:%M:%S.%f')
                # Check unread status if the latest message is from the other user and not read
                if latest_message['sender_id'] != user_id:
                    is_unread = True

            conversations.append({
                'chat_room_id': room_id,
                'chat_room_name': chat_room['name'],
                'other_user': other_user,
                'latest_message_snippet': snippet,
                'timestamp': message_timestamp,
                'is_unread': is_unread
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

@app.route('/chat/<int:chat_room_id>', methods=['GET'])
@login_required
def chat_messages(chat_room_id):
    db = get_db()
    # Verify user is a member of this chat room
    member_check = db.execute(
        'SELECT * FROM chat_room_members WHERE chat_room_id = ? AND user_id = ?',
        (chat_room_id, current_user.id)
    ).fetchone()

    if not member_check:
        flash("You are not a member of this chat room.", "danger")
        return redirect(url_for('inbox'))

    chat_room = db.execute('SELECT * FROM chat_rooms WHERE id = ?', (chat_room_id,)).fetchone()
    if not chat_room:
        flash("Chat room not found.", "danger")
        return redirect(url_for('inbox'))

    messages = db.execute(
        '''SELECT cm.*, u.username AS sender_username, u.originalName AS sender_originalName
           FROM chat_messages cm
           LEFT JOIN users u ON cm.sender_id = u.id -- Use LEFT JOIN for AI messages where sender_id might not exist in users
           WHERE cm.chat_room_id = ? ORDER BY cm.timestamp ASC''',
        (chat_room_id,)
    ).fetchall()

    messages_for_template = []
    for msg in messages:
        msg_dict = dict(msg)
        # Parse timestamp for Flask-Moment
        msg_dict['timestamp_dt'] = datetime.strptime(msg_dict['timestamp'], '%Y-%m-%d %H:%M:%S.%f')

        # Handle AI sender_info
        if msg_dict['is_ai_message'] == 1:
            msg_dict['sender_username'] = 'AdminAI'
            msg_dict['sender_originalName'] = 'AdminAI'
        messages_for_template.append(msg_dict)


    # Determine recipient for 1-to-1 chat for display purposes
    recipient_user = None
    if not chat_room['is_group_chat']:
        other_member = db.execute('SELECT user_id FROM chat_room_members WHERE chat_room_id = ? AND user_id != ?', (chat_room_id, current_user.id)).fetchone()
        if other_member:
            recipient_user = db.execute('SELECT id, username, originalName FROM users WHERE id = ?', (other_member['user_id'],)).fetchone()
        elif chat_room['name'] == 'AdminAI':
            recipient_user = {'id': -1, 'username': 'AdminAI', 'originalName': 'AdminAI'}

    # Get chat background image for current user
    chat_background_image_path = current_user.chat_background_image_path
    if chat_background_image_path:
        chat_background_image_path = url_for('uploaded_chat_background', filename=os.path.basename(chat_background_image_path))


    return render_template('chat_room.html',
                           chat_room=chat_room,
                           messages=messages_for_template,
                           current_user_id=current_user.id,
                           recipient_user=recipient_user,
                           chat_background_image_path=chat_background_image_path)

@app.route('/chat_messages/<int:recipient_id>', methods=['GET'])
@login_required
def chat_messages_direct(recipient_id):
    db = get_db()

    # Find or create a direct chat room between current_user and recipient_id
    # Logic for finding existing 1-to-1 chat (excluding AdminAI)
    chat_room = None
    if recipient_id != -1: # -1 indicates AdminAI
        # Check for a chat room that contains only these two users
        existing_rooms = db.execute('''
            SELECT cr.id
            FROM chat_rooms cr
            JOIN chat_room_members crm1 ON cr.id = crm1.chat_room_id
            JOIN chat_room_members crm2 ON cr.id = crm2.chat_room_id
            WHERE cr.is_group_chat = 0
              AND crm1.user_id = ?
              AND crm2.user_id = ?
            GROUP BY cr.id
            HAVING COUNT(DISTINCT crm1.user_id, crm2.user_id) = 2
        ''', (current_user.id, recipient_id)).fetchone()

        if existing_rooms:
            chat_room = db.execute('SELECT * FROM chat_rooms WHERE id = ?', (existing_rooms['id'],)).fetchone()
        else:
            # Create new direct chat room
            recipient_username = db.execute("SELECT username FROM users WHERE id = ?", (recipient_id,)).fetchone()["username"]
            db.execute('INSERT INTO chat_rooms (name, is_group_chat, created_at) VALUES (?, ?, ?)',
                       (f'Chat with {recipient_username}', 0, datetime.utcnow()))
            db.commit()
            new_room_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute('INSERT INTO chat_room_members (chat_room_id, user_id) VALUES (?, ?)', (new_room_id, current_user.id))
            db.execute('INSERT INTO chat_room_members (chat_room_id, user_id) VALUES (?, ?)', (new_room_id, recipient_id))
            db.commit()
            chat_room = db.execute('SELECT * FROM chat_rooms WHERE id = ?', (new_room_id,)).fetchone()
    elif recipient_id == -1: # Special case for AdminAI chat
        admin_ai_room = db.execute("SELECT * FROM chat_rooms WHERE name = 'AdminAI'").fetchone()
        if not admin_ai_room:
            # Create AdminAI chat room
            db.execute('INSERT INTO chat_rooms (name, is_group_chat, created_at) VALUES (?, ?, ?)', ('AdminAI', 0, datetime.utcnow()))
            db.commit()
            admin_ai_room_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute('INSERT INTO chat_room_members (chat_room_id, user_id) VALUES (?, ?)', (admin_ai_room_id, current_user.id))
            db.commit()
            chat_room = db.execute('SELECT * FROM chat_rooms WHERE id = ?', (admin_ai_room_id,)).fetchone()
        else:
            # Ensure current user is a member of AdminAI room
            member_exists = db.execute('SELECT 1 FROM chat_room_members WHERE chat_room_id = ? AND user_id = ?', (admin_ai_room['id'], current_user.id)).fetchone()
            if not member_exists:
                db.execute('INSERT INTO chat_room_members (chat_room_id, user_id) VALUES (?, ?)', (admin_ai_room['id'], current_user.id))
                db.commit()
            chat_room = admin_ai_room


    if not chat_room:
        flash("Could not establish chat room.", "danger")
        return redirect(url_for('inbox'))

    return redirect(url_for('chat_messages', chat_room_id=chat_room['id']))

@socketio.on('join_chat_room')
def handle_join_chat_room(data):
    room_id = data['room_id']
    user_id = data['user_id']
    join_room(str(room_id))
    print(f"User {user_id} joined chat room {room_id}")
    # You might want to emit a message to the room that a user joined
    emit('status_message', {'msg': f'User {user_id} has joined the chat.'}, room=str(room_id))

@socketio.on('leave_chat_room')
def handle_leave_chat_room(data):
    room_id = data['room_id']
    user_id = data['user_id']
    leave_room(str(room_id))
    print(f"User {user_id} left chat room {room_id}")
    emit('status_message', {'msg': f'User {user_id} has left the chat.'}, room=str(room_id))

@socketio.on('send_chat_message')
def handle_send_chat_message(data):
    chat_room_id = data['chat_room_id']
    sender_id = data['sender_id']
    content = data.get('content')
    media_path = data.get('media_path')
    media_type = data.get('media_type')
    timestamp = datetime.utcnow()

    db = get_db()
    try:
        db.execute(
            'INSERT INTO chat_messages (chat_room_id, sender_id, content, timestamp, media_path, media_type, is_ai_message) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (chat_room_id, sender_id, content, timestamp, media_path, media_type, 0) # 0 for user messages
        )
        db.commit()

        # Fetch sender username/originalName for display
        sender_info = db.execute('SELECT username, originalName FROM users WHERE id = ?', (sender_id,)).fetchone()
        sender_username = sender_info['username'] if sender_info else 'Unknown User'
        sender_originalName = sender_info['originalName'] if sender_info else 'Unknown User'


        # Prepare message for clients
        message_to_emit = {
            'sender_id': sender_id,
            'sender_username': sender_username,
            'sender_originalName': sender_originalName,
            'content': content,
            'media_path': media_path,
            'media_type': media_type,
            'timestamp': timestamp.isoformat(), # Send ISO format for consistent parsing
            'is_ai_message': 0
        }
        emit('receive_chat_message', message_to_emit, room=str(chat_room_id))

        # If it's the AdminAI room, send to AI model for response
        chat_room_name = db.execute('SELECT name FROM chat_rooms WHERE id = ?', (chat_room_id,)).fetchone()['name']
        if chat_room_name == 'AdminAI' and content:
            # Call AI model
            ai_response_text = generate_ai_response(content) # Implement this function
            if ai_response_text:
                # Store AI message
                ai_timestamp = datetime.utcnow()
                db.execute(
                    'INSERT INTO chat_messages (chat_room_id, sender_id, content, timestamp, is_ai_message) VALUES (?, ?, ?, ?, ?)',
                    (chat_room_id, sender_id, ai_response_text, ai_timestamp, 1) # sender_id here is still the user, but is_ai_message flag marks it as AI response
                )
                db.commit()
                # Emit AI message
                ai_message_to_emit = {
                    'sender_id': -1, # Convention for AI sender_id in frontend
                    'sender_username': 'AdminAI',
                    'sender_originalName': 'AdminAI',
                    'content': ai_response_text,
                    'media_path': None,
                    'media_type': None,
                    'timestamp': ai_timestamp.isoformat(),
                    'is_ai_message': 1
                }
                emit('receive_chat_message', ai_message_to_emit, room=str(chat_room_id))


    except sqlite3.Error as e:
        print(f"Database error saving chat message: {e}")
        emit('chat_error', {'message': 'Failed to send message.'}, room=str(chat_room_id))

# Function to generate AI response (placeholder)
def generate_ai_response(user_message):
    try:
        if not hasattr(config, 'GEMINI_API_KEY') or not config.GEMINI_API_KEY:
            print("GEMINI_API_KEY not found in config.py")
            return "Sorry, AI is not configured. Please contact support."

        genai.configure(api_key=config.GEMINI_API_KEY) # Ensure API key is in config.py
        model = genai.GenerativeModel('gemini-pro') # Using gemini-pro for text

        # You can fetch chat history for context if needed
        # For simplicity, just responding to the latest message
        response = model.generate_content(user_message)
        return response.text
    except Exception as e:
        print(f"Error generating AI response: {e}")
        return "Sorry, I'm having trouble responding right now. (AI error)"


@app.route('/upload_chat_media/<int:chat_room_id>', methods=['POST'])
@login_required
def upload_chat_media(chat_room_id):
    if 'media_file' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('chat_messages', chat_room_id=chat_room_id))

    media_file = request.files['media_file']
    if media_file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('chat_messages', chat_room_id=chat_room_id))

    if media_file:
        filename = secure_filename(media_file.filename)
        file_extension = os.path.splitext(filename)[1].lower()

        media_type = None
        upload_folder = None

        if file_extension in ['.jpg', '.jpeg', '.png', '.gif']:
            media_type = 'image'
            upload_folder = app.config['UPLOAD_CHAT_PHOTO_FOLDER']
        elif file_extension in ['.mp4', '.mov', '.avi', '.wmv', '.flv']:
            media_type = 'video'
            upload_folder = app.config['UPLOAD_CHAT_VIDEO_FOLDER']
        # Add other media types like audio if needed

        if not media_type:
            flash('Unsupported file type.', 'danger')
            return redirect(url_for('chat_messages', chat_room_id=chat_room_id))

        unique_filename = f"{uuid.uuid4()}_{filename}"
        file_path = os.path.join(upload_folder, unique_filename)
        media_file.save(file_path)

        # Store relative path in DB
        db_media_path = os.path.relpath(file_path, app.root_path).replace('\\', '/')

        # Emit message via socket.io
        socketio.emit('send_chat_message', {
            'chat_room_id': chat_room_id,
            'sender_id': current_user.id,
            'content': None, # No text content for media message
            'media_path': db_media_path,
            'media_type': media_type
        }, room=str(chat_room_id)) # Emit directly to the room to avoid double DB insert

        flash('Media uploaded and sent!', 'success')
        return redirect(url_for('chat_messages', chat_room_id=chat_room_id))

    flash('Failed to upload media.', 'danger')
    return redirect(url_for('chat_messages', chat_room_id=chat_room_id))


@app.route('/uploaded_chat_photo/<filename>')
def uploaded_chat_photo(filename):
    return send_from_directory(app.config['UPLOAD_CHAT_PHOTO_FOLDER'], filename)

@app.route('/uploaded_chat_video/<filename>')
def uploaded_chat_video(filename):
    return send_from_directory(app.config['UPLOAD_CHAT_VIDEO_FOLDER'], filename)

@app.route('/uploaded_chat_background/<filename>')
def uploaded_chat_background(filename):
    return send_from_directory(app.config['UPLOAD_CHAT_BACKGROUND_FOLDER'], filename)


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
        current_chat_background = url_for('uploaded_chat_background', filename=os.path.basename(current_chat_background))


    if request.method == 'POST':
        # Handle theme preference update
        new_theme = request.form.get('theme_preference')
        if new_theme in ['light', 'dark']:
            db.execute('UPDATE users SET theme_preference = ? WHERE id = ?', (new_theme, current_user.id))
            db.commit()
            # Update current_user object in session
            current_user.theme_preference = new_theme
            flash('Theme updated successfully!', 'success')

        # Handle chat background upload
        chat_background_file = request.files.get('chat_background_file')
        if chat_background_file and chat_background_file.filename:
            filename = secure_filename(chat_background_file.filename)
            unique_filename = f"{uuid.uuid4()}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_CHAT_BACKGROUND_FOLDER'], unique_filename)

            # Delete old background if exists
            if current_user.chat_background_image_path and os.path.exists(os.path.join(app.root_path, current_user.chat_background_image_path)):
                os.remove(os.path.join(app.root_path, current_user.chat_background_image_path))

            chat_background_file.save(file_path)
            db_file_path = os.path.relpath(file_path, app.root_path).replace('\\', '/')

            db.execute('UPDATE users SET chat_background_image_path = ? WHERE id = ?', (db_file_path, current_user.id))
            db.commit()
            current_user.chat_background_image_path = db_file_path
            flash('Chat background updated successfully!', 'success')

        # Handle removing chat background
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
    return render_template('game_page.html') # This should be your central game selection page

# Route for sending a game invitation
@app.route('/invite_game/<game_name>/<int:recipient_id>', methods=['POST'])
@login_required
def invite_game(game_name, recipient_id):
    db = get_db()
    # Check if recipient exists
    recipient = db.execute('SELECT id, username FROM users WHERE id = ?', (recipient_id,)).fetchone()
    if not recipient:
        flash("Recipient not found.", "danger")
        return redirect(url_for('list_members')) # Redirect back to member list

    # Prevent inviting self
    if current_user.id == recipient_id:
        flash("You cannot invite yourself to a game.", "danger")
        return redirect(url_for('list_members'))

    # Check for existing pending invitation for this game type
    existing_invite = db.execute('''
        SELECT id FROM game_invitations
        WHERE (sender_id = ? AND recipient_id = ?)
           OR (sender_id = ? AND recipient_id = ?)
           AND game_name = ? AND status = 'pending'
    ''', (current_user.id, recipient_id, recipient_id, current_user.id, game_name)).fetchone()

    if existing_invite:
        flash(f"A pending {game_name.capitalize()} game invitation already exists with {recipient['username']}.", "warning")
        return redirect(url_for('list_members'))


    # Create a unique game UUID for this potential multiplayer game
    game_uuid = str(uuid.uuid4())

    db.execute(
        'INSERT INTO game_invitations (sender_id, recipient_id, game_name, status, timestamp, game_uuid) VALUES (?, ?, ?, ?, ?, ?)',
        (current_user.id, recipient_id, game_name, 'pending', datetime.utcnow(), game_uuid)
    )
    db.commit()
    flash(f'Invitation sent to {recipient["username"]} for a {game_name} game!', 'success')

    # For chess, immediately redirect inviter to the game page.
    # The game will then wait for the recipient to join.
    if game_name == 'chess':
        return redirect(url_for('play_game', game_name='chess', gameId=game_uuid))
    else:
        return redirect(url_for('inbox')) # For other games, redirect to inbox or a game hub


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

    # Update invitation status
    db.execute('UPDATE game_invitations SET status = "accepted" WHERE id = ?', (invite['id'],))

    # Initialize multiplayer game entry (only if it's a chess game for now)
    if invite['game_name'] == 'chess':
        # Determine who is white and who is black randomly
        player_white_id = invite['sender_id']
        player_black_id = invite['recipient_id']
        if random.random() < 0.5: # Randomly swap colors
            player_white_id, player_black_id = player_black_id, player_white_id

        # Initial Chess Board State (FEN equivalent for 8x8 array)
        INITIAL_BOARD = [
            ['r', 'n', 'b', 'q', 'k', 'b', 'n', 'r'],
            ['p', 'p', 'p', 'p', 'p', 'p', 'p', 'p'],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            ['P', 'P', 'P', 'P', 'P', 'P', 'P', 'P'],
            ['R', 'N', 'B', 'Q', 'K', 'B', 'N', 'R']
        ]
        initial_board_json = json.dumps(INITIAL_BOARD)
        initial_castling_rights = json.dumps({ 'wK': True, 'wQ': True, 'bK': True, 'bQ': True })

        db.execute(
            '''INSERT INTO multiplayer_games (game_uuid, game_name, player_white_id, player_black_id,
                       current_board_state, current_turn, white_captures, black_captures,
                       castling_rights, en_passant_target, last_move, game_over, winner_id, created_at, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (game_uuid, invite['game_name'], player_white_id, player_black_id,
             initial_board_json, 'w', 0, 0,
             initial_castling_rights, None, None, 0, None, datetime.utcnow(), datetime.utcnow())
        )
        db.commit()
        flash(f'You accepted the {invite["game_name"]} invitation! Game started.', 'success')
        return redirect(url_for('play_game', game_name=invite['game_name'], gameId=game_uuid))
    else:
        flash(f'You accepted the {invite["game_name"]} invitation!', 'success')
        # For other games, redirect to a generic game page or direct play if available
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
    game_uuid = request.args.get('gameId') # Get gameId (now game_uuid) from query parameters

    if game_name == 'chess':
        if game_uuid:
            # This is a multiplayer chess game
            db = get_db()
            game_data = db.execute('SELECT * FROM multiplayer_games WHERE game_uuid = ?', (game_uuid,)).fetchone()
            if not game_data:
                flash("Multiplayer chess game not found.", "danger")
                return redirect(url_for('games_hub'))

            # Convert sqlite3.Row to a regular dictionary
            game_state_for_json = dict(game_data)

            # Convert datetime objects to ISO format strings for JSON serialization
            for key, value in game_state_for_json.items():
                if isinstance(value, datetime):
                    game_state_for_json[key] = value.isoformat()
                elif key in ['current_board_state', 'castling_rights', 'en_passant_target', 'last_move'] and isinstance(value, str):
                    # Ensure JSON strings are parsed back into Python objects for consistent handling
                    try:
                        game_state_for_json[key] = json.loads(value)
                    except json.JSONDecodeError:
                        game_state_for_json[key] = None # Or handle error appropriately

            # Determine player's color based on current_user.id
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
                # Pass full game state as JSON string after converting datetimes and ensuring nested objects are ready
                initial_game_state=json.dumps(game_state_for_json)
            )
        else:
            # This is a solo AI chess game (uses original chess_game.html)
            return render_template('chess_game.html', game_id=None) # game_id is not relevant for solo AI
    elif game_name == 'racing':
        return render_template('racing_game.html', game_name='racing')
    elif game_name == 'board_games':
        return render_template('game_placeholder.html', game_name='board_games')
    else:
        # Fallback for any other undefined game names
        return render_template('game_placeholder.html', game_name=game_name)


@socketio.on('make_move')
def handle_make_move(data):
    game_uuid = data['game_uuid']
    player_id = data['player_id']
    move_data = data['move_data'] # This is the client's move data
    new_game_state = data['new_game_state'] # This is the new state after client's logic

    db = get_db()

    # Verify game exists and player is a participant
    game_row = db.execute('SELECT * FROM multiplayer_games WHERE game_uuid = ?', (game_uuid,)).fetchone()
    if not game_row:
        emit('game_error', {'message': 'Game not found.'}, room=game_uuid)
        return

    # Check if it's the player's turn (IMPORTANT SERVER-SIDE VALIDATION)
    # The current_turn in new_game_state is already the NEXT turn after the client's move.
    # So, we need to check if the `player_id` matches the turn *before* the new_game_state's turn
    # This assumes the client passed the correct turn. A more robust check would involve re-validating the move server-side.
    expected_player_turn = 'w' if new_game_state['current_turn'] == 'b' else 'b' # if new_state turn is black, then white just moved
    if (expected_player_turn == 'w' and str(player_id) != str(game_row['player_white_id'])) or \
       (expected_player_turn == 'b' and str(player_id) != str(game_row['player_black_id'])):
        emit('game_error', {'message': 'It is not your turn.'}, room=game_uuid)
        return

    try:
        # Update multiplayer_games table with the new state from client
        db.execute(
            '''UPDATE multiplayer_games SET
               current_board_state = ?, current_turn = ?, white_captures = ?, black_captures = ?,
               castling_rights = ?, en_passant_target = ?, last_move = ?, game_over = ?, winner_id = ?, last_updated = ?
               WHERE game_uuid = ?''',
            (new_game_state['current_board_state'], # JSON string from client
             new_game_state['current_turn'],
             new_game_state['white_captures'],
             new_game_state['black_captures'],
             new_game_state['castling_rights'], # JSON string from client
             new_game_state['en_passant_target'], # JSON string or null from client
             new_game_state['last_move'], # JSON string or null from client
             new_game_state['game_over'],
             new_game_state['winner_id'], # Use winnerId from client
             datetime.utcnow(),
             game_uuid)
        )

        # Record the move in game_moves history
        move_number_row = db.execute('SELECT COUNT(*) FROM game_moves WHERE game_uuid = ?', (game_uuid,)).fetchone()
        move_number = (move_number_row[0] // 2) + 1 # Fullmove number (e.g., 1 for white's 1st move, 1 for black's 1st move)

        db.execute(
            '''INSERT INTO game_moves (game_uuid, move_number, player_id, move_data, timestamp)
               VALUES (?, ?, ?, ?, ?)''',
            (game_uuid, move_number, player_id, json.dumps(move_data), datetime.utcnow())
        )
        db.commit()

        # Parse JSON strings back to Python objects for the state to be emitted to other clients
        # This is for consistency, as the client expects JSON-parsed objects
        current_board_state_obj = json.loads(new_game_state['current_board_state'])
        castling_rights_obj = json.loads(new_game_state['castling_rights'])
        en_passant_target_obj = json.loads(new_game_state['en_passant_target']) if new_game_state['en_passant_target'] else None
        last_move_obj = json.loads(new_game_state['last_move']) if new_game_state['last_move'] else None

        # Emit the new game state to all clients in the room (including the sender)
        emit('game_state_update', {
            'current_board_state': current_board_state_obj,
            'current_turn': new_game_state['current_turn'],
            'white_captures': new_game_state['white_captures'],
            'black_captures': new_game_state['black_captures'],
            'castling_rights': castling_rights_obj,
            'en_passant_target': en_passant_target_obj,
            'last_move': last_move_obj,
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
        # Convert sqlite3.Row to a regular dictionary
        game_state_for_json = dict(game_data)

        # Parse JSON strings back into Python objects
        for key in ['current_board_state', 'castling_rights', 'en_passant_target', 'last_move']:
            if key in game_state_for_json and game_state_for_json[key] is not None:
                try:
                    game_state_for_json[key] = json.loads(game_state_for_json[key])
                except json.JSONDecodeError:
                    game_state_for_json[key] = None

        # Convert datetime objects to ISO format strings for JSON serialization
        for key, value in game_state_for_json.items():
            if isinstance(value, datetime):
                game_state_for_json[key] = value.isoformat()

        emit('game_state_update', game_state_for_json, room=game_uuid)
    else:
        emit('game_error', {'message': 'Game state not found.'}, room=game_uuid)


if __name__ == '__main__':
    # Initialize the database if it doesn't exist
    if not os.path.exists(DATABASE):
        init_db()
    socketio.run(app, debug=True)

