#!/usr/bin/perl

use DBI;
use LockFile::Simple qw(lock trylock unlock);
use Proc::ProcessTable;
use Data::Dumper qw(Dumper);
use IPC::Run 'run';
use File::Temp;
use Try::Tiny;
use sigtrap 'handler' => \&sig_handler, qw(INT TERM KILL QUIT);

sub trim { my $s = shift; $s =~ s/^\s+|\s+$//g; return $s };
my $t = Proc::ProcessTable->new;
my $getmail_running = grep { $_->{cmndline} =~ /^\/usr\/bin\/perl \/usr\/local\/bin\/getmail\s/ } @{$t->table};
if ($getmail_running ge 1)
{
  print "getmail is active, exiting...";
  exit;
}

sub qqw($) {
  my @params = ();
  my @values = split(/(?=--)/, $_[0]);
  foreach my $val (@values) {
    my @tmpparam = split(/ /, $val, 2);
    foreach my $tmpval (@tmpparam) {
        if ($tmpval ne '') {
          push @params, $tmpval;
        }
    }
  }
  foreach my $val (@params) {
    $val=trim($val);
  }
  return @params;
}

$run_dir="/tmp";
$dsn = 'DBI:mysql:database=' . $ENV{'DBNAME'} . ';mysql_socket=/var/run/mysqld/mysqld.sock';
$lock_file = $run_dir . "/getmail_busy";
$lockmgr = LockFile::Simple->make(-autoclean => 1, -max => 1);
$lockmgr->lock($lock_file) || die "can't lock ${lock_file}";
$dbh = DBI->connect($dsn, $ENV{'DBUSER'}, $ENV{'DBPASS'}, {
  mysql_auto_reconnect => 1,
  mysql_enable_utf8mb4 => 1
});
$dbh->do("UPDATE getmail SET is_running = 0");

sub sig_handler {
  # Send die to force exception in "run"
  die "sig_handler received signal, preparing to exit...\n";
};

open my $file, '<', "/etc/sogo/sieve.creds";
my $creds = <$file>;
close $file;
my ($master_user, $master_pass) = split /:/, $creds;
my $sth = $dbh->prepare("SELECT id,
  user,
  password,
  host,
  port,
  local_dest,
  protocol,
  use_ssl,
  delete_mail,
  read_all
    FROM getmail
      WHERE active = 1
        AND is_running = 0
        AND (
          UNIX_TIMESTAMP(NOW()) - UNIX_TIMESTAMP(last_run) > mins_interval * 60
          OR
          last_run IS NULL)
  ORDER BY last_run");

$sth->execute();
my $row;

while ($row = $sth->fetchrow_arrayref()) {

  $id                  = @$row[0];
  $user                = @$row[1];
  $password            = @$row[2];
  $host                = @$row[3];
  $port                = @$row[4];
  $local_dest    = @$row[5];
  $protocol            = @$row[6];
  $use_ssl             = @$row[7];
  $delete_mail         = @$row[8];
  $read_all            = @$row[9];

  if ($use_ssl eq "1") {
    if ($protocol eq "POP3") {$type = "SimplePOP3SSLRetriever"} elsif ($protocol eq "IMAP") { $type = "SimpleIMAPSSLRetriever" }
  } else {
    if ($protocol eq "POP3") {$type = "SimplePOP3Retriever"} elsif ($protocol eq "IMAP") { $type = "SimpleIMAPRetriever" }
  }

  $read_all_bool = $read_all eq "1" ? "true" : "false";
  $delete_bool = $delete_mail eq "1" ? "true" : "false";

  my $template = $run_dir . '/getmail.XXXXXXX';
  my $getmail_rc_file = File::Temp->new(TEMPLATE => $template);

  print $getmail_rc_file "[retriever]\n";
  print $getmail_rc_file "type = $type\n";
  print $getmail_rc_file "server = $host\n";
  print $getmail_rc_file "port = $port\n";
  print $getmail_rc_file "username = $user\n";
  print $getmail_rc_file "password = $password\n";
  print $getmail_rc_file "\n";
  print $getmail_rc_file "[destination]\n";
  print $getmail_rc_file "type = MDA_external\n";
  print $getmail_rc_file "path = /usr/local/bin/rspamc.py\n";
  print $getmail_rc_file "arguments = (\"$local_dest\",\"$host\")\n";
  #print $getmail_rc_file "arguments = (\"-t\",\"60\",\"-v\", \"-h\", \"rspamd\", \"--mime\", \"--exec\", \"/usr/local/libexec/dovecot/deliver -e -f %(sender) -d $local_dest\")\n";
  print $getmail_rc_file "user = vmail\n";
  print $getmail_rc_file "group = vmail\n";
  print $getmail_rc_file "\n";
  print $getmail_rc_file "[options]\n";
  print $getmail_rc_file "read_all = $read_all_bool\n";
  print $getmail_rc_file "delete = $delete_bool\n";
  print $getmail_rc_file "delivered_to = false\n";

  my $generated_cmds = [ "/usr/local/bin/getmail",
  "--getmaildir=$run_dir",
  "--rcfile=$getmail_rc_file"];

  try {
    $is_running = $dbh->prepare("UPDATE getmail SET is_running = 1 WHERE id = ?");
    $is_running->bind_param( 1, ${id} );
    $is_running->execute();

    run [@$generated_cmds], '&>', \my $stdout;

    $update = $dbh->prepare("UPDATE getmail SET returned_text = ? WHERE id = ?");
    $update->bind_param( 1, ${stdout} );
    $update->bind_param( 2, ${id} );
    $update->execute();
  } catch {
    $update = $dbh->prepare("UPDATE getmail SET returned_text = 'Could not start or finish getmail' WHERE id = ?");
    $update->bind_param( 1, ${id} );
    $update->execute();
  } finally {
    $update = $dbh->prepare("UPDATE getmail SET last_run = NOW(), is_running = 0 WHERE id = ?");
    $update->bind_param( 1, ${id} );
    $update->execute();
  };


}

$sth->finish();
$dbh->disconnect();

$lockmgr->unlock($lock_file);
