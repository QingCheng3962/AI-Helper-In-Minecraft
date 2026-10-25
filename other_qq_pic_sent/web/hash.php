<?php
/**
 * 生成登录密码哈希（命令行运行）：
 *   php hash.php '你的密码'
 * 然后把输出填到环境变量 QQ_PIC_PASSWORD_HASH。
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

$password = $argv[1] ?? '';
if ($password === '') {
    fwrite(STDERR, "用法: php hash.php '你的密码'\n");
    exit(1);
}
echo password_hash($password, PASSWORD_DEFAULT), PHP_EOL;
