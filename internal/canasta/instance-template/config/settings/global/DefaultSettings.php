<?php
// Default settings for all wikis in the instance.
// This file makes all wikis private by default: only logged-in users can
// read pages, and anonymous users are blocked from editing or creating accounts.
//
// You can edit or delete this file to change the defaults for all wikis.
// Per-wiki overrides can be placed in config/settings/wikis/<wiki-id>/.
$wgGroupPermissions['*']['read'] = false;
$wgGroupPermissions['*']['edit'] = false;
$wgGroupPermissions['*']['createaccount'] = false;
