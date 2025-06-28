import http.server
import socketserver
import json
import threading
import uuid
import urllib.parse
import redis
import os

# Redis configuration for Railway
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
try:
    redis_client = redis.from_url(redis_url, decode_responses=True)
    # Test connection
    redis_client.ping()
    print(f"Connected to Redis: {redis_url}")
except Exception as e:
    print(f"Redis connection failed: {e}")
    redis_client = None

class GameServerHandler(http.server.BaseHTTPRequestHandler):
    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers()

    def do_POST(self):
        if not redis_client:
            self._set_headers(500)
            self.wfile.write(json.dumps({'error': 'Redis not available'}).encode())
            return

        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            data = {}
            
        print(f"POST {self.path}: {data}")
        
        if self.path == '/create_room':
            player = data.get('player')
            if not player:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Missing player in request'}).encode())
                return
            room_id = str(uuid.uuid4())[:8]
            room = {
                'players': [player],
                'ready': {player: False},
                'board': [[0]*7 for _ in range(6)],
                'turn': 0,
                'winner': None
            }
            redis_client.set(f'room:{room_id}', json.dumps(room), ex=3600)  # 1 hour expiry
            self._set_headers()
            self.wfile.write(json.dumps({'room_id': room_id}).encode())
            
        elif self.path == '/join_room':
            player = data.get('player')
            room_id = data.get('room_id')
            room = get_room(room_id)
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            if len(room['players']) >= 2:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Room already full'}).encode())
                return
            if player in room['players']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Player already in room'}).encode())
                return
            room['players'].append(player)
            room['ready'][player] = False
            save_room(room_id, room)
            self._set_headers()
            self.wfile.write(json.dumps({'room_id': room_id, 'success': True}).encode())
            
        elif self.path == '/quick_join':
            player = data.get('player')
            found = False
            try:
                for key in redis_client.scan_iter('room:*'):
                    room_id = key.split(':', 1)[1]
                    room = get_room(room_id)
                    if room and len(room['players']) == 1:
                        room['players'].append(player)
                        room['ready'][player] = False
                        save_room(room_id, room)
                        self._set_headers()
                        self.wfile.write(json.dumps({'room_id': room_id}).encode())
                        found = True
                        break
            except Exception as e:
                print(f"Error in quick_join: {e}")
                
            if not found:
                room_id = str(uuid.uuid4())[:8]
                room = {
                    'players': [player],
                    'ready': {player: False},
                    'board': [[0]*7 for _ in range(6)],
                    'turn': 0,
                    'winner': None
                }
                redis_client.set(f'room:{room_id}', json.dumps(room), ex=3600)
                self._set_headers()
                self.wfile.write(json.dumps({'room_id': room_id}).encode())
                
        elif self.path == '/set_ready':
            player = data.get('player')
            room_id = data.get('room_id')
            room = get_room(room_id)
            if not room or player not in room['players']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid room or player'}).encode())
                return
            room['ready'][player] = True
            all_ready = all(room['ready'].values()) and len(room['players']) == 2
            save_room(room_id, room)
            self._set_headers()
            self.wfile.write(json.dumps({'all_ready': all_ready}).encode())
            
        elif self.path == '/make_move':
            player = data.get('player')
            room_id = data.get('room_id')
            col = data.get('col')
            room = get_room(room_id)
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            if room['winner'] is not None:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Game over'}).encode())
                return
            try:
                player_idx = room['players'].index(player)
            except ValueError:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Player not in room'}).encode())
                return
            if room['turn'] != player_idx:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Not your turn'}).encode())
                return
            for row in reversed(range(6)):
                if room['board'][row][col] == 0:
                    room['board'][row][col] = player_idx + 1
                    if check_win(room['board'], row, col, player_idx + 1):
                        room['winner'] = player
                    else:
                        room['turn'] = 1 - room['turn']
                    save_room(room_id, room)
                    self._set_headers()
                    self.wfile.write(json.dumps({'success': True, 'winner': room['winner']}).encode())
                    return
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': 'Column full'}).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def do_GET(self):
        if not redis_client:
            self._set_headers(500)
            self.wfile.write(json.dumps({'error': 'Redis not available'}).encode())
            return
            
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        
        print(f"GET {path}: {query}")
        
        if path == '/lobby_status':
            room_id = query.get('room_id', [None])[0]
            room = get_room(room_id)
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            self._set_headers()
            self.wfile.write(json.dumps({'players': room['players'], 'ready': room['ready']}).encode())
            
        elif path == '/game_state':
            room_id = query.get('room_id', [None])[0]
            room = get_room(room_id)
            if not room:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Room not found'}).encode())
                return
            self._set_headers()
            self.wfile.write(json.dumps({'board': room['board'], 'turn': room['turn'], 'winner': room['winner']}).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

def get_room(room_id):
    if not room_id or not redis_client:
        return None
    try:
        data = redis_client.get(f'room:{room_id}')
        if data:
            return json.loads(data)
    except Exception as e:
        print(f"Error getting room {room_id}: {e}")
    return None

def save_room(room_id, room):
    if redis_client:
        try:
            redis_client.set(f'room:{room_id}', json.dumps(room), ex=3600)
        except Exception as e:
            print(f"Error saving room {room_id}: {e}")

def check_win(board, row, col, player):
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

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    with socketserver.ThreadingTCPServer(("", port), GameServerHandler) as httpd:
        print(f"Game server running on port {port}")
        httpd.serve_forever()
