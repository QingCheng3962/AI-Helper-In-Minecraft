<?php
/**
 * 安全配置：全部从环境变量读取，不要把 Token/密码写进代码或提交到仓库。
 *
 * 必填：
 *   QQ_RELAY_BASE       B 上中继地址，如 http://127.0.0.1:18765
 *   QQ_RELAY_TOKEN      中继的 token（A 的 webApiToken 也可以不同）
 *   QQ_PIC_PASSWORD_HASH  password_hash() 生成的密码哈希（见 hash.php）
 * 可选：
 *   QQ_PIC_BASE         图片公网前缀，如 https://pic.qingchengsep.top
 */
return [
    'relay_base' => rtrim(getenv('QQ_RELAY_BASE') ?: 'http://127.0.0.1:18765', '/'),
    'relay_token' => getenv('QQ_RELAY_TOKEN') ?: '',
    'public_base' => rtrim(getenv('QQ_PIC_BASE') ?: '', '/'),
    'password_hash' => getenv('QQ_PIC_PASSWORD_HASH') ?: '',
    'session_name' => 'qqpic_sid',
    'login_max_fails' => 5,
    'login_window' => 600,
    'login_lock_file' => sys_get_temp_dir() . '/qqpic_login.lock',
    'api_timeout' => 10,
    'api_max_bytes' => 2 * 1024 * 1024,
];
