import socket
import logging
import argparse
import os
from concurrent.futures import ThreadPoolExecutor
from game_server import HttpServerGame

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

httpserver = HttpServerGame()

def ProcessTheClient(connection, address):
    logging.info(f"Accepted connection from {address}")
    try:
        # Step 1: Read headers until \r\n\r\n
        rcv = b''
        while b'\r\n\r\n' not in rcv:
            data = connection.recv(4096)
            if not data:
                break
            rcv += data
        if b'\r\n\r\n' not in rcv:
            logging.warning(f"Malformed HTTP request from {address}")
            connection.close()
            return
        header_end = rcv.index(b'\r\n\r\n') + 4
        headers_raw = rcv[:header_end].decode(errors='replace')
        body = rcv[header_end:]

        # Step 2: Parse Content-Length
        content_length = 0
        for line in headers_raw.split('\r\n'):
            if line.lower().startswith('content-length:'):
                try:
                    content_length = int(line.split(':', 1)[1].strip())
                except Exception:
                    content_length = 0
        # Step 3: Read the rest of the body if needed
        while len(body) < content_length:
            more = connection.recv(4096)
            if not more:
                break
            body += more
        # Step 4: Reconstruct the full HTTP request
        full_request = rcv[:header_end] + body
        request_str = full_request.decode(errors='replace')
        logging.info(f"Request from {address}: {request_str.strip()}")
        hasil = httpserver.proses(request_str)
        hasil = hasil + b"\r\n\r\n"
        connection.sendall(hasil)
        logging.info(f"Response sent to {address} ({len(hasil)} bytes)")
    except OSError as e:
        logging.error(f"OSError with {address}: {e}")
    except Exception as e:
        logging.error(f"Exception with {address}: {e}")
    finally:
        connection.close()
        logging.info(f"Connection closed for {address}")
    return

def Server(port):
    the_clients = []
    my_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    my_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Bind to 0.0.0.0 untuk Railway (bukan localhost)
    my_socket.bind(('0.0.0.0', port))
    my_socket.listen(5)
    logging.info(f"Game server listening on port {port}")
    with ThreadPoolExecutor(10) as executor:
        while True:
            connection, client_address = my_socket.accept()
            logging.info(f"New client: {client_address}")
            p = executor.submit(ProcessTheClient, connection, client_address)
            the_clients.append(p)
            jumlah = ['x' for i in the_clients if not i.done()]
            logging.info(f"Active threads: {len(jumlah)}")

def main():
    # Gunakan PORT dari environment variable (Railway requirement)
    port = int(os.getenv('PORT', 5001))
    Server(port)

if __name__ == "__main__":
    main()
