import eventlet
eventlet.monkey_patch()

import random
import string
import time
from flask import Flask, render_template, request, Response
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy 
import csv 
from io import StringIO 
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-for-testing!'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')

socketio = SocketIO(app)
db = SQLAlchemy(app)

class Play(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    game_session_id = db.Column(db.String(50), nullable=False, index=True)
    
    round_number = db.Column(db.Integer, nullable=False)
    set_number = db.Column(db.Integer, nullable=False)
    
    play_number_in_round = db.Column(db.Integer, nullable=False)
    player_sid = db.Column(db.String(100))
    value_played = db.Column(db.Integer, nullable=False)
    time_since_previous = db.Column(db.Float, nullable=False)
    was_mistake = db.Column(db.Boolean, nullable=False)
    
    observer_input = db.Column(db.String(100))

    def __repr__(self):
        return f'<Play {self.id} (Room: {self.game_session_id} Round: {self.round_number})>'

game_rooms = {}

@app.route('/')
def index():
    return render_template('index.html')

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
    
    if room_code in game_rooms:
        room = game_rooms[room_code]
        
        leave_room(room_code)
        # Tell the *other* player their opponent left
        for player_sid in room['players']:
            if player_sid != request.sid:
                emit('opponent_disconnected', room=player_sid)
            if player_sid == request.sid:
                room['players'].remove(player_sid)
        
        if len(room['players']) == 0:
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

@socketio.on('start_round')
def handle_start_round():
    room_code = get_room_code_for_sid(request.sid)
    room = game_rooms[room_code]
    if not room or len(room['players']) != 2: return
        
    if not room['game_state']:
        room['game_state'] = {
        'game_data_buffer': [],
        'round_number': 1, # Will be set to 1 by the helper
        'set_number': 0,
        'mistake_count': 0,
        'game_status': 'pending',
        'play_start_time': None,
        'all_played_list': [],
        'hands': {},
        'pending_inputs': {}
        }
        start_new_round(room_code, round_num = room["game_state"]["round_number"])
    
    #If a player reconnects halfway through a round, just reset the round
    if len(room['game_state']['all_played_list']) != 10:
        start_new_round(room_code, round_num = room["game_state"]["round_number"])
    else: #Start new round
        start_new_round(room_code, round_num = room["game_state"]["round_number"] + 1)

def start_new_round(room_code, round_num):
    room = game_rooms.get(room_code)
        
    state = room['game_state']

    set_num = 2 if round_num > 5 else 1
    player1_sid = room['players'][0]
    player2_sid = room['players'][1]
    
    all_numbers = random.sample(range(1, 101), 10)
    hand1 = sorted(all_numbers[:5])
    hand2 = sorted(all_numbers[5:])

    state['round_number'] = round_num
    state['set_number'] = set_num
    state['mistake_count'] = 0
    state['game_status'] = 'running'
    state['play_start_time'] = time.time()
    state['all_played_list'] = []
    state['hands'] = {
        player1_sid: hand1,
        player2_sid: hand2
    }
    state['pending_inputs'] = {}
    
    print(f"Starting round {round_num} (Set {set_num}) in room {room_code}.")

    emit('game_started', {
        'hand': hand1,
        'board': [],
        'round': round_num,
        'set': set_num
    }, room=player1_sid)
    
    emit('game_started', {
        'hand': hand2,
        'board': [],
        'round': round_num,
        'set': set_num
    }, room=player2_sid)

@socketio.on('play_number')
def handle_play_number(data):
    value = data.get('value') 
    actor_sid = request.sid
    room_code = get_room_code_for_sid(actor_sid)
    if not room_code:
        return
    
    room = game_rooms[room_code]   
    all_players = room['players']
    observer_sid = all_players[0] if all_players[1] == actor_sid else all_players[1]

    state = room['game_state']
    if state['game_status'] != 'running': 
        return
    
    actor_hand = state['hands'][actor_sid].copy()
    observer_hand = state['hands'][observer_sid].copy()
    if not actor_hand or value not in actor_hand: 
        return

    current_time = time.time()
    play_time = current_time - state['play_start_time']

    all_remaining_numbers = state['hands'][all_players[0]] + state['hands'][all_players[1]]
    true_min = min(all_remaining_numbers)
    
    was_mistake = False
    if value != true_min:
        was_mistake = True
        state['mistake_count'] += 1

        for card in observer_hand:
            if card < value:
                play_obvious_card(card, observer_sid)
        
        for card in actor_hand:
            if card < value:
                play_obvious_card(card, actor_sid)

    play_data = {
        'value': value,
        'isMistake': was_mistake,
        'player_sid': actor_sid,
        'time_played': play_time
    }
    state['all_played_list'].append(play_data)
    actor_hand.remove(value)

    if len(actor_hand) == 0:
        for card in observer_hand:
            play_obvious_card(card, observer_sid)
    if len(observer_hand) == 0:
        for card in actor_hand:
            play_obvious_card(card, actor_sid)

    state['game_status'] = 'waiting_for_input'

    state['temp_play_data'] = play_data
    state['observer_sid'] = observer_sid
    state['actor_sid'] = actor_sid
    
    print(f"Player {actor_sid} played {value}. Waiting for input from {observer_sid}.")

    emit('wait_for_input', room=actor_sid)

    emit('request_input', {'set': state['set_number']}, room=observer_sid)

    if was_mistake:
        emit('mistake_notice', {
            'value': value,
            'correct_value': true_min
        }, room=room_code)

def play_obvious_card(value, player_sid):
    room_code = get_room_code_for_sid(player_sid)
    room = game_rooms[room_code]
    state = room['game_state']  

    play_data = {
        'value': value,
        'isMistake': False,
        'player_sid': player_sid,
        'time_played': 0
    }
    state['all_played_list'].append(play_data)
    state['hands'][player_sid].remove(value)

    new_play = Play(
        game_session_id=room_code,
        round_number=state['round_number'],
        set_number=state['set_number'],
        play_number_in_round=len(state['all_played_list']),
        player_sid=player_sid,
        value_played=value,
        time_since_previous=0,
        was_mistake=False,
        observer_input=None
    )
    state['game_data_buffer'].append(new_play)

    for player in room['players']:
        emit('game_state_update', {
            'hand': state['hands'][player],
            'board': state['all_played_list'],
            'start_counter': False
        }, room=player)

@socketio.on('submit_input')
def handle_submit_input(data):
    observer_sid = request.sid
    input_data = data.get('input_data')
    room_code = get_room_code_for_sid(observer_sid)
    
    if not room_code: return
    room = game_rooms[room_code]
    state = room['game_state']
    
    if state['game_status'] != 'waiting_for_input' or state['observer_sid'] != observer_sid:
        print(f"Warning: Player {observer_sid} submitted input at an invalid time.")
        return
    
    play_data = state.pop('temp_play_data', {})
    actor_sid = state.pop('actor_sid', None)

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
    
    state['game_data_buffer'].append(new_play)
    
    print(f"--- Data Buffered (Play {len(state['game_data_buffer'])}/100) ---")
    print(f"  Room: {room_code}, Round: {state['round_number']}")
    print(f"---------------------------------")
    
    state['game_status'] = 'running'
    state['play_start_time'] = time.time()

    if len(state['all_played_list']) >= 10:
        print(f"Round {state['round_number']} over for room {room_code}.")

        for player_sid in room['players']:
            emit('game_state_update', {
                'hand': state['hands'][player_sid],
                'board': state['all_played_list'],
                'start_counter': False
            }, room=player_sid)
        
        if state['round_number'] >= 10:
            print(f"GAME OVER for room {room_code}. Committing data.")
            
            try:
                all_plays_to_save = state['game_data_buffer']

                db.session.add_all(all_plays_to_save)

                db.session.commit()
                print(f"--- BATCH DATABASE SAVE SUCCESS ({len(all_plays_to_save)} plays) ---")
            
            except Exception as e:
                db.session.rollback()
                print(f"!!! BATCH DATABASE SAVE FAILED: {e} !!!")

            emit('game_over', {
                'round': state['round_number'],
                'mistakes': state['mistake_count']
            }, room=room_code)

            game_rooms.pop(room_code, None)

        else:
            # (This is the Round Over block)
            emit('round_over', {
                'round': state['round_number'],
                'mistakes': state['mistake_count']
            }, room=room_code)
    
    else:
        for player_sid in room['players']:
            emit('game_state_update', {
                'hand': state['hands'][player_sid],
                'board': state['all_played_list'],
                'start_counter': True
            }, room=player_sid)
@socketio.on('reset_round')
def handle_reset_round():
    sid = request.sid
    room_code = get_room_code_for_sid(sid)
    
    if not room_code:
        print(f"Error: Player {sid} not in a room.")
        return
        
    room = game_rooms[room_code]
    state = room['game_state']
    
    current_round_num = state['round_number']
    current_set_num = state['set_number']
    
    print(f"RESETTING round {current_round_num} in room {room_code}.")
    
    player1_sid = room['players'][0]
    player2_sid = room['players'][1]
    
    all_numbers = random.sample(range(0, 101), 10)
    hand1 = sorted(all_numbers[:5])
    hand2 = sorted(all_numbers[5:])

    state['mistake_count'] = 0
    state['game_status'] = 'running'
    state['play_start_time'] = time.time()
    state['all_played_list'] = []
    state['hands'] = {
        player1_sid: hand1,
        player2_sid: hand2
    }
    state['pending_inputs'] = {}
    
    emit('game_started', {
        'hand': hand1,
        'board': [],
        'round': current_round_num,
        'set': current_set_num
    }, room=player1_sid)
    
    emit('game_started', {
        'hand': hand2,
        'board': [],
        'round': current_round_num,
        'set': current_set_num
    }, room=player2_sid)

@app.route('/admin/export/<secret_key>')
def export_data(secret_key):
    if secret_key != 'none-shall-pass-unless-their-names-starts-with-an-I':
        return "Not authorized", 403

    si = StringIO()
    cw = csv.writer(si)
 
    plays = Play.query.order_by(Play.id).all()
    
    header = [
        'id', 'game_session_id', 'round_number', 'set_number', 
        'play_number_in_round', 'player_sid', 'value_played', 
        'time_since_previous', 'was_mistake', 'observer_input'
    ]
    cw.writerow(header)
    
    for play in plays:
        cw.writerow([
            play.id, play.game_session_id, play.round_number, 
            play.set_number, play.play_number_in_round, play.player_sid,
            play.value_played, play.time_since_previous, play.was_mistake,
            play.observer_input
        ])
    
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition":
                 "attachment; filename=game_export.csv"})

