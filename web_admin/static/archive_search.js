// 档案查询页:零依赖局部刷新。
// 筛选/排序/翻页 → 拉取表格片段就地替换;点"详情"直接进入档案详情页。
// 全部走事件委托,挂在 document 上,DOM 片段被替换后无需重新绑定。
// 渐进增强:无 JS 时表单照常整页提交、链接照常跳转,功能不丢。
(function () {
  "use strict";

  var GRID_PANE = "archive-grid-pane";
  var COLS_KEY = "ff_archive_cols";
  var FETCH_HEADERS = { "X-Requested-With": "fetch" };
  var inputTimer = null;

  function gridPane() {
    return document.getElementById(GRID_PANE);
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

  // ── 批量删除:收集勾选行,构造一次性 POST 表单提交 ───────────────────────
  function submitBulkDelete(button) {
    var checked = document.querySelectorAll(".row-select:checked");
    if (!checked.length) {
      window.alert("请先勾选要删除的档案");
      return;
    }
    if (
      !window.confirm(
        "确认删除选中的 " + checked.length + " 份档案?此操作不可恢复。"
      )
    ) {
      return;
    }
    var form = document.createElement("form");
    form.method = "post";
    form.action = "/archives/bulk-delete";
    form.style.display = "none";
    var csrf = document.createElement("input");
    csrf.type = "hidden";
    csrf.name = "csrf_token";
    csrf.value = button.getAttribute("data-csrf") || "";
    form.appendChild(csrf);
    Array.prototype.forEach.call(checked, function (cb) {
      var inp = document.createElement("input");
      inp.type = "hidden";
      inp.name = "archive_id";
      inp.value = cb.value;
      form.appendChild(inp);
    });
    document.body.appendChild(form);
    form.submit();
  }

  // ── 事件委托(挂 document,跨片段替换有效)────────────────────────────────
  document.addEventListener("click", function (ev) {
    var bulk = ev.target.closest("[data-bulk-delete]");
    if (bulk) {
      ev.preventDefault();
      submitBulkDelete(bulk);
      return;
    }
    var nav = ev.target.closest("a[data-grid-nav]");
    if (nav && gridPane() && gridPane().contains(nav)) {
      ev.preventDefault();
      loadGrid(nav.getAttribute("href"), true);
    }
    // 行内"详情"链接是普通 <a>,直接跳转到档案详情页,无需 JS 拦截。
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
