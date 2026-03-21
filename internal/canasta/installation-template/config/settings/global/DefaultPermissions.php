<?php
// Default permissions for wiki farms.
// This file makes all wikis private by default: only logged-in users can
// read pages, and anonymous users are blocked from editing.
//
// You can edit or delete this file to change the defaults for wikis on
// this wiki farm. Per-wiki overrides can be placed in
// config/settings/wikis/<wiki-id>/.
$wgGroupPermissions['*']['read'] = false;
$wgGroupPermissions['*']['edit'] = false;
