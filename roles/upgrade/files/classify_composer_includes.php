<?php

/**
 * Report which merge-plugin includes of config/composer.local.json the web
 * container's image ignores. Runs inside the container, read from stdin:
 *
 *   php -- <base64 of a JSON list of include patterns>
 *
 * Prints one JSON object. {"supported": false} when the image does not keep
 * its own bundled list (no composer.bundled.json), so the file is still the
 * whole list. Otherwise {"supported": true, "drop": [...]}, the patterns that
 * match no operator file, or {"supported": true, "error": "..."}.
 *
 * Each pattern is run alone through the image's composer-local.php with no
 * bundled list; the pattern is the operator's exactly when the image keeps
 * an include for it.
 */

$mwHome = rtrim(getenv('MW_HOME') ?: '/var/www/mediawiki/w', '/');
$script = "$mwHome/maintenance/composer-local.php";
if (!is_file("$mwHome/composer.bundled.json") || !is_file($script)) {
    echo json_encode(['supported' => false]), "\n";
    exit(0);
}

function finish(array $result, ?string $tmp): void {
    if ($tmp !== null) {
        array_map('unlink', glob("$tmp/*") ?: []);
        rmdir($tmp);
    }
    echo json_encode(['supported' => true] + $result, JSON_UNESCAPED_SLASHES), "\n";
    exit(0);
}

$patterns = json_decode(base64_decode($argv[1] ?? '', true) ?: 'null', true);
if (!is_array($patterns) || !array_is_list($patterns)) {
    finish(['error' => 'the include list could not be decoded'], null);
}

$tmp = sys_get_temp_dir() . '/canasta-composer-tidy-' . bin2hex(random_bytes(8));
if (!mkdir($tmp, 0700)) {
    finish(['error' => "cannot create $tmp"], null);
}

$drop = [];
foreach ($patterns as $pattern) {
    if (!is_string($pattern)) {
        continue;
    }
    $operator = ['extra' => ['merge-plugin' => ['include' => [$pattern]]]];
    file_put_contents("$tmp/operator.json", json_encode($operator, JSON_UNESCAPED_SLASHES));
    @unlink("$tmp/output.json");
    $output = [];
    $command = implode(' ', array_map('escapeshellarg', [
        PHP_BINARY, $script, $mwHome, "$tmp/no-bundled.json", "$tmp/operator.json", "$tmp/output.json",
    ])) . ' 2>&1';
    exec($command, $output, $status);
    $effective = $status === 0 && is_file("$tmp/output.json")
        ? json_decode(file_get_contents("$tmp/output.json"), true)
        : null;
    if (!is_array($effective)) {
        finish(['error' => "composer-local.php failed on $pattern (exit $status): "
            . implode(' ', $output)], $tmp);
    }
    if (!($effective['extra']['merge-plugin']['include'] ?? [])) {
        $drop[] = $pattern;
    }
}
finish(['drop' => $drop], $tmp);
