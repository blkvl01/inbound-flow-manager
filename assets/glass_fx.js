/* Liquid-glass support:
   1) Injects the SVG displacement filters used by the edge-refraction layer
      (.lg-l1) via backdrop-filter: url(#flow-glass-distort). Done once.
   2) Delegated click handler that flips the KPI sub-cards between the kg face
      and the ULD / PLT kg breakdown face (toggles .is-split).
   Everything degrades gracefully: if a browser ignores SVG filters inside
   backdrop-filter, only the edge refraction is lost; the rest of the glass
   (specular sheen, chromatic edge, blur) keeps working. */
(function () {
  var SVG_DEFS_ID = "flow-glass-svg-defs";

  function injectSvg() {
    if (document.getElementById(SVG_DEFS_ID)) return;
    var holder = document.createElement("div");
    holder.id = SVG_DEFS_ID;
    holder.setAttribute("aria-hidden", "true");
    holder.style.cssText =
      "position:absolute;width:0;height:0;overflow:hidden;pointer-events:none;";
    holder.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0">' +
      "<defs>" +
      '<filter id="flow-glass-distort" x="-25%" y="-25%" width="150%" height="150%" color-interpolation-filters="sRGB">' +
      '<feTurbulence type="fractalNoise" baseFrequency="0.010 0.013" numOctaves="2" seed="11" result="noise"/>' +
      '<feGaussianBlur in="noise" stdDeviation="1.2" result="softNoise"/>' +
      '<feDisplacementMap in="SourceGraphic" in2="softNoise" scale="22" xChannelSelector="R" yChannelSelector="G"/>' +
      "</filter>" +
      '<filter id="flow-glass-distort-soft" x="-20%" y="-20%" width="140%" height="140%" color-interpolation-filters="sRGB">' +
      '<feTurbulence type="fractalNoise" baseFrequency="0.013 0.017" numOctaves="2" seed="4" result="noise"/>' +
      '<feGaussianBlur in="noise" stdDeviation="1" result="softNoise"/>' +
      '<feDisplacementMap in="SourceGraphic" in2="softNoise" scale="11" xChannelSelector="R" yChannelSelector="G"/>' +
      "</filter>" +
      "</defs></svg>";
    (document.body || document.documentElement).appendChild(holder);
  }

  function onClick(ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    var card = t.closest(".kpi-sub-card");
    if (!card) return;
    card.classList.toggle("is-split");
  }

  function attach() {
    injectSvg();
    document.addEventListener("click", onClick, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
