(function () {
  "use strict";

  // Every selector/class checked below is copied from Homepage's actual
  // component source (gethomepage/homepage, src/components/services/
  // {ping,status,item}.jsx and src/components/widgets/resources/{usage-bar,
  // resource}.jsx), not guessed from the rendered page — Homepage is
  // client-rendered, so curling it only returns a JSON props blob.

  // ---- Time-of-day greeting ---------------------------------------------
  function greetingPrefix(hour) {
    if (hour < 5) return "Still up";
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    if (hour < 22) return "Good evening";
    return "Still up";
  }

  function applyGreeting() {
    var el = document.querySelector(".information-widget-greeting span");
    if (!el) return;
    var text = greetingPrefix(new Date().getHours()) + ", Voytech";
    if (el.textContent !== text) el.textContent = text;
  }

  // ---- Host resource bars (CPU/mem/disk) ---------------------------------
  // usage-bar.jsx renders the live percentage only as an inline
  // style.width on the fill div — there's no class to key off, so this
  // reads that width back out and escalates color at real thresholds.
  function colorizeResourceBars() {
    document.querySelectorAll(".resource-usage").forEach(function (bar) {
      var fill = bar.firstElementChild;
      if (!fill) return;
      var pct = parseFloat(fill.style.width) || 0;
      bar.classList.toggle("is-warning", pct >= 70 && pct < 90);
      bar.classList.toggle("is-danger", pct >= 90);
    });
  }

  // ---- Per-container CPU highlight ---------------------------------------
  // Homepage has a native `widget.highlight` threshold feature (see
  // utils/highlights.js), but it's only wired into named integration
  // widgets — the automatic per-container stats block (shown here because
  // every service in services.yaml sets showStats+container+server) is
  // built by item.jsx as a synthetic widget with no `type`/`highlight`
  // forwarded to it, so that config path is a no-op for it. This reads
  // the same real, already-rendered CPU percentage directly instead.
  // docker/component.jsx always renders the CPU block first when present,
  // so ":first-child" reliably means CPU, not mem/rx/tx.
  function colorizeContainerCpu() {
    document.querySelectorAll(".service-stats .service-block:first-child").forEach(function (block) {
      var valueEl = block.firstElementChild;
      var pct = valueEl ? parseFloat(valueEl.textContent) : NaN;
      if (Number.isNaN(pct)) {
        block.removeAttribute("data-fednet-level");
      } else if (pct >= 90) {
        block.setAttribute("data-fednet-level", "danger");
      } else if (pct >= 70) {
        block.setAttribute("data-fednet-level", "warn");
      } else {
        block.removeAttribute("data-fednet-level");
      }
    });
  }

  // ---- Fleet status strip -------------------------------------------------
  // Aggregates the ping/container-health dots Homepage already renders per
  // service into one glance-able strip. Colors come straight from
  // ping.jsx/status.jsx's own Tailwind classes (bg-emerald-500 = up/
  // healthy, bg-rose-500 = down/error, bg-orange-400 = unhealthy/partial,
  // bg-blue-500 = container starting). A service with neither `ping` nor
  // `container` configured has no dot at all and is correctly left out of
  // the count, rather than being guessed at.
  var SEVERITY = { down: 3, warning: 2, starting: 1, healthy: 0 };

  function classifyDot(el) {
    var c = el.className || "";
    if (c.indexOf("bg-emerald-500") !== -1) return "healthy";
    if (c.indexOf("bg-rose-500") !== -1) return "down";
    if (c.indexOf("bg-orange-400") !== -1) return "warning";
    if (c.indexOf("bg-blue-500") !== -1) return "starting";
    return null; // no data yet, or the "unknown" default (bg-black/bg-white)
  }

  function worstLevel(serviceEl) {
    var dots = serviceEl.querySelectorAll(".ping-status > div, .docker-status > div");
    var worst = null;
    for (var i = 0; i < dots.length; i++) {
      var level = classifyDot(dots[i]);
      if (level && (worst === null || SEVERITY[level] > SEVERITY[worst])) worst = level;
    }
    return worst;
  }

  // Built with createElement/textContent rather than innerHTML — every
  // piece here is a static label, but there's no reason to parse HTML for
  // it at all.
  function buildFleetItem(dotClass, countAttr, suffixText, startsHidden) {
    var item = document.createElement("span");
    item.className = "fednet-fleet-item";
    if (startsHidden) {
      item.setAttribute("data-fednet-attention-wrap", "");
      item.hidden = true;
    }

    var dot = document.createElement("span");
    dot.className = "fednet-fleet-dot " + dotClass;
    item.appendChild(dot);

    var count = document.createElement("span");
    count.className = "fednet-fleet-count";
    count.setAttribute(countAttr, "");
    count.textContent = "0";
    item.appendChild(count);

    item.appendChild(document.createTextNode(" " + suffixText));
    return item;
  }

  function ensureFleetStrip() {
    var existing = document.getElementById("fednet-fleet-status");
    if (existing) return existing;
    var anchor = document.getElementById("information-widgets");
    if (!anchor) return null;

    var strip = document.createElement("div");
    strip.id = "fednet-fleet-status";
    strip.appendChild(buildFleetItem("is-healthy", "data-fednet-healthy", "healthy", false));
    strip.appendChild(buildFleetItem("is-warning", "data-fednet-attention", "need attention", true));

    var updated = document.createElement("span");
    updated.className = "fednet-fleet-updated";
    updated.setAttribute("data-fednet-updated", "");
    strip.appendChild(updated);

    anchor.insertAdjacentElement("afterend", strip);
    return strip;
  }

  function scanFleet() {
    var services = document.querySelectorAll("#layout-groups li.service, #services li.service");
    var healthy = 0;
    var attention = 0;
    var tracked = 0;

    services.forEach(function (li) {
      var level = worstLevel(li);
      if (level === null) return;
      tracked += 1;
      if (level === "down" || level === "warning") attention += 1;
      else healthy += 1; // healthy or transiently "starting"
    });

    // Nothing resolved yet (first paint, before SWR responses land) —
    // don't show a misleading "0 healthy".
    if (tracked === 0) return;

    var strip = ensureFleetStrip();
    if (!strip) return;

    strip.querySelector("[data-fednet-healthy]").textContent = String(healthy);

    var attentionWrap = strip.querySelector("[data-fednet-attention-wrap]");
    if (attention > 0) {
      strip.querySelector("[data-fednet-attention]").textContent = String(attention);
      attentionWrap.hidden = false;
    } else {
      attentionWrap.hidden = true;
    }

    strip.querySelector("[data-fednet-updated]").textContent =
      "checked " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  // ---- Wire it all up ------------------------------------------------------
  function runEnhancements() {
    applyGreeting();
    colorizeResourceBars();
    colorizeContainerCpu();
    scanFleet();
  }

  runEnhancements();

  if (window.MutationObserver) {
    var scheduled = false;
    var scheduleRun = function () {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(function () {
        scheduled = false;
        runEnhancements();
      });
    };

    new MutationObserver(scheduleRun).observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["style", "class"],
      characterData: true,
    });
  }
})();
