<?php
$path = '/sabredav';
set_include_path(get_include_path() . PATH_SEPARATOR . $path);

use Sabre\DAV;
use Sabre\DAV\Auth;

// The autoloader
require 'vendor/autoload.php';

date_default_timezone_set('Europe/Berlin');

// Now we're creating a whole bunch of objects
$rootDirectory = new DAV\FS\Directory('/sabredav/public');

// The server object is responsible for making sense out of the WebDAV protocol
$server = new DAV\Server($rootDirectory);

// If your server is not on your webroot, make sure the following line has the
// correct information
// $server->setBaseUri('/webdav/server.php');

// The lock manager is reponsible for making sure users don't overwrite
// each others changes.
$lockBackend = new DAV\Locks\Backend\File('/sabredav/data/locks');
$lockPlugin = new DAV\Locks\Plugin($lockBackend);
$server->addPlugin($lockPlugin);

// This ensures that we get a pretty index in the browser, but it is
// optional.
$server->addPlugin(new DAV\Browser\Plugin());

$authBackend = new Auth\Backend\IMAP('{dovecot:993/imap/ssl/novalidate-cert}');
$authPlugin = new Auth\Plugin($authBackend);

// Adding the plugin to the server.
$server->addPlugin($authPlugin);

// All we need to do now, is to fire up the server
$server->exec();

?>
