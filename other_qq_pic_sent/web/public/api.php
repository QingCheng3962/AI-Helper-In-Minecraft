<?php
/**
 * 只读代理：浏览器 -> 本文件（带登录会话）-> B 上的 Python 中继。
 * Token 只在服务器端使用，绝不会出现在 HTML/JS 里。
 */
declare(strict_types=1);
require __DIR__ . '/../src/auth.php';

qqpic_require_login();
qqpic_secure_headers(true);
header('Content-Type: application/json; charset=utf-8');

if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    header('Allow: GET');
    echo json_encode(['ok' => false, 'msg' => 'method not allowed']);
    exit;
}

$since = filter_input(INPUT_GET, 'since', FILTER_VALIDATE_INT);
$limit = filter_input(INPUT_GET, 'limit', FILTER_VALIDATE_INT);
$since = ($since === false || $since === null) ? 0 : max(0, (int)$since);
$limit = ($limit === false || $limit === null) ? 50 : min(100, max(1, (int)$limit));

$cfg = qqpic_config();
if ($cfg['relay_token'] === '') {
    http_response_code(500);
    echo json_encode(['ok' => false, 'msg' => 'relay token 未配置']);
    exit;
}

$url = $cfg['relay_base'] . '/api/qq/messages?' . http_build_query([
    'since' => $since,
    'limit' => $limit,
]);

$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => false,
    // 只允许 HTTP(S)，防止被配置成 file:// 等协议
    CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
    CURLOPT_REDIR_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
    CURLOPT_CONNECTTIMEOUT => 3,
    CURLOPT_TIMEOUT => (int)$cfg['api_timeout'],
    CURLOPT_HTTPHEADER => [
        'Accept: application/json',
        'X-Token: ' . $cfg['relay_token'],
    ],
    CURLOPT_USERAGENT => 'qqpic-web/1.0',
]);
$body = curl_exec($ch);
$code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
$err = curl_error($ch);
curl_close($ch);

if ($body === false || $code !== 200) {
    http_response_code(502);
    echo json_encode(['ok' => false, 'msg' => '中继不可用'], JSON_UNESCAPED_UNICODE);
    error_log('qqpic relay error: code=' . $code . ' err=' . $err);
    exit;
}
if (strlen($body) > (int)$cfg['api_max_bytes']) {
    http_response_code(502);
    echo json_encode(['ok' => false, 'msg' => '中继响应过大']);
    exit;
}

$data = json_decode($body, true, 64);
if (!is_array($data) || empty($data['ok']) || !isset($data['messages'])
        || !is_array($data['messages'])) {
    http_response_code(502);
    echo json_encode(['ok' => false, 'msg' => '中继返回格式错误']);
    exit;
}

$messages = [];
foreach ($data['messages'] as $msg) {
    if (!is_array($msg)) {
        continue;
    }
    $images = [];
    foreach (($msg['images'] ?? []) as $img) {
        if (!is_array($img)) {
            continue;
        }
        $raw = (string)($img['abs_url'] ?? $img['url'] ?? '');
        $safe = qqpic_safe_image_url($raw, $cfg);
        if ($safe !== null) {
            $images[] = [
                'url' => $safe,
                'name' => qqpic_clip((string)($img['name'] ?? ''), 120),
            ];
        }
    }
    $messages[] = [
        'id' => (int)($msg['id'] ?? 0),
        'time' => (float)($msg['time'] ?? 0),
        'group_name' => qqpic_clip((string)($msg['group_name'] ?? ''), 100),
        'sender' => qqpic_clip((string)($msg['sender'] ?? ''), 100),
        'text' => qqpic_clip((string)($msg['text'] ?? ''), 4000),
        'images' => $images,
    ];
}

$lastId = $messages ? (int)$messages[count($messages) - 1]['id'] : $since;
echo json_encode([
    'ok' => true,
    'last_id' => $lastId,
    'messages' => $messages,
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);

/** 只允许：本域相对 /pic_http/、配置的图片域名、或中继自己的地址（转成同源路径）。 */
function qqpic_safe_image_url(string $url, array $cfg): ?string
{
    if ($url === '') {
        return null;
    }
    if (strpos($url, '/pic_http/') === 0) {
        return $url;
    }
    $parts = parse_url($url);
    if (!is_array($parts)) {
        return null;
    }
    $scheme = strtolower((string)($parts['scheme'] ?? ''));
    if (!in_array($scheme, ['http', 'https'], true)) {
        return null;
    }
    if (!empty($parts['user']) || !empty($parts['pass'])) {
        return null;
    }
    $host = strtolower((string)($parts['host'] ?? ''));
    $path = (string)($parts['path'] ?? '');
    if (strpos($path, '/pic_http/') !== 0) {
        return null;
    }

    $publicHost = strtolower((string)(parse_url($cfg['public_base'], PHP_URL_HOST) ?: ''));
    if ($publicHost !== '' && $host === $publicHost) {
        return $url;
    }

    $relayHost = strtolower((string)(parse_url($cfg['relay_base'], PHP_URL_HOST) ?: ''));
    if ($relayHost !== '' && $host === $relayHost) {
        // 浏览器访问不到内网中继地址：改为同源路径，由 Nginx/中继转发
        return '/pic_http/' . rawurlencode(basename($path));
    }

    // 没有配置公网域名时，允许同源
    $selfHost = strtolower((string)($_SERVER['HTTP_HOST'] ?? ''));
    if ($publicHost === '' && $selfHost !== '' && $host === $selfHost) {
        return $url;
    }
    return null;
}

function qqpic_clip(string $value, int $max): string
{
    if (function_exists('mb_substr')) {
        return mb_substr($value, 0, $max, 'UTF-8');
    }
    return substr($value, 0, $max);
}
