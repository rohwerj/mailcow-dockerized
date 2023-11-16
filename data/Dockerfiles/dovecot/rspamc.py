#!/usr/bin/python3

import email
from email import policy
import http.client
import json
import socket
import subprocess
import sys

class UnixHTTPConnection(http.client.HTTPConnection):

    def __init__(self, path, host='localhost', port=None, timeout=None):
        http.client.HTTPConnection.__init__(self, host, port=port, timeout=timeout)
        self.path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        self.sock = sock

destination = sys.argv[1]
hostname = sys.argv[2]

arguments = ["/usr/lib/dovecot/deliver", "-e", "-d", destination]

msgBytes = sys.stdin.buffer.read()
msg = email.message_from_bytes(s=msgBytes, policy=policy.default)

try:
    bytes(msg)
except UnicodeEncodeError as e:
    print('Encoding error happened for email message {0}'.format(str(e)))
    process = subprocess.run(arguments, input=msgBytes)
    if process.returncode != 0:
        exit("Deliver failed with return code " + str(process.returncode))
    else:
        exit(0)

headers = {
  "Pass": "all"
}

connection = UnixHTTPConnection("/var/lib/rspamd/rspamd.sock")
connection.request("POST", "/checkv2", bytes(msg), headers)
response = connection.getresponse()
if response.status != 200:
    sys.exit("Status from rspamd is " + str(response.status) + ": " + response.reason)

server = response.getheader("Server")

data = response.read()
result = json.loads(data)

score = result["score"]
action = result["action"]

if server:
    msg["X-Spam-Scanner"] = server

if action == "reject" or action == "add header":
    spam = "yes"
else:
    spam = "no"

msg["X-Spam"] = spam
msg["X-Spam-Score"] = str(score)
msg["X-Spam-Action"] = action

process = subprocess.run(arguments, input=bytes(msg))
if process.returncode != 0:
    exit("Deliver failed with return code " + str(process.returncode))
