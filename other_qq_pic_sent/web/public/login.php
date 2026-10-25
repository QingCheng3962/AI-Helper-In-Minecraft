<?php
declare(strict_types=1);
require __DIR__ . '/../src/auth.php';

qqpic_session_start();
qqpic_secure_headers();

if (qqpic_logged_in()) {
    header('Location: index.php', true, 303);
    exit;
}

$cfg = qqpic_config();
$error = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $ip = qqpic_client_ip();
    if (!qqpic_check_csrf($_POST['csrf'] ?? null)) {
        $error = '会话已过期，请刷新页面重试。';
    } elseif (!qqpic_login_allowed($ip)) {
        $error = '尝试次数过多，请 10 分钟后再试。';
    } elseif (empty($cfg['password_hash'])) {
        $error = '管理员尚未配置 QQ_PIC_PASSWORD_HASH。';
    } elseif (password_verify((string)($_POST['password'] ?? ''), $cfg['password_hash'])) {
        qqpic_login_succeeded($ip);
        session_regenerate_id(true);
        $_SESSION['qqpic_ok'] = true;
        unset($_SESSION['qqpic_csrf']);
        header('Location: index.php', true, 303);
        exit;
    } else {
        qqpic_login_failed($ip);
        $error = '密码错误。';
    }
}
$csrf = qqpic_csrf_token();
?>
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ 图片 · 登录</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body class="login-page">
  <main class="login-box">
    <h1>QQ 图片</h1>
    <p class="muted">仅限授权用户查看</p>
    <?php if ($error !== ''): ?>
      <p class="error" role="alert"><?= qqpic_h($error) ?></p>
    <?php endif; ?>
    <form method="post" autocomplete="off">
      <input type="hidden" name="csrf" value="<?= qqpic_h($csrf) ?>">
      <label for="password">密码</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" required autofocus>
      <button type="submit">登录</button>
    </form>
  </main>
</body>
</html>
