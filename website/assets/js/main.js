/* ===== 隙间 XiJian 官网交互 ===== */
(function () {
  "use strict";

  // 导航滚动状态
  var nav = document.getElementById("nav");
  function onScroll() {
    if (nav) {
      nav.classList.toggle("scrolled", window.scrollY > 12);
    }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // 移动端菜单
  var toggle = document.getElementById("navToggle");
  var links = document.getElementById("navLinks");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var open = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    links.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        links.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  // 复制 QQ 号
  var copyBtns = document.querySelectorAll(".copy-btn");
  copyBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.getAttribute("data-copy") || "";
      function done() {
        btn.classList.add("copied");
        btn.textContent = "已复制";
        setTimeout(function () {
          btn.classList.remove("copied");
          btn.textContent = "复制";
        }, 1800);
      }
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); done(); } catch (e) {}
        document.body.removeChild(ta);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else {
        fallback();
      }
    });
  });
})();
