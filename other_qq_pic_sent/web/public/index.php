<?php
declare(strict_types=1);
require __DIR__ . '/../src/auth.php';

qqpic_require_login();
qqpic_secure_headers();
$cfg = qqpic_config();
$imageBase = $cfg['public_base'] !== '' ? $cfg['public_base'] : '';
?>
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ 群图片</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <header class="topbar">
    <h1>QQ 群图片</h1>
    <div>
      <span id="qq-status" class="muted">加载中…</span>
      <a class="logout" href="logout.php">退出</a>
    </div>
  </header>

  <main id="qq-app" data-image-base="<?= qqpic_h($imageBase) ?>">
    <div id="qq-list" class="messages" aria-live="polite"></div>
    <p id="qq-empty" class="muted empty" hidden>还没有消息，去 QQ 群发一条试试。</p>
  </main>

  <script src="assets/app.js" defer></script>
</body>
</html>
