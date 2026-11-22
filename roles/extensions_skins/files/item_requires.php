<?php

/**
 * Print, as JSON, the name and the extensions and skins required by every
 * extension and skin installed in the web container, read from stdin:
 *
 *   php --
 *
 * Output: {"extensions": {"<dir>": {"name": ..., "extensions": [...],
 * "skins": [...]}}, "skins": {...}}. <dir> is the directory under
 * extensions/ or skins/, including a single sub-path such as
 * ConfirmEdit/ReCaptchaNoCaptcha.
 */

$mwHome = rtrim(getenv('MW_HOME') ?: '/var/www/mediawiki/w', '/');

function requiredNames($requires, string $key): array {
    if (!is_array($requires) || !isset($requires[$key]) || !is_array($requires[$key])) {
        return [];
    }
    return array_values(array_map('strval', array_keys($requires[$key])));
}

$out = ['extensions' => new stdClass(), 'skins' => new stdClass()];
foreach (['extensions' => 'extension.json', 'skins' => 'skin.json'] as $type => $file) {
    $base = "$mwHome/$type";
    $items = [];
    $paths = array_merge(
        glob("$base/*/$file") ?: [],
        glob("$base/*/*/$file") ?: []
    );
    foreach ($paths as $path) {
        $data = json_decode((string)@file_get_contents($path), true);
        if (!is_array($data)) {
            continue;
        }
        $dir = substr(dirname($path), strlen($base) + 1);
        $requires = $data['requires'] ?? [];
        $items[$dir] = [
            'name' => (string)($data['name'] ?? basename($dir)),
            'extensions' => requiredNames($requires, 'extensions'),
            'skins' => requiredNames($requires, 'skins'),
        ];
    }
    if ($items) {
        $out[$type] = $items;
    }
}
echo json_encode($out), "\n";
