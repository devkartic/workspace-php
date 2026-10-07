<?php

// Verify this project's PHP runtime without exposing full server configuration.
header('Content-Type: text/plain; charset=utf-8');

echo "php84 is running\n";
echo 'PHP version: ' . PHP_VERSION . "\n";
