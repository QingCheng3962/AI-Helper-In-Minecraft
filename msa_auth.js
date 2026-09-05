// Microsoft / 正版 account login helper (device-code flow).
//
// Authenticates with a Microsoft account that owns Minecraft and caches the
// tokens so that bot_engine.js can later log in silently (no browser needed).
//
// The token cache is shared with minecraft-protocol's default location
// (<minecraft folder>/nmp-cache/<username>), so the cache folder argument is
// optional and only needed if bot_engine.js was told to use a custom
// `profilesFolder`.
//
// Usage:
//   node msa_auth.js [username] [profilesFolder]

const path = require('path');
const { Authflow, Titles } = require('prismarine-auth');
const minecraftFolderPath = require('minecraft-folder-path');

const username = process.argv[2] || 'MSA_Account';
let cacheFolder = process.argv[3];
if (!cacheFolder) {
    cacheFolder = path.join(minecraftFolderPath, 'nmp-cache');
}

// Mirror the defaults used by minecraft-protocol's microsoft auth so the same
// cache is reused.
const options = {
    authTitle: Titles.MinecraftNintendoSwitch,
    deviceType: 'Nintendo',
    flow: 'live',
};

function showCode(data) {
    if (!data) return;
    const code = data.user_code || '';
    const uri = data.verification_uri || 'https://www.microsoft.com/link';
    console.log('');
    console.log('[正版登录] 请用浏览器打开下面的页面并输入代码完成授权：');
    console.log(`  页面: ${uri}`);
    console.log(`  代码: ${code}`);
    console.log(`  直达: http://microsoft.com/link?otc=${code}`);
    console.log('（等待授权完成后本窗口会自动继续…）');
    console.log('');
}

async function main() {
    const flow = new Authflow(username, cacheFolder, options, showCode);
    console.log(`[正版登录] 开始登录微软账户（账户标识: ${username}）…`);
    const { profile } = await flow.getMinecraftJavaToken({
        fetchProfile: true,
        fetchEntitlements: true,
        fetchCertificates: false,
    });
    console.log(`[正版登录] 登录成功: ${profile.name} (${profile.id})`);
    console.log(`[正版登录] 凭据已缓存于: ${cacheFolder}`);
    console.log('[正版登录] 现在可以回到 GUI 点击「连接」进入服务器。');
}

main().catch((e) => {
    console.error('[正版登录] 登录失败: ' + ((e && e.message) || e));
    process.exit(1);
});
