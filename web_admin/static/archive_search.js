// 档案查询页:零依赖局部刷新。
// 筛选/排序/翻页 → 拉取表格片段替换左栏;点行 → 拉取详情片段替换右栏。
// 全部走事件委托,挂在 document 上,DOM 片段被替换后无需重新绑定。
// 渐进增强:无 JS 时表单照常整页提交、链接照常跳转,功能不丢。
(function () {
  "use strict";

  var GRID_PANE = "archive-grid-pane";
  var DETAIL_PANE = "detail-pane";
  var COLS_KEY = "ff_archive_cols";
  var FETCH_HEADERS = { "X-Requested-With": "fetch" };
  var inputTimer = null;

  function gridPane() {
    return document.getElementById(GRID_PANE);
  }
  function detailPane() {
    return document.getElementById(DETAIL_PANE);
  }
  function filterForm() {
    return document.getElementById("archive-grid-form");
  }

  // ── 列显示/隐藏 ─────────────────────────────────────────────────────────
  function loadColState() {
    try {
      return JSON.parse(window.localStorage.getItem(COLS_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }
  function saveColState(state) {
    try {
      window.localStorage.setItem(COLS_KEY, JSON.stringify(state));
    } catch (e) {
      /* 隐私模式忽略 */
    }
  }
  function applyColumn(key, visible) {
    var cells = document.querySelectorAll(".col-" + key);
    for (var i = 0; i < cells.length; i++) {
      cells[i].style.display = visible ? "" : "none";
    }
  }
  // 片段替换后重放列状态,并同步复选框勾选。
  function applyAllColumns() {
    var state = loadColState();
    var boxes = document.querySelectorAll("[data-col]");
    for (var i = 0; i < boxes.length; i++) {
      var key = boxes[i].getAttribute("data-col");
      if (Object.prototype.hasOwnProperty.call(state, key)) {
        boxes[i].checked = !!state[key];
        applyColumn(key, state[key]);
      }
    }
  }

  // ── 抓取并替换表格片段 ───────────────────────────────────────────────────
  function loadGrid(url, push) {
    var pane = gridPane();
    if (!pane) return;
    // 替换前记下焦点(列内筛选边打字边刷新时不丢光标)。
    var active = document.activeElement;
    var focusInfo = null;
    if (active && active.name && pane.contains(active)) {
      focusInfo = {
        name: active.name,
        start: active.selectionStart,
        end: active.selectionEnd,
      };
    }
    pane.classList.add("is-loading");
    fetch(url, { headers: FETCH_HEADERS, credentials: "same-origin" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("grid " + resp.status);
        return resp.text();
      })
      .then(function (html) {
        pane.innerHTML = html;
        pane.classList.remove("is-loading");
        applyAllColumns();
        if (focusInfo) {
          var el = pane.querySelector('[name="' + focusInfo.name + '"]');
          if (el) {
            el.focus();
            try {
              if (focusInfo.start != null) {
                el.setSelectionRange(focusInfo.start, focusInfo.end);
              }
            } catch (e) {
              /* select / number 等不支持 setSelectionRange,忽略 */
            }
          }
        }
        if (push !== false) {
          try {
            window.history.pushState({}, "", url);
          } catch (e) {
            /* 忽略 */
          }
        }
      })
      .catch(function () {
        // 局部刷新失败 → 退回整页加载,保证可用。
        window.location.href = url;
      });
  }

  function formUrl(form) {
    var params = new URLSearchParams(new FormData(form));
    var qs = params.toString();
    return form.getAttribute("action") + (qs ? "?" + qs : "");
  }

  // ── 主从详情 ─────────────────────────────────────────────────────────────
  function highlightRow(id) {
    var rows = document.querySelectorAll("tr.grid-row");
    for (var i = 0; i < rows.length; i++) {
      rows[i].classList.toggle(
        "is-selected",
        rows[i].getAttribute("data-archive-id") === String(id)
      );
    }
  }
  function syncSelectedParam(id) {
    // 同步 URL 与筛选表单隐藏域,使后续刷新保留选中态。
    try {
      var url = new URL(window.location.href);
      url.searchParams.set("selected", id);
      window.history.replaceState({}, "", url.toString());
    } catch (e) {
      /* 忽略 */
    }
    var form = filterForm();
    if (form) {
      var hidden = form.querySelector('input[name="selected"]');
      if (!hidden) {
        hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "selected";
        form.appendChild(hidden);
      }
      hidden.value = id;
    }
  }
  function openPanel(id) {
    var pane = detailPane();
    if (!pane) return;
    pane.classList.add("is-loading");
    fetch("/archives/" + encodeURIComponent(id) + "/panel", {
      headers: FETCH_HEADERS,
      credentials: "same-origin",
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("panel " + resp.status);
        return resp.text();
      })
      .then(function (html) {
        pane.innerHTML = html;
        pane.classList.remove("is-loading");
        highlightRow(id);
        syncSelectedParam(id);
      })
      .catch(function () {
        pane.classList.remove("is-loading");
        window.location.href = "/archives/" + encodeURIComponent(id);
      });
  }

  // ── 事件委托(挂 document,跨片段替换有效)────────────────────────────────
  document.addEventListener("click", function (ev) {
    var nav = ev.target.closest("a[data-grid-nav]");
    if (nav && gridPane() && gridPane().contains(nav)) {
      ev.preventDefault();
      loadGrid(nav.getAttribute("href"), true);
      return;
    }
    var row = ev.target.closest("tr.grid-row");
    if (row) {
      var link = ev.target.closest("a");
      if (link && !link.classList.contains("row-open")) return; // 放行其它真实链接
      var id = row.getAttribute("data-archive-id");
      if (id) {
        ev.preventDefault();
        openPanel(id);
      }
    }
  });

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (
      form.id === "archive-grid-form" ||
      form.classList.contains("grid-pager")
    ) {
      if (!gridPane()) return;
      ev.preventDefault();
      loadGrid(formUrl(form), true);
    }
  });

  document.addEventListener("change", function (ev) {
    var t = ev.target;
    if (t.matches("[data-col]")) {
      var state = loadColState();
      var key = t.getAttribute("data-col");
      state[key] = t.checked;
      saveColState(state);
      applyColumn(key, t.checked);
      return;
    }
    if (t.matches("select[data-autosubmit]")) {
      var form = filterForm();
      if (form && gridPane()) loadGrid(formUrl(form), true);
    }
  });

  document.addEventListener("input", function (ev) {
    if (!ev.target.matches("input[data-autosubmit]")) return;
    var form = filterForm();
    if (!form || !gridPane()) return;
    if (inputTimer) window.clearTimeout(inputTimer);
    inputTimer = window.setTimeout(function () {
      loadGrid(formUrl(form), true);
    }, 450);
  });

  // 浏览器前进/后退:按地址重载表格片段(不再压栈)。
  window.addEventListener("popstate", function () {
    if (gridPane()) loadGrid(window.location.href, false);
  });

  applyAllColumns();
})();
