<?php
/**
 * 会话 / CSRF / 登录限速 / 安全响应头。
 * 只使用 PHP 标准库，不依赖数据库。
 */
declare(strict_types=1);

function qqpic_config(): array
{
    static $config = null;
    if ($config === null) {
        $config = require __DIR__ . '/config.php';
    }
    return $config;
}

function qqpic_session_start(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }
    $cfg = qqpic_config();
    session_name($cfg['session_name']);
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'secure' => !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off',
        'httponly' => true,
        'samesite' => 'Strict',
    ]);
    session_start();
}

function qqpic_secure_headers(bool $api = false): void
{
    header('X-Content-Type-Options: nosniff');
    header('X-Frame-Options: DENY');
    header('Referrer-Policy: no-referrer');
    header('Permissions-Policy: geolocation=(), microphone=(), camera=()');
    if ($api) {
        header('Cache-Control: no-store');
    }
    header("Content-Security-Policy: default-src 'self'; img-src 'self' https: data:; "
        . "script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
        . "base-uri 'none'; form-action 'self'");
}

function qqpic_logged_in(): bool
{
    qqpic_session_start();
    return !empty($_SESSION['qqpic_ok']);
}

function qqpic_require_login(): void
{
    if (!qqpic_logged_in()) {
        header('Location: login.php', true, 303);
        exit;
    }
}

function qqpic_csrf_token(): string
{
    qqpic_session_start();
    if (empty($_SESSION['qqpic_csrf'])) {
        $_SESSION['qqpic_csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['qqpic_csrf'];
}

function qqpic_check_csrf(?string $token): bool
{
    qqpic_session_start();
    return is_string($token) && !empty($_SESSION['qqpic_csrf'])
        && hash_equals($_SESSION['qqpic_csrf'], $token);
}

function qqpic_client_ip(): string
{
    // 默认不信任 X-Forwarded-For；如果前面有可信反代，在这里显式处理。
    return (string)($_SERVER['REMOTE_ADDR'] ?? 'unknown');
}

function qqpic_login_allowed(string $ip): bool
{
    $cfg = qqpic_config();
    $state = qqpic_load_login_state();
    $now = time();
    $row = $state[$ip] ?? ['fails' => 0, 'first' => $now];
    if ($now - (int)$row['first'] > (int)$cfg['login_window']) {
        return true;
    }
    return (int)$row['fails'] < (int)$cfg['login_max_fails'];
}

function qqpic_login_failed(string $ip): void
{
    $cfg = qqpic_config();
    $state = qqpic_load_login_state();
    $now = time();
    $row = $state[$ip] ?? ['fails' => 0, 'first' => $now];
    if ($now - (int)$row['first'] > (int)$cfg['login_window']) {
        $row = ['fails' => 0, 'first' => $now];
    }
    $row['fails'] = (int)$row['fails'] + 1;
    $state[$ip] = $row;
    qqpic_save_login_state($state);
}

function qqpic_login_succeeded(string $ip): void
{
    $state = qqpic_load_login_state();
    unset($state[$ip]);
    qqpic_save_login_state($state);
}

function qqpic_load_login_state(): array
{
    $cfg = qqpic_config();
    $fp = @fopen($cfg['login_lock_file'], 'c+');
    if ($fp === false) {
        return [];
    }
    try {
        flock($fp, LOCK_SH);
        $raw = stream_get_contents($fp);
        $data = json_decode($raw ?: '{}', true);
        return is_array($data) ? $data : [];
    } finally {
        flock($fp, LOCK_UN);
        fclose($fp);
    }
}

function qqpic_save_login_state(array $state): void
{
    $cfg = qqpic_config();
    $fp = @fopen($cfg['login_lock_file'], 'c+');
    if ($fp === false) {
        return;
    }
    try {
        flock($fp, LOCK_EX);
        ftruncate($fp, 0);
        rewind($fp);
        fwrite($fp, json_encode($state, JSON_UNESCAPED_UNICODE));
        fflush($fp);
    } finally {
        flock($fp, LOCK_UN);
        fclose($fp);
    }
}

function qqpic_h(?string $value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
