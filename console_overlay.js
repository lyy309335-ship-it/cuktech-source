/* CUKTECH 控制台 · 运行管理浮层
 *
 * 为什么是「注入脚本」而不是第二个窗口：
 *   pywebview 6.2.1 的 WinForms 后端在同一进程里创建第二个窗口时，那个窗口的
 *   WebView2 控制器起不来（日志里是 0x80070578 无效窗口句柄），load_url 会「成功」
 *   返回但页面永远不加载，evaluate_js 更是直接挂死（等 js_result_semaphore 等到超时）。
 *   所以整个应用只用**一个** WebView2 窗口，运行管理做成注入到控制台页面里的浮层。
 *
 * 这个文件由 manager.py 在每次页面 load 时 evaluate_js 注入。
 * 全程 try/catch 包裹：注入失败最多是没有齿轮按钮，托盘菜单仍然全功能。
 */
(function () {
  'use strict';

  if (window.__cuktechOverlay) { return; }        // 同一个页面只注入一次
  window.__cuktechOverlay = true;

  // 只在真正的控制台页面上挂浮层（bootstrap 页有自己的管理卡片）
  if (location.protocol !== 'http:') { return; }

  // 出错时把异常写到服务端日志（console.log）。浮层是「锦上添花」的东西，
  // 但它坏掉时页面本身看不出任何异常，没有这一层就只能靠猜。
  var errSent = 0;
  window.addEventListener('error', function (ev) {
    if (errSent++ > 3) { return; }
    try {
      fetch('/__overlay?p=' + encodeURIComponent(
        'JSERR ' + (ev.message || '?') + ' @' + (ev.filename || '') + ':' + ev.lineno
      )).catch(function () {});
    } catch (e) {}
  });

  // 探针：纯 JS + fetch 打一个带参数的 GET，服务端会把它打进 console.log。
  // 这是「页面脚本真的跑起来了」最硬的证据，且完全绕开 js_api 那一层。
  try {
    fetch('/__overlay?p=' + encodeURIComponent(
      location.href + ' | ' + document.title + ' | api=' + (window.pywebview ? 1 : 0) +
      ' | vp=' + window.innerWidth + 'x' + window.innerHeight +
      ' dpr=' + (window.devicePixelRatio || 0) +
      ' vis=' + document.visibilityState +
      ' bodyH=' + (document.body ? document.body.scrollHeight : -1)
    )).catch(function () {});
  } catch (e) {}

  var Z = 2147483000;

  var css = [
    '.ckt-fab{position:fixed;right:16px;bottom:16px;z-index:' + Z + ';display:flex;align-items:center;gap:6px;',
    'height:36px;padding:0 13px;border-radius:18px;border:1px solid rgba(255,255,255,.12);',
    'background:rgba(23,26,33,.86);color:#cbd5e1;font:12.5px/1 "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;',
    'cursor:pointer;backdrop-filter:blur(8px);box-shadow:0 6px 20px rgba(0,0,0,.4);transition:.15s;user-select:none}',
    '.ckt-fab:hover{background:rgba(35,40,52,.94);color:#fff;border-color:rgba(110,231,214,.5)}',
    '.ckt-fab .ckt-dot{width:7px;height:7px;border-radius:50%;background:#39404e}',
    '.ckt-fab .ckt-dot.on{background:#6ee7d6;box-shadow:0 0 7px #6ee7d6}',

    '.ckt-panel{position:fixed;right:16px;bottom:60px;z-index:' + Z + ';width:306px;',
    'background:rgba(20,23,30,.97);border:1px solid rgba(255,255,255,.10);border-radius:15px;',
    'box-shadow:0 18px 50px rgba(0,0,0,.55);backdrop-filter:blur(14px);',
    'font:13px/1.5 "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;color:#e8ecf4;',
    'padding:14px 14px 12px;display:none;user-select:none}',
    '.ckt-panel.open{display:block}',

    '.ckt-hd{display:flex;align-items:center;gap:8px;margin-bottom:10px}',
    '.ckt-hd .ckt-logo{width:22px;height:22px;border-radius:7px;display:flex;align-items:center;justify-content:center;',
    'background:linear-gradient(135deg,#6ee7d6,#3b82f6)}',
    '.ckt-hd .ckt-logo svg{width:12px;height:12px;fill:#0f1116}',
    '.ckt-hd .ckt-t{font-size:13px;font-weight:600;flex:1}',
    '.ckt-hd .ckt-ver{font-size:10px;line-height:1;padding:3px 6px;border-radius:20px;',
    'color:#7c8497;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.06);',
    'font-variant-numeric:tabular-nums;letter-spacing:.2px;white-space:nowrap}',
    '.ckt-hd .ckt-x{width:22px;height:22px;border:none;border-radius:6px;background:transparent;color:#7c8497;',
    'font-size:15px;line-height:1;cursor:pointer}',
    '.ckt-hd .ckt-x:hover{background:rgba(255,255,255,.08);color:#fff}',

    '.ckt-stat{display:flex;align-items:baseline;justify-content:space-between;padding:9px 11px;border-radius:10px;',
    'background:rgba(110,231,214,.07);border:1px solid rgba(110,231,214,.14);margin-bottom:10px}',
    '.ckt-stat b{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.4px}',
    '.ckt-stat span{font-size:11px;color:#7c8497}',
    '.ckt-badge{font-size:10.5px;padding:2px 8px;border-radius:20px;background:rgba(255,255,255,.06);color:#8b93a6}',
    '.ckt-badge.on{background:rgba(110,231,214,.14);color:#6ee7d6}',

    '.ckt-row{display:flex;align-items:center;gap:10px;padding:8px 2px}',
    '.ckt-row+.ckt-row{border-top:1px solid rgba(255,255,255,.055)}',
    '.ckt-row .ckt-txt{flex:1;min-width:0}',
    '.ckt-row .ckt-t1{font-size:12.5px}',
    '.ckt-row .ckt-t2{font-size:10.5px;color:#6c7488;margin-top:1px}',
    '.ckt-sw{width:40px;height:22px;border-radius:20px;border:none;padding:0;position:relative;',
    'background:#39404e;cursor:pointer;transition:background .18s;flex:0 0 auto}',
    '.ckt-sw::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;',
    'background:#fff;transition:transform .18s;box-shadow:0 1px 3px rgba(0,0,0,.4)}',
    '.ckt-sw.on{background:#6ee7d6}',
    '.ckt-sw.on::after{transform:translateX(18px)}',
    '.ckt-sw:disabled{opacity:.45;cursor:not-allowed}',

    // v1.6.0：「自动启动」连同它的时间窗/端口范围搬到了控制台网页的
    // 「设备设置 > 场景设置」，浮层里的 .ckt-cfg / .ckt-time / .ckt-seg / .ckt-sel
    // 一并删掉了，别再加回来 —— 同一个设置出现两个入口，用户改哪边都像没生效。

    '.ckt-btns{display:flex;gap:7px;margin-top:11px}',
    '.ckt-btn{flex:1;height:31px;border-radius:9px;border:1px solid rgba(255,255,255,.10);',
    'background:rgba(255,255,255,.045);color:#dfe5f0;font:12px/1 inherit;cursor:pointer;transition:.15s}',
    '.ckt-btn:hover{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.18)}',
    '.ckt-btn.ckt-danger{color:#f3b0b0;border-color:rgba(239,68,68,.34)}',
    '.ckt-btn.ckt-danger:hover{background:rgba(239,68,68,.18);border-color:rgba(239,68,68,.45);color:#fca5a5}',
    '.ckt-foot{margin-top:10px;font-size:10px;color:#575e70;line-height:1.6;word-break:break-all;overflow:hidden}',

    // v1.7.0 版本更新。单独包一层是因为它下面可能多一条进度条，
    // 直接塞 .ckt-row 会破坏 .ckt-row+.ckt-row 那条分隔线。
    '.ckt-upbox{border-top:1px solid rgba(255,255,255,.055)}',
    '.ckt-prog{display:none;height:4px;margin:0 2px 9px;border-radius:2px;',
    'background:rgba(255,255,255,.08);overflow:hidden}',
    '.ckt-prog.on{display:block}',
    '.ckt-prog i{display:block;height:100%;width:0;border-radius:2px;background:#6ee7d6;transition:width .25s}',
    '.ckt-mini{flex:0 0 auto;height:24px;padding:0 10px;border-radius:7px;white-space:nowrap;',
    'border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);color:#8b93a6;',
    'font:11.5px/1 inherit;cursor:pointer;transition:.15s}',
    '.ckt-mini:hover{background:rgba(255,255,255,.1);color:#cbd5e1}',
    '.ckt-mini.hot{border-color:rgba(110,231,214,.45);background:rgba(110,231,214,.14);color:#6ee7d6}',
    '.ckt-mini.hot:hover{background:rgba(110,231,214,.22)}',
    '.ckt-mini:disabled{opacity:.5;cursor:not-allowed}',
    // 面板里可点的邮箱文案（v2.0.0 追加）
    '.ckt-link{color:#8fb8ff!important;cursor:pointer;text-decoration:underline dotted}',
    '.ckt-link:hover{color:#b3cdff!important}',
    '.ckt-up-hi{color:#6ee7d6!important}',
    '.ckt-up-err{color:#f09696!important}',

    // v2.0.0 捐赠浮窗：盖在整页上（z 比面板高一层），点空白/×/Esc 都能关
    '.ckt-mask{position:fixed;inset:0;z-index:' + (Z + 5) + ';display:none;align-items:center;',
    'justify-content:center;background:rgba(8,10,14,.74);backdrop-filter:blur(3px);padding:16px}',
    '.ckt-mask.open{display:flex}',
    '.ckt-donate{width:360px;max-width:100%;max-height:100%;overflow:auto;background:#141720;',
    'border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:12px;',
    'box-shadow:0 24px 60px rgba(0,0,0,.6);font:13px/1.45 "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;',
    'color:#e8ecf4;user-select:none}',
    '.ckt-donate .ckt-dh{display:flex;align-items:center;gap:8px;margin-bottom:9px}',
    '.ckt-donate .ckt-dt{flex:1;font-size:12.5px;font-weight:600}',
    '.ckt-donate img{display:block;width:100%;border-radius:11px;background:#fff}',
    '.ckt-donate .ckt-tabs{display:flex;gap:6px;margin-bottom:9px}',
    '.ckt-donate .ckt-tab{flex:1;padding:6px 0;border-radius:9px;cursor:pointer;',
    'border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);color:#98a2b8;',
    'font:12px/1 inherit;transition:.15s}',
    '.ckt-donate .ckt-tab:hover{background:rgba(255,255,255,.09);color:#cfd6e4}',
    '.ckt-donate .ckt-tab.on{background:rgba(110,231,214,.16);border-color:rgba(110,231,214,.45);color:#6ee7d6}',
    '.ckt-donate .ckt-mail{color:#8fb8ff;text-decoration:none}',
    '.ckt-donate .ckt-mail:hover{text-decoration:underline}',
    '.ckt-donate .ckt-df{margin-top:9px;font-size:10.5px;color:#6c7488;text-align:center;line-height:1.6}',

    '.ckt-toast{position:fixed;right:16px;bottom:104px;z-index:' + Z + ';padding:7px 12px;border-radius:9px;',
    'background:rgba(23,26,33,.95);border:1px solid rgba(255,255,255,.12);color:#dfe5f0;',
    'font:12px/1.4 inherit;opacity:0;transform:translateY(6px);transition:.2s;pointer-events:none;max-width:280px}',
    '.ckt-toast.show{opacity:1;transform:translateY(0)}'
  ].join('');

  function h(tag, cls, txt) {
    var el = document.createElement(tag);
    if (cls) { el.className = cls; }
    if (txt != null) { el.textContent = txt; }
    return el;
  }

  function api() {
    return (window.pywebview && window.pywebview.api) || null;
  }

  // Bug 反馈邮箱（v2.0.0 追加）：面板一行 + 捐赠浮窗页脚都用它，放在这里免得顺序踩坑
  var FEEDBACK_MAIL = 'X3093354438@icloud.com';

  // 把一段文字塞进剪贴板。navigator.clipboard 在 http://127.0.0.1 属安全上下文，能用；
  // 万一被策略拦（老 WebView2 / 权限设置），退回 execCommand 那条老路。
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (ok, no) {
      try {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        var done = document.execCommand('copy');
        document.body.removeChild(ta);
        done ? ok() : no(new Error('execCommand copy failed'));
      } catch (e) { no(e); }
    });
  }

  // ---------------------------------------------------------------- 骨架
  var style = document.createElement('style');
  style.id = 'ckt-overlay-style';
  style.textContent = css;
  (document.head || document.documentElement).appendChild(style);

  // ?overlay=1：只给「用无头浏览器截图看样式」这种场景用，正常不会出现
  var preview = /[?&]overlay=1/.test(location.search);

  var fab = h('div', 'ckt-fab');
  // 先藏起来：等 pywebview 的 js_api 就绪才亮出来。普通浏览器里打开
  // http://127.0.0.1:8080 没有 js_api，这个按钮点了也没反应，干脆不显示。
  fab.style.display = preview ? '' : 'none';
  var dot = h('span', 'ckt-dot');
  fab.appendChild(dot);
  fab.appendChild(document.createTextNode('运行管理'));

  var panel = h('div', 'ckt-panel');

  var hd = h('div', 'ckt-hd');
  var logo = h('div', 'ckt-logo');
  logo.innerHTML = '<svg viewBox="0 0 24 24"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>';
  hd.appendChild(logo);
  hd.appendChild(h('div', 'ckt-t', 'CUKTECH 运行管理'));
  // 版本号：由 Python 侧 snapshot() 里的 version 填，页面本身不硬编码
  var ver = h('span', 'ckt-ver', 'v--');
  hd.appendChild(ver);
  var btnX = h('button', 'ckt-x', '\u00d7');
  hd.appendChild(btnX);
  panel.appendChild(hd);

  var stat = h('div', 'ckt-stat');
  var statLeft = document.createElement('div');
  var big = document.createElement('b');
  big.textContent = '--';
  var unit = h('span', null, ' W');
  statLeft.appendChild(big);
  statLeft.appendChild(unit);
  stat.appendChild(statLeft);
  var badge = h('span', 'ckt-badge', '检查中');
  stat.appendChild(badge);
  panel.appendChild(stat);

  function row(t1, t2) {
    var r = h('div', 'ckt-row');
    var txt = h('div', 'ckt-txt');
    txt.appendChild(h('div', 'ckt-t1', t1));
    txt.appendChild(h('div', 'ckt-t2', t2));
    r.appendChild(txt);
    var sw = h('button', 'ckt-sw');
    r.appendChild(sw);
    panel.appendChild(r);
    return sw;
  }

  var swService = row('采集服务', '蓝牙读取充电器数据，关掉后所有控制失效');
  var swWidget = row('桌面卡片', '常驻桌面的小窗，关掉可省内存');
  // v2.0.0：托盘图标上到底写不写瓦数。关掉只影响「读数」，采集和控制在页面上照旧。
  var swTrayPower = row('托盘功率', '在托盘图标上直接显示当前瓦数');
  var swAutostart = row('开机自启', '登录系统后自动在托盘运行');

  // v1.6.0：「自动开启设置」整块搬去了控制台网页的「设备设置 > 场景设置」，
  // 浮层里只留纯运行管理（服务 / 卡片 / 开机自启）。
  // 规则实现没动，还在 desktop/autoport.py，只是改了入口 —— 别再在这里加回来，
  // 同一个设置有两个入口，用户改哪边都像没生效。

  // ---------------- 版本更新（v1.7.0）
  // 进度靠 openPanel 里已有的 2 秒 refresh 带出来，这里不另起轮询。
  var up = (function () {
    var box = h('div', 'ckt-upbox');
    var r = h('div', 'ckt-row');
    var txt = h('div', 'ckt-txt');
    txt.appendChild(h('div', 'ckt-t1', '版本更新'));
    var t2 = h('div', 'ckt-t2', '当前 v--');
    txt.appendChild(t2);
    r.appendChild(txt);
    var act = h('button', 'ckt-mini', '检查');
    r.appendChild(act);
    var prog = h('div', 'ckt-prog');
    var fill = h('i');
    prog.appendChild(fill);
    box.appendChild(r);
    box.appendChild(prog);

    // v2.0.0：捐赠入口放在版本更新这块里（用户看「有没有新版」的地方顺便就能支持一下）
    var dr = h('div', 'ckt-row');
    var dtxt = h('div', 'ckt-txt');
    dtxt.appendChild(h('div', 'ckt-t1', '支持开发'));
    dtxt.appendChild(h('div', 'ckt-t2', '微信 / 支付宝扫码，随心打赏'));
    dr.appendChild(dtxt);
    var dbtn = h('button', 'ckt-mini', '捐赠');
    dr.appendChild(dbtn);
    box.appendChild(dr);

    // v2.0.0 追加：Bug 反馈邮箱就放在捐赠下面一行，不另开面板
    var fr = h('div', 'ckt-row');
    var ftxt = h('div', 'ckt-txt');
    ftxt.appendChild(h('div', 'ckt-t1', '问题反馈'));
    var fmail = h('div', 'ckt-t2 ckt-link', FEEDBACK_MAIL);
    fmail.title = '点一下复制邮箱';
    ftxt.appendChild(fmail);
    fr.appendChild(ftxt);
    var fbtn = h('button', 'ckt-mini', '复制');
    fr.appendChild(fbtn);
    box.appendChild(fr);

    fmail.onclick = fbtn.onclick = function () {
      copyText(FEEDBACK_MAIL).then(function () {
        say('邮箱已复制：' + FEEDBACK_MAIL);
      }).catch(function () {
        // 老 WebView / 剪贴板被策略拦：退化成选中，用户自己 Ctrl+C
        try {
          var r = document.createRange();
          r.selectNodeContents(fmail);
          var sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(r);
        } catch (e) {}
        say('复制失败，已选中，请 Ctrl+C');
      });
    };

    panel.appendChild(box);
    return { t2: t2, act: act, prog: prog, fill: fill, donate: dbtn };
  })();
  var lastUpdate = null;

  function paintUpdate(u) {
    if (!u) { return; }
    lastUpdate = u;
    var ph = u.phase || 'idle';
    var cur = 'v' + (u.current || '--');
    up.t2.className = 'ckt-t2';
    up.t2.title = '';
    up.act.className = 'ckt-mini';
    up.act.disabled = false;
    up.prog.classList.remove('on');

    if (ph === 'disabled') {
      // 没配更新源（源码运行 / 自己编译的包）：不给假按钮
      up.t2.textContent = cur + ' · 未配置更新源';
      up.act.textContent = '检查';
      up.act.disabled = true;
    } else if (ph === 'checking') {
      up.t2.textContent = cur + ' · 正在检查…';
      up.act.textContent = '检查中';
      up.act.disabled = true;
    } else if (ph === 'uptodate') {
      up.t2.textContent = cur + ' · 已是最新版本';
      up.act.textContent = '检查';
    } else if (ph === 'available') {
      // 当前版本在标题栏 already 有 badge，这里只报新版本 + 第一条改动摘要
      var n0 = (u.notes && u.notes.length) ? u.notes[0] : '';
      up.t2.textContent = '新版本 v' + (u.latest || '') + (n0 ? ' · ' + n0 : ' 可用');
      up.t2.className = 'ckt-t2 ckt-up-hi';
      up.t2.title = (u.notes || []).join('\n');
      up.act.textContent = '立即更新';
      up.act.className = 'ckt-mini hot';
    } else if (ph === 'downloading') {
      var pct = Math.round((u.progress || 0) * 100);
      up.t2.textContent = '正在下载安装包… ' + pct + '%';
      up.t2.className = 'ckt-t2 ckt-up-hi';
      up.act.textContent = '下载中';
      up.act.disabled = true;
      up.prog.classList.add('on');
      up.fill.style.width = pct + '%';
    } else if (ph === 'ready' || ph === 'installing') {
      up.t2.textContent = '正在安装，程序即将重启…';
      up.t2.className = 'ckt-t2 ckt-up-hi';
      up.act.textContent = '安装中';
      up.act.disabled = true;
      up.prog.classList.add('on');
      up.fill.style.width = '100%';
    } else if (ph === 'error') {
      up.t2.textContent = u.message || '检查更新失败';
      up.t2.className = 'ckt-t2 ckt-up-err';
      up.act.textContent = '重试';
    } else {
      up.t2.textContent = cur + ' · ' + (u.message || '尚未检查');
      up.act.textContent = '检查';
    }
  }

  up.act.onclick = function () {
    var a = api();
    if (!a) { say('请在桌面版里更新'); return; }
    var ph = lastUpdate ? lastUpdate.phase : 'idle';
    if (ph === 'available') {
      up.act.disabled = true;
      up.act.textContent = '准备中…';
      a.install_update().then(function (u) {
        paintUpdate(u);
        if (u && u.phase === 'error') { say(u.message || '更新失败'); }
      }).catch(function () {
        say('更新启动失败，请看 manager.log');
        up.act.disabled = false;
        up.act.textContent = '立即更新';
      });
      return;
    }
    up.act.disabled = true;
    up.act.textContent = '检查中';
    a.check_update(true).then(function (u) {
      paintUpdate(u);
      var p = u && u.phase;
      if (p === 'uptodate') { say('已是最新版本'); }
      else if (p === 'available') { say('发现新版本 v' + u.latest); }
      else if (p === 'error') { say(u.message || '检查失败'); }
      else if (p === 'disabled') { say('这个版本没配置更新源'); }
    }).catch(function () {
      say('检查失败，请看 manager.log');
      up.act.disabled = false;
      up.act.textContent = '检查';
    });
  };

  // ---------------- 捐赠浮窗（v2.0.0）
  // 第一次点才建 DOM：绝大多数人不会点，没必要每次注入都创建一堆节点。
  // 两个渠道（微信 / 支付宝）用两个页签切，同一张图位换 src，不堆两张长图。
  // 图片来源有两条路：
  //   1. /donate.jpg、/alipay.jpg —— 图片就躺在 web\ 目录里，控制台页面同源，浏览器自己缓存；
  //   2. js_api 的 data URI —— 窗口停在本地引导页（file:// 源）时相对路径取不到，
  //      由 Python 把 web\ 下的图读出来编成 data URI 兜底。
  var DONATE_KINDS = [
    { key: 'wechat', name: '微信', title: '微信扫码捐赠', src: '/donate.jpg', alt: '微信支付捐赠码', file: 'donate.jpg' },
    { key: 'alipay', name: '支付宝', title: '支付宝扫码捐赠', src: '/alipay.jpg', alt: '支付宝收款码', file: 'alipay.jpg' }
  ];
  var donateMask = null;
  var donateImg = null;
  var donateTitle = null;
  var donateTabs = {};
  var donateKind = 'wechat';
  var donateTried = {};   // 哪个渠道已经退过 data URI，避免来回打转

  function hideDonate() {
    if (donateMask) { donateMask.classList.remove('open'); }
  }

  function loadDonate(kind) {
    var item = null, i;
    for (i = 0; i < DONATE_KINDS.length; i++) {
      if (DONATE_KINDS[i].key === kind) { item = DONATE_KINDS[i]; }
    }
    if (!item || !donateImg) { return; }
    donateKind = kind;
    for (var k in donateTabs) {
      if (donateTabs.hasOwnProperty(k)) {
        donateTabs[k].className = 'ckt-tab' + (k === kind ? ' on' : '');
      }
    }
    donateTitle.textContent = item.title;
    donateImg.alt = item.alt;
    donateImg.src = item.src;          // 先走同源相对路径
  }

  function fallbackDonate(kind) {
    var a = api();
    if (donateTried[kind] || !a || !a.donate_image) {
      // 两条路都没图（打包漏了 web\ 下的图）：给一句人话，别留个破图标
      donateImg.style.display = 'none';
      if (!donateMask.querySelector('.ckt-df.err')) {
        var n = h('div', 'ckt-df err', '图片没加载出来 · 可到程序目录 web\\ 下查看');
        donateImg.parentNode.insertBefore(n, donateImg.nextSibling);
      }
      return;
    }
    donateTried[kind] = true;
    a.donate_image(kind).then(function (uri) {
      if (uri) {
        donateImg.style.display = '';
        donateImg.src = uri;
      }
    }).catch(function () {});
  }

  function showDonate() {
    if (!donateMask) {
      donateMask = h('div', 'ckt-mask');
      var card = h('div', 'ckt-donate');
      var bar = h('div', 'ckt-dh');
      donateTitle = h('div', 'ckt-dt', DONATE_KINDS[0].title);
      bar.appendChild(donateTitle);
      var bx = h('button', 'ckt-x', '\u00d7');
      bar.appendChild(bx);

      var tabs = h('div', 'ckt-tabs');
      DONATE_KINDS.forEach(function (it) {
        var t = h('button', 'ckt-tab', it.name);
        t.onclick = function () { loadDonate(it.key); };
        donateTabs[it.key] = t;
        tabs.appendChild(t);
      });

      donateImg = h('img');
      donateImg.onerror = function () { fallbackDonate(donateKind); };

      var dfoot = h('div', 'ckt-df');
      dfoot.appendChild(h('span', null, '感谢支持 · 图片可右键另存'));
      var dmail = h('a', 'ckt-mail', FEEDBACK_MAIL);
      dmail.href = 'mailto:' + FEEDBACK_MAIL + '?subject=CUKTECH%20问题反馈';
      dfoot.appendChild(h('br'));
      dfoot.appendChild(h('span', null, '问题反馈：'));
      dfoot.appendChild(dmail);

      card.appendChild(bar);
      card.appendChild(tabs);
      card.appendChild(donateImg);
      card.appendChild(dfoot);
      donateMask.appendChild(card);
      document.body.appendChild(donateMask);
      bx.onclick = hideDonate;
      donateMask.onclick = function (e) {
        if (e.target === donateMask) { hideDonate(); }
      };
    }
    donateMask.classList.add('open');
    loadDonate(donateKind);
  }

  up.donate.onclick = showDonate;

  var btns = h('div', 'ckt-btns');
  var btnData = h('button', 'ckt-btn', '数据目录');
  var btnQuit = h('button', 'ckt-btn ckt-danger', '退出');
  btns.appendChild(btnData);
  btns.appendChild(btnQuit);
  panel.appendChild(btns);

  var foot = h('div', 'ckt-foot', '数据目录：--');
  panel.appendChild(foot);

  var toast = h('div', 'ckt-toast');

  function mount() {
    document.body.appendChild(fab);
    document.body.appendChild(panel);
    document.body.appendChild(toast);
  }
  if (document.body) { mount(); } else { document.addEventListener('DOMContentLoaded', mount); }

  // ---------------------------------------------------------------- 状态
  var busy = false;
  var timer = null;

  function say(msg) {
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { toast.classList.remove('show'); }, 2200);
  }

  // v1.6.0：原来这里有 paintAuto / paintAutoCfg / saveWindow / saveScope，
  // 负责浮层里「自动开启设置」的开关、时间窗、端口范围。
  // 这块整建制搬去了控制台网页（web/index.html → 设备设置 → 场景设置），
  // 编辑逻辑也在那边用 fetch('/api/autoport') 实现，这里不再保留副本。

  function paint(s) {
    if (!s) { return; }
    if (s.version) { ver.textContent = 'v' + s.version; }
    var ready = !!s.serviceReady;
    var running = !!s.serviceRunning;
    big.textContent = ready ? Number(s.total || 0).toFixed(1) : '--';
    if (!running) { badge.className = 'ckt-badge'; badge.textContent = '服务已停止'; }
    else if (ready) { badge.className = 'ckt-badge on'; badge.textContent = '已连接'; dot.className = 'ckt-dot on'; }
    else { badge.className = 'ckt-badge'; badge.textContent = '连接中…'; dot.className = 'ckt-dot'; }
    if (ready) { dot.className = 'ckt-dot on'; }

    swService.classList.toggle('on', running);
    swWidget.classList.toggle('on', !!s.widgetRunning);
    // 老配置里没有这个键时按「开」兜底，和 Python 侧 load_conf 的默认值一致
    swTrayPower.classList.toggle('on', s.trayPower !== false);
    swAutostart.classList.toggle('on', !!s.autostart);
    if (s.dataDir) { foot.textContent = '数据目录：' + s.dataDir; }
    paintUpdate(s.update);
  }

  function refresh() {
    var a = api();
    if (!a || busy) { return; }
    a.get_state().then(paint).catch(function () {});
  }

  function toggle(sw, method, label) {
    var a = api();
    if (!a || busy) { return; }
    busy = true;
    sw.disabled = true;
    var want = !sw.classList.contains('on');
    a[method](want).then(function (s) {
      if (s && s.ok === false) { say(label + '切换失败，请看日志'); }
      paint(s);
    }).catch(function () {
      say(label + '切换失败');
    }).then(function () {
      busy = false;
      sw.disabled = false;
      refresh();
    });
  }

  swService.onclick = function () { toggle(swService, 'set_service', '采集服务'); };
  swWidget.onclick = function () { toggle(swWidget, 'set_widget', '桌面卡片'); };
  swTrayPower.onclick = function () { toggle(swTrayPower, 'set_tray_power', '托盘功率'); };
  swAutostart.onclick = function () { toggle(swAutostart, 'set_autostart', '开机自启'); };

  btnData.onclick = function () {
    var a = api();
    if (a) { a.open_data_dir(); }
  };
  btnQuit.onclick = function () {
    var a = api();
    if (a) { a.quit_app(); }
  };

  function openPanel(open) {
    var want = open == null ? !panel.classList.contains('open') : open;
    panel.classList.toggle('open', want);
    if (want) {
      refresh();
      if (!timer) { timer = setInterval(refresh, 2000); }
    } else if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  fab.onclick = function () { openPanel(); };
  btnX.onclick = function () { openPanel(false); };

  // 给主程序留个入口：托盘「运行管理」= 亮出窗口 + 展开浮层
  window.__cktOverlay = { open: openPanel, refresh: refresh };

  // 亮按钮 + 向主程序报到。
  //
  // 时机：pywebview 是在「导航完成」时才把 js_api 放进页面的，比我们这段脚本
  // 晚一点，所以不能同步判断有没有 api。桌面卡片那边用的是同一个信号
  // （window 上的 pywebviewready 事件），这里保持一致；再加一条兜底：
  // 万一事件已经过去了，直接看 api 在不在。
  function ready() {
    if (ready.done) { return; }
    ready.done = true;
    fab.style.display = '';
    var a = api();
    if (a && a.overlay_ready) {
      try { a.overlay_ready(location.href + ' | ' + document.title); } catch (e) {}
    }
    // 补一次取数：脚本执行时 api 往往还没注入，上面那句 refresh() 会直接空转，
    // 结果版本号一直停在占位的 v--，直到手动点开面板才被刷新。
    refresh();
  }

  // 用「轮询」而不是只听 pywebviewready 事件。
  // 实测 pywebview 6.2.1 的注入时机和页面导航完成有竞争：有时注入落在上一个文档里，
  // 事件早于本脚本注册监听就永远等不到，浮层就再也不会亮（日志里表现为没有
  // 「页面就绪（浮层回报）」这一行）。轮询对时序完全不敏感，只认「api 到底在不在」。
  var waitTries = 0;
  (function waitApi() {
    if (ready.done) { return; }
    if (api()) { ready(); return; }
    if (waitTries++ > 120) {           // 30 秒还没出现 = 真的没有 js_api
      if (!preview) {
        // 普通浏览器打开时没有 js_api，这块浮层点了也没用，整块撤掉
        try {
          fab.remove();
          panel.remove();
          style.remove();
          window.__cuktechOverlay = false;
          window.__cktOverlay = null;
        } catch (e) {}
      }
      return;
    }
    setTimeout(waitApi, 250);
  })();
  window.addEventListener('pywebviewready', ready, { once: true });

  // 6 秒还没拿到 js_api 就报一笔上去：只在真出问题时发一次请求，
  // 以后排查「齿轮按钮没出来」不用再靠猜。
  setTimeout(function () {
    if (ready.done) { return; }
    try {
      fetch('/__overlay?p=' + encodeURIComponent(
        'API-MISSING pw=' + (typeof window.pywebview) +
        ' api=' + (window.pywebview && window.pywebview.api ? 1 : 0) +
        ' tries=' + waitTries
      )).catch(function () {});
    } catch (e) {}
  }, 6000);

  // Esc 关闭（先关捐赠浮窗，再关面板）
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') { return; }
    if (donateMask && donateMask.classList.contains('open')) { hideDonate(); return; }
    if (panel.classList.contains('open')) { openPanel(false); }
  });

  refresh();

  // 预览模式（无头截图用）：没有 js_api 就填一份样例数据、直接把面板展开，
  // 这样看到的就是真实的排版而不是一排「--」。
  if (preview && !api()) {
    paint({
      serviceReady: true, serviceRunning: true, total: 45.2,
      widgetRunning: true, autostart: false, trayPower: true,
      version: '2.0.0',
      dataDir: 'C:\\Users\\xy309\\AppData\\Local\\CUKTECH\\data',
      update: {
        phase: 'available', current: '1.7.0', latest: '1.7.1',
        progress: 0, notes: ['修复窄窗口下设置项错位'], message: '发现新版本 v1.7.1'
      }
    });
    openPanel(true);
    // ?overlay=1&donate=1 直接把捐赠浮窗也展开，给「改完样式截一张图」用
    // &donate=alipay 额外切到支付宝页签，同样只为截图
    var dm = /[?&]donate=(1|wechat|alipay)/.exec(location.search);
    if (dm) {
      showDonate();
      if (dm[1] === 'alipay') { loadDonate('alipay'); }
    }
  }
})();
