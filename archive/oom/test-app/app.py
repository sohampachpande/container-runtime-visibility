import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        if request.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return

        if request.path == "/allocate":
            mb = int(parse_qs(request.query).get("mb", ["0"])[0])
            logging.info("allocation requested: %d MiB", mb)
            memory = []
            for allocated in range(mb):
                chunk = bytearray(1024 * 1024)
                for page in range(0, len(chunk), 4096):
                    chunk[page] = 1
                memory.append(chunk)
                if (allocated + 1) % 8 == 0:
                    logging.info("allocated %d MiB", allocated + 1)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"allocated {len(memory)} MiB\n".encode())
            return

        self.send_error(404)

    def log_message(self, format, *args):
        logging.info("%s - %s", self.address_string(), format % args)


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
