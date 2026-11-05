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

    // 自动开启设置的下拉设置区（时间窗 + 端口范围）
    '.ckt-cfg{margin:1px 0 9px;padding:8px 9px;border-radius:9px;',
    'background:rgba(255,255,255,.028);border:1px solid rgba(255,255,255,.055)}',
    '.ckt-cfg-line{display:flex;align-items:center;gap:6px}',
    '.ckt-cfg-line+.ckt-cfg-line{margin-top:7px}',
    '.ckt-lb{flex:0 0 auto;width:34px;font-size:10.5px;color:#7c8497;white-space:nowrap}',
    '.ckt-time{width:65px;height:22px;padding:0 3px;border-radius:6px;border:1px solid rgba(255,255,255,.12);',
    'background:rgba(255,255,255,.05);color:#dfe5f0;font:11px/1 inherit;color-scheme:dark;outline:none;',
    'font-variant-numeric:tabular-nums}',
    '.ckt-time:focus{border-color:rgba(110,231,214,.55);background:rgba(110,231,214,.06)}',
    '.ckt-time::-webkit-calendar-picker-indicator{opacity:.45;cursor:pointer;width:9px;height:9px;margin-left:1px}',
    '.ckt-time::-webkit-calendar-picker-indicator:hover{opacity:.85}',
    '.ckt-dash{flex:0 0 auto;color:#5b6274;font-size:11px}',
    '.ckt-seg{display:flex;flex:0 0 auto;border-radius:7px;overflow:hidden;border:1px solid rgba(255,255,255,.12)}',
    '.ckt-seg-b{height:21px;padding:0 7px;border:none;background:transparent;color:#8b93a6;',
    'font:10.5px/1 inherit;cursor:pointer;transition:.15s;white-space:nowrap}',
    '.ckt-seg-b+.ckt-seg-b{border-left:1px solid rgba(255,255,255,.1)}',
    '.ckt-seg-b:hover:not(.on){background:rgba(255,255,255,.06);color:#cbd5e1}',
    '.ckt-seg-b.on{background:rgba(110,231,214,.16);color:#6ee7d6}',
    '.ckt-seg-b:disabled{opacity:.5;cursor:not-allowed}',
    '.ckt-sel{height:22px;padding:0 2px;border-radius:6px;border:1px solid rgba(255,255,255,.12);',
    'background:rgba(255,255,255,.05);color:#dfe5f0;font:11px/1 inherit;color-scheme:dark;',
    'outline:none;cursor:pointer;flex:0 0 auto}',
    '.ckt-sel:focus{border-color:rgba(110,231,214,.55)}',
    '.ckt-sel.off{display:none}',

    '.ckt-btns{display:flex;gap:7px;margin-top:11px}',
    '.ckt-btn{flex:1;height:31px;border-radius:9px;border:1px solid rgba(255,255,255,.10);',
    'background:rgba(255,255,255,.045);color:#dfe5f0;font:12px/1 inherit;cursor:pointer;transition:.15s}',
    '.ckt-btn:hover{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.18)}',
    '.ckt-btn.ckt-danger{color:#f3b0b0;border-color:rgba(239,68,68,.34)}',
    '.ckt-btn.ckt-danger:hover{background:rgba(239,68,68,.18);border-color:rgba(239,68,68,.45);color:#fca5a5}',
    '.ckt-foot{margin-top:10px;font-size:10px;color:#575e70;line-height:1.6;word-break:break-all;overflow:hidden}',
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
  var swAutostart = row('开机自启', '登录系统后自动在托盘运行');

  // 自动开启设置：在所设时间窗内启动时检查所选端口，有没开的就开（规则在 autoport.py）
  var autoRow = (function () {
    var r = h('div', 'ckt-row');
    var txt = h('div', 'ckt-txt');
    txt.appendChild(h('div', 'ckt-t1', '自动开启设置'));
    var sub = h('div', 'ckt-t2', '在所设时间窗内启动时检查所选端口，有未开启的即开启');
    txt.appendChild(sub);
    r.appendChild(txt);
    var sw = h('button', 'ckt-sw');
    r.appendChild(sw);
    panel.appendChild(r);
    return { sub: sub, sw: sw };
  })();
  var autoTxt = autoRow.sub;
  var autoSw = autoRow.sw;

  // 自动开启设置的下拉区：① 时间窗 ② 端口范围（全部 / 单口）
  // 两行都「改完即存」，不需要额外保存按钮：这功能本身就是低频设置，
  // 多一个保存按钮反而容易忘了点，导致界面显示的设置和实际生效的不一致。
  var autoCfg = (function () {
    var box = h('div', 'ckt-cfg');

    // ① 时间窗
    var lineT = h('div', 'ckt-cfg-line');
    lineT.appendChild(h('span', 'ckt-lb', '时间窗'));
    var tStart = document.createElement('input');
    tStart.type = 'time';
    tStart.className = 'ckt-time';
    tStart.value = '07:00';
    tStart.title = '开始时间（含）';
    var tEnd = document.createElement('input');
    tEnd.type = 'time';
    tEnd.className = 'ckt-time';
    tEnd.value = '12:00';
    tEnd.title = '结束时间（含这一整分钟）';
    lineT.appendChild(tStart);
    lineT.appendChild(h('span', 'ckt-dash', '\u2014'));
    lineT.appendChild(tEnd);
    box.appendChild(lineT);

    // ② 端口范围：分段按钮明确区分「全部」和「指定单口」两种模式
    var lineP = h('div', 'ckt-cfg-line');
    lineP.appendChild(h('span', 'ckt-lb', '端口'));
    var seg = h('div', 'ckt-seg');
    var bAll = h('button', 'ckt-seg-b on', '全部充电口');
    var bOne = h('button', 'ckt-seg-b', '指定单口');
    seg.appendChild(bAll);
    seg.appendChild(bOne);
    lineP.appendChild(seg);
    var sel = document.createElement('select');
    sel.className = 'ckt-sel off';
    sel.title = '选择要处理的充电口';
    [['c1', 'C1'], ['c2', 'C2'], ['c3', 'C3'], ['a', 'A']].forEach(function (p) {
      var o = document.createElement('option');
      o.value = p[0];
      o.textContent = p[1];
      sel.appendChild(o);
    });
    lineP.appendChild(sel);
    box.appendChild(lineP);

    panel.appendChild(box);
    return { box: box, tStart: tStart, tEnd: tEnd, bAll: bAll, bOne: bOne, sel: sel };
  })();

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

  function paintAuto(ap) {
    if (!ap) { return; }
    var ph = ap.phase || 'idle';
    autoSw.classList.toggle('on', !!ap.enabled);
    if (ap.message) { autoTxt.textContent = ap.message; }
    autoTxt.style.color = ph === 'error' ? '#f09696' : (ph === 'done' ? '#8fd9cf' : '#6c7488');
    paintAutoCfg(ap);
  }

  // 把后端保存的时间窗 / 端口范围回填到控件上。
  // 只在「值确实不一样」时才写，避免每秒轮询把用户正在挑的时间冲掉。
  function paintAutoCfg(ap) {
    if (!ap) { return; }
    var active = document.activeElement;
    if (ap.start && active !== autoCfg.tStart && autoCfg.tStart.value !== ap.start) {
      autoCfg.tStart.value = ap.start;
    }
    if (ap.end && active !== autoCfg.tEnd && autoCfg.tEnd.value !== ap.end) {
      autoCfg.tEnd.value = ap.end;
    }
    var one = !!ap.ports && ap.ports !== 'all';
    autoCfg.bAll.classList.toggle('on', !one);
    autoCfg.bOne.classList.toggle('on', one);
    autoCfg.sel.classList.toggle('off', !one);
    if (one && autoCfg.sel.value !== ap.ports) { autoCfg.sel.value = ap.ports; }
  }

  // 改设置 = 立刻存。没有「保存」按钮：这功能本身是低频设置，
  // 多一个按钮反而容易漏点，界面显示的和实际生效的就会对不上。
  var cfgBusy = false;

  function saveWindow() {
    var a = api();
    if (!a || cfgBusy) { return; }
    var s = autoCfg.tStart.value;
    var e = autoCfg.tEnd.value;
    if (!s || !e) { return; }
    cfgBusy = true;
    a.set_auto_port_window(s, e).then(function (ap) {
      paintAuto(ap);
      if (ap && ap.ok === false) {
        say(ap.error || '时间窗没保存');
        if (ap.start) { autoCfg.tStart.value = ap.start; }
        if (ap.end) { autoCfg.tEnd.value = ap.end; }
        return;
      }
      say('时间窗已改为 ' + ((ap && ap.window) || (s + '–' + e)));
    }).catch(function () {
      say('时间窗保存失败，请看 manager.log');
    }).then(function () { cfgBusy = false; });
  }

  function saveScope(scope) {
    var a = api();
    if (!a || cfgBusy) { return; }
    cfgBusy = true;
    autoCfg.bAll.disabled = true;
    autoCfg.bOne.disabled = true;
    autoCfg.sel.disabled = true;
    a.set_auto_port_ports(scope).then(function (ap) {
      paintAuto(ap);
      say('端口范围：' + ((ap && ap.scope) || scope));
    }).catch(function () {
      say('端口范围保存失败，请看 manager.log');
    }).then(function () {
      cfgBusy = false;
      autoCfg.bAll.disabled = false;
      autoCfg.bOne.disabled = false;
      autoCfg.sel.disabled = false;
    });
  }

  autoCfg.tStart.addEventListener('change', saveWindow);
  autoCfg.tEnd.addEventListener('change', saveWindow);
  autoCfg.bAll.onclick = function () {
    if (autoCfg.bAll.classList.contains('on')) { return; }   // 已经是全部模式，不用重复存
    saveScope('all');
  };
  autoCfg.bOne.onclick = function () {
    // 先切界面状态再存：下拉框得先亮出来，用户才知道选中了哪个口
    autoCfg.bAll.classList.remove('on');
    autoCfg.bOne.classList.add('on');
    autoCfg.sel.classList.remove('off');
    saveScope(autoCfg.sel.value || 'c1');
  };
  autoCfg.sel.onchange = function () { saveScope(autoCfg.sel.value); };

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
    swAutostart.classList.toggle('on', !!s.autostart);
    if (s.dataDir) { foot.textContent = '数据目录：' + s.dataDir; }
    paintAuto(s.autoPort);
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

  autoSw.onclick = function () {
    var a = api();
    if (!a || busy) { return; }
    busy = true;
    autoSw.disabled = true;
    var want = !autoSw.classList.contains('on');
    a.set_auto_port(want).then(function (s) {
      paint(s);
      say(want ? '自动开启设置已开启' : '自动开启设置已关闭');
      // 开启时若在时间窗内会立刻补做一次，3 秒后再拉一次拿结果
      if (want) { setTimeout(refresh, 3000); }
    }).catch(function () {
      say('切换失败，请看日志');
    }).then(function () {
      busy = false;
      autoSw.disabled = false;
    });
  };

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

  // Esc 关闭
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && panel.classList.contains('open')) { openPanel(false); }
  });

  refresh();

  // 预览模式（无头截图用）：没有 js_api 就填一份样例数据、直接把面板展开，
  // 这样看到的就是真实的排版而不是一排「--」。
  if (preview && !api()) {
    paint({
      serviceReady: true, serviceRunning: true, total: 45.2,
      widgetRunning: true, autostart: false,
      version: '1.3.0',
      dataDir: 'C:\\Users\\xy309\\AppData\\Local\\CUKTECH\\data',
      autoPort: {
        enabled: true, phase: 'skipped', start: '07:00', end: '12:00', ports: 'all',
        window: '07:00–12:00', scope: '全部充电口', inWindow: false,
        message: '不在 07:00–12:00 时段（现在 21:04:59），本次不执行'
      }
    });
    openPanel(true);
  }
})();
