// Motion foundation: drives the active-status pulse ring, skeleton shimmer,
// and run-row entry animations with the Motion DOM API (pinned version loaded
// from the jsdelivr CDN). Everything is gated behind prefers-reduced-motion —
// when reduced motion is preferred, or Motion fails to load, the CSS keyframe
// fallbacks in styles.css keep the UI fully functional and static.
import { animate } from "https://cdn.jsdelivr.net/npm/motion@11.13.5/+esm";

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
const ROW_SELECTOR = 'tr[id^="run-"]';
const RING_CLASS = "status-dot-ring";

const RING_KEYFRAMES = {
  transform: ["scale(0.7)", "scale(1.6)"],
  opacity: [0.8, 0],
};
const RING_OPTIONS = { duration: 1.8, ease: "easeOut", repeat: Infinity };

const SHIMMER_KEYFRAMES = { backgroundPosition: ["-200% 0", "200% 0"] };
const SHIMMER_OPTIONS = { duration: 1.2, ease: "easeInOut", repeat: Infinity };

const ROW_KEYFRAMES = {
  opacity: [0, 1],
  transform: ["translateY(8px)", "translateY(0)"],
};
const ROW_OPTIONS = { duration: 0.3, ease: "easeOut" };

function addRing(dot) {
  if (dot.querySelector("." + RING_CLASS)) return;
  const ring = document.createElement("span");
  ring.className = RING_CLASS;
  ring.setAttribute("aria-hidden", "true");
  dot.appendChild(ring);
  animate(ring, RING_KEYFRAMES, RING_OPTIONS);
}

function setupPulseRings(root) {
  root.querySelectorAll(".status-dot-pulse").forEach(addRing);
}

function setupShimmers(root) {
  root.querySelectorAll(".skeleton").forEach((el) => {
    if (el.dataset.motionShimmer) return;
    el.dataset.motionShimmer = "1";
    animate(el, SHIMMER_KEYFRAMES, SHIMMER_OPTIONS);
  });
}

function findRows(node) {
  if (node.nodeType !== Node.ELEMENT_NODE) return [];
  if (node.matches && node.matches(ROW_SELECTOR)) return [node];
  return Array.from(node.querySelectorAll ? node.querySelectorAll(ROW_SELECTOR) : []);
}

let observedTable = null;

function observeRows() {
  const table = document.getElementById("runs-table-body");
  if (!table || table === observedTable) return;
  observedTable = table;
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        findRows(node).forEach((row) => animate(row, ROW_KEYFRAMES, ROW_OPTIONS));
      }
    }
  }).observe(table, { childList: true, subtree: true });
}

function enableMotion() {
  document.documentElement.classList.add("motion-ready");
}

function init() {
  if (REDUCED.matches) return;
  enableMotion();
  setupPulseRings(document);
  setupShimmers(document);
  observeRows();
}

// hx-boost navigation (Part 3) swaps <body> without reloading the module, so
// re-attach effects to freshly swapped content here.
function onHtmxLoad(event) {
  if (REDUCED.matches) return;
  const root = event.detail && event.detail.elt ? event.detail.elt : document;
  enableMotion();
  setupPulseRings(root);
  setupShimmers(root);
  observeRows();
}

// Module scripts execute after the document is parsed.
init();
document.addEventListener("htmx:load", onHtmxLoad);

// hx-boost history restore swaps cached HTML back in without a load event;
// re-attach effects to the restored document as well.
document.addEventListener("htmx:restored", onHtmxLoad);
