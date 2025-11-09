import random
import string
import time
from flask import Flask, render_template, request, Response
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy 
import csv 
from io import StringIO 

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-for-testing!'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
DB_PATH = '/var/data/game_data.db'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'

socketio = SocketIO(app)
db = SQLAlchemy(app)

class Play(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # --- Game/Room Identifiers ---
    # We'll use the room_code as the game_session_id
    game_session_id = db.Column(db.String(50), nullable=False, index=True)
    
    # --- Round Context ---
    round_number = db.Column(db.Integer, nullable=False)
    set_number = db.Column(db.Integer, nullable=False)
    
    # --- Play-Specific Data ---
    play_number_in_round = db.Column(db.Integer, nullable=False)
    player_sid = db.Column(db.String(100)) # The player who *played* the card
    value_played = db.Column(db.Integer, nullable=False)
    time_since_previous = db.Column(db.Float, nullable=False)
    was_mistake = db.Column(db.Boolean, nullable=False)
    
    # --- Player Input Data ---
    # This is the data from the *other* player (the observer)
    observer_input = db.Column(db.String(100))

    def __repr__(self):
        return f'<Play {self.id} (Room: {self.game_session_id} Round: {self.round_number})>'

game_rooms = {}

# 4. --- Main Flask Route ---
@app.route('/')
def index():
    return render_template('index.html')

# 5. --- SocketIO Handlers ---

def generate_room_code(length=4):
    return "".join(random.choices(string.ascii_uppercase, k=length))
    
def get_room_code_for_sid(sid):
    for room_code, data in game_rooms.items():
        if sid in data['players']:
            return room_code
    return None

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    room_code = get_room_code_for_sid(request.sid)
    
    if room_code and room_code in game_rooms:
        room = game_rooms[room_code]
        
        # Tell the *other* player their opponent left
        for player_sid in room['players']:
            if player_sid != request.sid:
                emit('opponent_disconnected', room=player_sid)
        
        # This is the key:
        # We remove the room from memory
        game_rooms.pop(room_code, None)
        print(f"Room {room_code} cleaned up due to disconnect.")

@socketio.on('create_room')
def handle_create_room():
    room_code = generate_room_code()
    while room_code in game_rooms:
        room_code = generate_room_code()
        
    game_rooms[room_code] = {
        'players': [request.sid],
        'game_state': {}
    }
    join_room(room_code)
    print(f"Room {room_code} created. Player 1: {request.sid}")
    emit('room_created', {'room_code': room_code})

@socketio.on('join_room')
def handle_join_room(data):
    room_code = data.get('room_code')
    if not room_code in game_rooms:
        emit('error_message', {'message': 'Room not found.'})
        return
    room = game_rooms[room_code]
    if len(room['players']) >= 2:
        emit('error_message', {'message': 'This room is full.'})
        return
    if request.sid in room['players']:
        return
    room['players'].append(request.sid)
    join_room(room_code)
    print(f"Player 2 {request.sid} joined room {room_code}.")
    emit('game_ready', room=room_code)

@socketio.on('start_game')
def handle_start_game():
    room_code = get_room_code_for_sid(request.sid)
    if not room_code: return
    room = game_rooms[room_code]
    if len(room['players']) != 2: 
        return
        
    player1_sid = room['players'][0]
    player2_sid = room['players'][1]
    
    all_numbers = random.sample(range(0, 101), 10)
    hand1 = sorted(all_numbers[:5])
    hand2 = sorted(all_numbers[5:])
    

    room['game_state'] = {
        'round_number': 1,
        'set_number': 1,
        'mistake_count': 0,
        'play_start_time': time.time(),
        'game_status': 'running',
        'all_played_list': [], # Will store {value: X, isMistake: bool}
        'hands': {
            player1_sid: hand1,
            player2_sid: hand2
        },
        'pending_inputs': {},
        'game_data_buffer': []
    }
    
    print(f"Game starting in room {room_code}. Sending hands.")
    
    emit('game_started', {
        'hand': hand1,
        'board': [],
        'round': 1,
        'set': 1
    }, room=player1_sid)
    
    emit('game_started', {
        'hand': hand2,
        'board': [],
        'round': 1,
        'set': 1
    }, room=player2_sid)

@socketio.on('start_next_round')
def handle_start_next_round():
    sid = request.sid
    room_code = get_room_code_for_sid(sid)
    
    if not room_code:
        print(f"Error: Player {sid} not in a room.")
        return
        
    room = game_rooms[room_code]
    state = room['game_state']
    
    # --- 1. Increment Round and Set ---
    new_round_num = state['round_number'] + 1
    new_set_num = 2 if new_round_num > 5 else 1
    
    # --- 2. Generate New Game State ---
    player1_sid = room['players'][0]
    player2_sid = room['players'][1]
    
    all_numbers = random.sample(range(0, 101), 10)
    hand1 = sorted(all_numbers[:5])
    hand2 = sorted(all_numbers[5:])
    
    # Reset the round-specific state
    state['round_number'] = new_round_num
    state['set_number'] = new_set_num
    state['mistake_count'] = 0,
    state['play_start_time'] = time.time(),
    state['game_status'] = 'running'
    state['all_played_list'] = []
    state['hands'] = {
        player1_sid: hand1,
        player2_sid: hand2
    }
    state['pending_inputs'] = {}
    
    print(f"Starting round {new_round_num} in room {room_code}.")
    
    # --- 3. Emit `game_started` just like before ---
    # This tells the clients to render the new hands and board
    emit('game_started', {
        'hand': hand1,
        'board': [],
        'round': new_round_num,
        'set': new_set_num
    }, room=player1_sid)
    
    emit('game_started', {
        'hand': hand2,
        'board': [],
        'round': new_round_num,
        'set': new_set_num
    }, room=player2_sid)    
    
## --- UPDATED: Handle a player playing a number ---
@socketio.on('play_number')
def handle_play_number(data):
    value = data.get('value')
    actor_sid = request.sid
    room_code = get_room_code_for_sid(actor_sid)
    
    # --- 1. Validation ---
    if not room_code: return
    room = game_rooms[room_code]
    state = room['game_state']
    if state['game_status'] != 'running': return
    player_hand = state['hands'].get(actor_sid)
    if not player_hand or value not in player_hand: return

    # --- 2. Game Logic ---
    current_time = time.time()
    play_time = current_time - state['play_start_time']
    
    # Get the observer's SID
    all_players = room['players']
    observer_sid = all_players[0] if all_players[1] == actor_sid else all_players[1]

    # Find the true minimum
    all_remaining_numbers = state['hands'][all_players[0]] + state['hands'][all_players[1]]
    true_min = min(all_remaining_numbers)
    
    was_mistake = False
    if value != true_min:
        was_mistake = True
        state['mistake_count'] += 1
    
    # Remove card from hand
    player_hand.remove(value)
        
    # Add card to board
    play_data = {
        'value': value,
        'isMistake': was_mistake,
        'player_sid': actor_sid,
        'time_played': play_time
    }
    state['all_played_list'].append(play_data)
    
    # --- 3. Pause and Store Temp Data ---
    
    # Set game status to wait
    state['game_status'] = 'waiting_for_input'
    
    # Store the data we'll need to save *after* we get the input
    state['temp_play_data'] = play_data
    state['observer_sid'] = observer_sid
    state['actor_sid'] = actor_sid
    
    print(f"Player {actor_sid} played {value}. Waiting for input from {observer_sid}.")

    # --- 4. Emit Specific Events ---
    
    # Tell the actor (player who clicked) to wait
    emit('wait_for_input', room=actor_sid)
    
    # Tell the observer (other player) to give input
    emit('request_input', {'set': state['set_number']}, room=observer_sid)
    
    # Send mistake notice to *both* players
    if was_mistake:
        emit('mistake_notice', {
            'value': value,
            'correct_value': true_min
        }, room=room_code)

## --- UPDATED: Handle the observer's submitted input ---
@socketio.on('submit_input')
def handle_submit_input(data):
    observer_sid = request.sid
    input_data = data.get('input_data')
    room_code = get_room_code_for_sid(observer_sid)
    
    # --- 1. Validation ---
    if not room_code: return
    room = game_rooms[room_code]
    state = room['game_state']
    
    if state['game_status'] != 'waiting_for_input' or state['observer_sid'] != observer_sid:
        print(f"Warning: Player {observer_sid} submitted input at an invalid time.")
        return
    
    # Get the data we stored temporarily
    play_data = state.pop('temp_play_data', {})
    actor_sid = state.pop('actor_sid', None)

    # Create the new Play object
    new_play = Play(
        game_session_id=room_code,
        round_number=state['round_number'],
        set_number=state['set_number'],
        play_number_in_round=len(state['all_played_list']),
        player_sid=actor_sid,
        value_played=play_data.get('value'),
        time_since_previous=play_data.get('time_played'),
        was_mistake=play_data.get('isMistake'),
        observer_input=input_data
    )
    
    # Add the new Play object to our buffer instead of the DB
    state['game_data_buffer'].append(new_play)
    
    print(f"--- Data Buffered (Play {len(state['game_data_buffer'])}/100) ---")
    print(f"  Room: {room_code}, Round: {state['round_number']}")
    print(f"---------------------------------")
    # --- 3. Resume Game and Broadcast Update ---
    
    state['game_status'] = 'running'
    state['play_start_time'] = time.time()
    
    for player_sid in room['players']:
        emit('game_state_update', {
            'hand': state['hands'][player_sid],
            'board': state['all_played_list']
        }, room=player_sid)

    # --- 4. Check for Round/Game End ---
    if len(state['all_played_list']) == 10:
        print(f"Round {state['round_number']} over for room {room_code}.")
        
        if state['round_number'] >= 10:
            # --- THIS IS THE GAME OVER BLOCK ---
            print(f"GAME OVER for room {room_code}. Committing data.")
            
            # --- ADD THIS DATABASE LOGIC ---
            try:
                # Get all 100 plays from the buffer
                all_plays_to_save = state['game_data_buffer']
                
                # Add them all to the session at once
                db.session.add_all(all_plays_to_save)
                
                # Commit the transaction
                db.session.commit()
                print(f"--- BATCH DATABASE SAVE SUCCESS ({len(all_plays_to_save)} plays) ---")
            
            except Exception as e:
                db.session.rollback()
                print(f"!!! BATCH DATABASE SAVE FAILED: {e} !!!")
            # --- END OF NEW LOGIC ---

            emit('game_over', {
                'round': state['round_number'],
                'mistakes': state['mistake_count']
            }, room=room_code)
            
            # Clean up the completed game room
            game_rooms.pop(room_code, None)

        else:
            # (This is the Round Over block)
            emit('round_over', {
                'round': state['round_number'],
                'mistakes': state['mistake_count']
            }, room=room_code)

@app.route('/admin/export/<secret_key>')
def export_data(secret_key):
    # Change this to a long, random password!
    if secret_key != 'none-shall-pass-unless-their-names-starts-with-an-I':
        return "Not authorized", 403

    # Use StringIO to create a file in memory
    si = StringIO()
    cw = csv.writer(si)
    
    # Get all plays from the database, ordered by time
    plays = Play.query.order_by(Play.id).all()
    
    # Write Header Row
    header = [
        'id', 'game_session_id', 'round_number', 'set_number', 
        'play_number_in_round', 'player_sid', 'value_played', 
        'time_since_previous', 'was_mistake', 'observer_input'
    ]
    cw.writerow(header)
    
    # Write Data Rows
    for play in plays:
        cw.writerow([
            play.id, play.game_session_id, play.round_number, 
            play.set_number, play.play_number_in_round, play.player_sid,
            play.value_played, play.time_since_previous, play.was_mistake,
            play.observer_input
        ])
    
    # Format as a downloadable file
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition":
                 "attachment; filename=game_export.csv"})
