import uuid
import json
import redis
import os
from datetime import datetime

redis_client = redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379'), decode_responses=True)

class HttpServerGame:
    def __init__(self):
        pass
    
    def response(self, kode=404, message='Not Found', messagebody=bytes(), headers={}):
        tanggal = datetime.now().strftime('%c')
        resp = []
        resp.append(f"HTTP/1.0 {kode} {message}\r\n")
        resp.append(f"Date: {tanggal}\r\n")
        resp.append("Connection: close\r\n")
        resp.append("Server: myserver/1.0\r\n")
        resp.append(f"Content-Length: {len(messagebody)}\r\n")
        for kk in headers:
            resp.append(f"{kk}:{headers[kk]}\r\n")
        resp.append("\r\n")
        response_headers = ''.join(resp)
        if isinstance(messagebody, str):
            messagebody = messagebody.encode()
        response = response_headers.encode() + messagebody
        return response

    def proses(self, data):
        requests = data.split("\r\n")
        baris = requests[0]
        all_headers = [n for n in requests[1:] if n != '']
        j = baris.split(" ")
        try:
            method = j[0].upper().strip()
            if method == 'GET':
                object_address = j[1].strip()
                return self.http_get(object_address, all_headers)
            if method == 'POST':
                object_address = j[1].strip()
                body = requests[-1] if requests[-1] else ''
                return self.http_post(object_address, all_headers, body)
            else:
                return self.response(400, 'Bad Request', b'', {})
        except IndexError:
            return self.response(400, 'Bad Request', b'', {})

    def http_get(self, object_address, headers):
        if object_address == '/':
            return self.response(200, 'OK', b'Connect Four Game Server', dict())
        if object_address.startswith('/game_state'):
            import urllib.parse
            parsed = urllib.parse.urlparse(object_address)
            query = urllib.parse.parse_qs(parsed.query)
            room_id = query.get('room_id', [None])[0]
            room = self.get_room(room_id)
            if not room:
                return self.response(404, 'Not Found', json.dumps({'error': 'Room not found'}).encode(), {'Content-Type': 'application/json'})
            return self.response(200, 'OK', json.dumps({'board': room['board'], 'turn': room['turn'], 'winner': room['winner']}).encode(), {'Content-Type': 'application/json'})
        if object_address.startswith('/lobby_status'):
            import urllib.parse
            parsed = urllib.parse.urlparse(object_address)
            query = urllib.parse.parse_qs(parsed.query)
            room_id = query.get('room_id', [None])[0]
            room = self.get_room(room_id)
            if not room:
                return self.response(404, 'Not Found', json.dumps({'error': 'Room not found'}).encode(), {'Content-Type': 'application/json'})
            return self.response(200, 'OK', json.dumps({'players': room['players'], 'ready': room['ready']}).encode(), {'Content-Type': 'application/json'})
        return self.response(404, 'Not Found', b'', {})

    def http_post(self, object_address, headers, body):
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        if object_address == '/create_room':
            player = data.get('player')
            if not player:
                return self.response(400, 'Bad Request', json.dumps({'error': 'Missing player in request'}).encode(), {'Content-Type': 'application/json'})
            room_id = str(uuid.uuid4())[:8]
            room = {
                'players': [player],
                'ready': {player: False},
                'board': [[0]*7 for _ in range(6)],
                'turn': 0,
                'winner': None
            }
            self.save_room(room_id, room)
            return self.response(200, 'OK', json.dumps({'room_id': room_id}).encode(), {'Content-Type': 'application/json'})
        elif object_address == '/join_room':
            player = data.get('player')
            room_id = data.get('room_id')
            room = self.get_room(room_id)
            if not room:
                return self.response(404, 'Not Found', json.dumps({'error': 'Room not found'}).encode(), {'Content-Type': 'application/json'})
            if len(room['players']) >= 2:
                return self.response(400, 'Bad Request', json.dumps({'error': 'Room already full'}).encode(), {'Content-Type': 'application/json'})
            if player in room['players']:
                return self.response(400, 'Bad Request', json.dumps({'error': 'Player already in room'}).encode(), {'Content-Type': 'application/json'})
            room['players'].append(player)
            room['ready'][player] = False
            self.save_room(room_id, room)
            return self.response(200, 'OK', json.dumps({'room_id': room_id, 'success': True}).encode(), {'Content-Type': 'application/json'})
        elif object_address == '/quick_join':
            player = data.get('player')
            found = False
            for key in redis_client.scan_iter('room:*'):
                room_id = key.split(':', 1)[1]
                room = self.get_room(room_id)
                if room and len(room['players']) == 1:
                    room['players'].append(player)
                    room['ready'][player] = False
                    self.save_room(room_id, room)
                    found = True
                    return self.response(200, 'OK', json.dumps({'room_id': room_id}).encode(), {'Content-Type': 'application/json'})
            if not found:
                room_id = str(uuid.uuid4())[:8]
                room = {
                    'players': [player],
                    'ready': {player: False},
                    'board': [[0]*7 for _ in range(6)],
                    'turn': 0,
                    'winner': None
                }
                self.save_room(room_id, room)
                return self.response(200, 'OK', json.dumps({'room_id': room_id}).encode(), {'Content-Type': 'application/json'})
        elif object_address == '/set_ready':
            player = data.get('player')
            room_id = data.get('room_id')
            room = self.get_room(room_id)
            if not room or player not in room['players']:
                return self.response(400, 'Bad Request', json.dumps({'error': 'Invalid room or player'}).encode(), {'Content-Type': 'application/json'})
            room['ready'][player] = True
            all_ready = all(room['ready'].values()) and len(room['players']) == 2
            self.save_room(room_id, room)
            return self.response(200, 'OK', json.dumps({'all_ready': all_ready}).encode(), {'Content-Type': 'application/json'})
        elif object_address == '/make_move':
            player = data.get('player')
            room_id = data.get('room_id')
            col = data.get('col')
            room = self.get_room(room_id)
            if not room:
                return self.response(404, 'Not Found', json.dumps({'error': 'Room not found'}).encode(), {'Content-Type': 'application/json'})
            if room['winner'] is not None:
                return self.response(400, 'Bad Request', json.dumps({'error': 'Game over'}).encode(), {'Content-Type': 'application/json'})
            try:
                player_idx = room['players'].index(player)
            except ValueError:
                return self.response(400, 'Bad Request', json.dumps({'error': 'Player not in room'}).encode(), {'Content-Type': 'application/json'})
            if room['turn'] != player_idx:
                return self.response(400, 'Bad Request', json.dumps({'error': 'Not your turn'}).encode(), {'Content-Type': 'application/json'})
            for row in reversed(range(6)):
                if room['board'][row][col] == 0:
                    room['board'][row][col] = player_idx + 1
                    if self.check_win(room['board'], row, col, player_idx + 1):
                        room['winner'] = player
                    else:
                        room['turn'] = 1 - room['turn']
                    self.save_room(room_id, room)
                    return self.response(200, 'OK', json.dumps({'success': True, 'winner': room['winner']}).encode(), {'Content-Type': 'application/json'})
            return self.response(400, 'Bad Request', json.dumps({'error': 'Column full'}).encode(), {'Content-Type': 'application/json'})
        return self.response(404, 'Not Found', b'', {})

    def get_room(self, room_id):
        if not room_id:
            return None
        data = redis_client.get(f'room:{room_id}')
        if data:
            return json.loads(data) # type: ignore
        return None

    def save_room(self, room_id, room):
        redis_client.set(f'room:{room_id}', json.dumps(room))

    def check_win(self, board, row, col, player):
        def count(dx, dy):
            cnt = 0
            x, y = col, row
            while 0 <= x < 7 and 0 <= y < 6 and board[y][x] == player:
                cnt += 1
                x += dx
                y += dy
            return cnt - 1
        directions = [ (1,0), (0,1), (1,1), (1,-1) ]
        for dx, dy in directions:
            total = 1 + count(dx, dy) + count(-dx, -dy)
            if total >= 4:
                return True
        return False
