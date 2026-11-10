#!/usr/bin/python3

import fcntl
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import MySQLdb
import MySQLdb.cursors

run_dir = '/tmp'
lock_file = run_dir + '/getmail_busy.lock'

# Max size of getmail output stored in returned_text (the tail holds the summary)
max_output = 16 * 1024 * 1024

# send and receive query for getmail jobs
next_sql = """SELECT id, user, password, host, port, local_dest, protocol, use_ssl, delete_mail, read_all
    FROM getmail
      WHERE active = 1
        AND is_running = 0
        AND (
          UNIX_TIMESTAMP(NOW()) - UNIX_TIMESTAMP(last_run) > mins_interval * 60
          OR
          last_run IS NULL
        )
        {skip}
  ORDER BY last_run IS NOT NULL, last_run ASC, id ASC
  LIMIT 1"""

claim_sql = "UPDATE getmail SET is_running = 1 WHERE id = %s AND is_running = 0 AND active = 1"

conn = None
deferred = set()
stopping = False

def connect():
  return MySQLdb.connect(
      unix_socket='/var/run/mysqld/mysqld.sock',
      user=os.environ['DBUSER'],
      passwd=os.environ['DBPASS'],
      db=os.environ['DBNAME'],
      charset='utf8mb4',
      autocommit=True
  )

def query(sql, args=None):
  global conn
  try:
    conn.ping()
  except MySQLdb.Error:
    conn = connect()
  cur = conn.cursor(MySQLdb.cursors.DictCursor)
  cur.execute(sql, args)
  return cur

def unlink(files):
  for path in files:
    try:
      os.unlink(path)
    except OSError:
      pass

def getmail_running():
  for pid in filter(str.isdigit, os.listdir('/proc')):
    try:
      with open('/proc/' + pid + '/cmdline', 'rb') as f:
        cmdline = f.read().replace(b'\0', b' ')
    except OSError:
      continue
    # Checks if a getmail process is running, that was started by this runner
    if re.search(rb'/usr/bin/getmail', cmdline, re.I) and re.search(rb'--rcfile=' + run_dir.encode(), cmdline):
      return True
  return False

def finish_job(job_id, text, success):
  try:
    query(
        "UPDATE getmail SET returned_text = %s, success = %s, last_run = NOW(), is_running = 0 WHERE id = %s",
        (text, success, job_id)
    )
  except MySQLdb.Error:
    query(
        "UPDATE getmail SET success = 0, last_run = NOW(), is_running = 0 WHERE id = %s",
        (job_id,)
    )

def prepare_job(j, files):
  def tmpfile(content):
    fd, path = tempfile.mkstemp(prefix='getmail.', dir=run_dir)
    files.append(path)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
      f.write(content)
    return path

  # determine retriever type
  use_ssl = num(j['use_ssl'])
  protocol = j['protocol']
  if use_ssl == 1:
    retriever_type = "SimplePOP3SSLRetriever" if protocol == "POP3" else "SimpleIMAPSSLRetriever"
  else:
    retriever_type = "SimplePOP3Retriever" if protocol == "POP3" else "SimpleIMAPRetriever"

  read_all_bool = "true" if num(j['read_all']) == 1 else "false"
  delete_bool = "true" if num(j['delete_mail']) == 1 else "false"

  # generate configuration file for getmail
  rc_content = f"""[retriever]
type = {retriever_type}
server = {j['host']}
port = {j['port']}
username = {j['user']}
password = {j['password']}

[destination]
type = MDA_external
path = /usr/local/bin/rspamc.py
arguments = ("{j['local_dest']}","{j['host']}")
user = vmail
group = vmail

[options]
read_all = {read_all_bool}
delete = {delete_bool}
delivered_to = false
"""
  rc_file = tmpfile(rc_content)

  cmd = [
      "/usr/bin/getmail",
      f"--getmaildir={run_dir}",
      f"--rcfile={rc_file}"
  ]

  return {'id': j['id'], 'files': files, 'outfile': tmpfile(''), 'cmd': cmd}

def num(value):
  try:
    return int(value)
  except (TypeError, ValueError):
    return 0

def start_job(job):
  env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in os.environ}
  env['HOME'] = run_dir
  with open(job['outfile'], 'wb') as out:
    return subprocess.Popen(
        job['cmd'],
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        env=env
    )

def reap_job(job, exit_code):
  stdout = b''
  try:
    with open(job['outfile'], 'rb') as f:
      size = os.fstat(f.fileno()).st_size
      if size > max_output:
        f.seek(size - max_output)
        stdout = b'[output truncated, last %d bytes]\n' % max_output
      stdout += f.read()
  except OSError:
    pass
  unlink(job['files'])
  text = stdout.decode('utf-8', 'replace')

  success = 1 if exit_code == 0 else 0
  finish_job(job['id'], text, success)

def stop(signum, frame):
  global stopping
  stopping = True

# global file-lock with fcntl (prevents parallel execution of runner)
lock_fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
try:
  fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
  sys.exit("can't lock " + lock_file)

if getmail_running():
  print("getmail is active, exiting...")
  sys.exit(0)

for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT):
  signal.signal(sig, stop)

conn = connect()
# reset hanging flags of previous crashes
query("UPDATE getmail SET is_running = 0")

try:
  while not stopping:
    skip = ('AND id NOT IN (%s)' % ', '.join(['%s'] * len(deferred))) if deferred else ''
    j = query(next_sql.format(skip=skip), tuple(deferred) or None).fetchone()
    if not j:
      break

    # atomic claim (is_running 0 -> 1)
    if query(claim_sql, (j['id'],)).rowcount != 1:
      deferred.add(j['id'])
      continue

    files = []
    try:
      job = prepare_job(j, files)
    except Exception as e:
      print(f"getmail job {j['id']}: {e}", file=sys.stderr)
      finish_job(j['id'], f"Could not start or finish getmail: {e}", 0)
      continue

    try:
      proc = start_job(job)
      # since getmail reads the mailboxes sequentially we wait for the process here
      stdout_bytes, _ = proc.communicate()
      exit_code = proc.returncode

      print(f"exit = {exit_code}")
      reap_job(job, exit_code)

    except OSError as e:
      print(f"getmail job {j['id']}: {e}", file=sys.stderr)
      unlink(job['files'])
      query("UPDATE getmail SET is_running = 0 WHERE id = %s", (j['id'],))
      break

finally:
  # make sure the lock descriptor stays clean
  pass
