<?php

/**
 * Report the files that vendor/composer/autoload_files.php requires but that
 * do not exist; the autoloader fails on every request until they do. Runs
 * inside the web container, read from stdin:
 *
 *   php --
 *
 * Prints one path per missing file, relative to MediaWiki's core directory
 * when it lies inside it, and exits 1 if any are missing.
 */

$mwHome = rtrim(getenv('MW_HOME') ?: '/var/www/mediawiki/w', '/');
$list = "$mwHome/vendor/composer/autoload_files.php";
if (!is_file($list)) {
    exit(0);
}

function normalize(string $path): string {
    $parts = [];
    foreach (explode('/', $path) as $part) {
        if ($part === '..') {
            array_pop($parts);
        } elseif ($part !== '.' && $part !== '') {
            $parts[] = $part;
        }
    }
    return '/' . implode('/', $parts);
}

// The listed paths are built from __DIR__, which has its symlinks resolved.
$roots = array_unique([$mwHome, realpath($mwHome) ?: $mwHome]);
$missing = 0;
foreach ((array)(require $list) as $file) {
    if (!is_file($file)) {
        $path = normalize($file);
        foreach ($roots as $root) {
            if (str_starts_with($path, "$root/")) {
                $path = substr($path, strlen($root) + 1);
                break;
            }
        }
        echo $path, "\n";
        $missing++;
    }
}
exit($missing > 0 ? 1 : 0);
