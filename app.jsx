/* ============================================================
   App · React wires up the SVG map, edges, drawer, screener
   ============================================================ */

const { useState, useMemo, useCallback } = React;

/* ---------- Geometry helpers ---------- */
function curvePath(s, t, lift = 0.18) {
  const dx = t.x - s.x, dy = t.y - s.y;
  const mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2;
  const len = Math.sqrt(dx * dx + dy * dy);
  // perpendicular offset for the control point · bow upward for short, sideways for long
  const nx = -dy / (len || 1), ny = dx / (len || 1);
  const cx = mx + nx * len * lift, cy = my + ny * len * lift;
  return `M ${s.x} ${s.y} Q ${cx} ${cy} ${t.x} ${t.y}`;
}

/* Wave path · sinusoidal perpendicular perturbation along the s→t line.
   Used by the flow mode for "gas" - feels like compressible-fluid flow. */
function wavePath(s, t, amplitude = 4, frequency = 8) {
  const dx = t.x - s.x, dy = t.y - s.y;
  const len = Math.sqrt(dx * dx + dy * dy);
  if (len === 0) return `M ${s.x} ${s.y}`;
  const nx = -dy / len, ny = dx / len;
  const steps = Math.max(20, Math.floor(len / 8));
  let path = `M ${s.x} ${s.y}`;
  for (let i = 1; i <= steps; i++) {
    const u = i / steps;
    const x = s.x + dx * u;
    const y = s.y + dy * u;
    const off = amplitude * Math.sin(u * Math.PI * frequency);
    path += ` L ${(x + nx * off).toFixed(1)} ${(y + ny * off).toFixed(1)}`;
  }
  return path;
}

/* Zigzag path · triangular-wave perturbation. Used by "electrons" so the
   line reads like an electrical waveform. */
function zigzagPath(s, t, amplitude = 3, segLen = 16) {
  const dx = t.x - s.x, dy = t.y - s.y;
  const len = Math.sqrt(dx * dx + dy * dy);
  if (len === 0) return `M ${s.x} ${s.y}`;
  const nx = -dy / len, ny = dx / len;
  const steps = Math.max(8, Math.floor(len / segLen));
  let path = `M ${s.x} ${s.y}`;
  for (let i = 1; i <= steps; i++) {
    const u = i / steps;
    const x = s.x + dx * u;
    const y = s.y + dy * u;
    // alternate sign so each step pulls the path opposite the previous
    const sign = (i % 2 === 0) ? 1 : -1;
    const off = (i === steps) ? 0 : amplitude * sign;
    path += ` L ${(x + nx * off).toFixed(1)} ${(y + ny * off).toFixed(1)}`;
  }
  return path;
}

/* Offset-curve path · returns curvePath drawn between endpoints shifted by
   `offset` units perpendicular to the s→t line. Used by "products" to render
   double parallel pipelines. */
function offsetCurvePath(s, t, offset, lift = 0.10) {
  const dx = t.x - s.x, dy = t.y - s.y;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const nx = -dy / len, ny = dx / len;
  const s2 = { x: s.x + nx * offset, y: s.y + ny * offset };
  const t2 = { x: t.x + nx * offset, y: t.y + ny * offset };
  return curvePath(s2, t2, lift);
}

const DISTRICT_CENTROID = {
  ocean:      { x: 110, y: 470 },
  port:       { x: 350, y: 470 },
  industrial: { x: 580, y: 410 },
  oilfield:   { x: 760, y: 640 },
  equipment:  { x: 740, y: 405 },
  quarry:     { x: 1010, y: 320 },
  farmland:   { x: 1190, y: 500 },
  power:      { x: 950, y: 695 },
  nuclear:    { x: 1210, y: 690 },
  town:       { x: 670, y: 280 },
};

/* ---------------- Map header overlay ---------------- */
function MapTitle() {
  return (
    <div className="map-title">
      <div className="kicker">Atlas · Plate I</div>
      <h1><em>Energy Atlas</em></h1>
      <div className="scale">
        <span>0</span>
        <span className="bar"></span>
        <span>VALUE CHAIN</span>
      </div>
    </div>
  );
}

function Compass() {
  return (
    <svg className="compass" width="44" height="44" viewBox="0 0 44 44">
      <circle cx="22" cy="22" r="18" stroke="#4f5563" strokeWidth="0.6" fill="none"/>
      <circle cx="22" cy="22" r="1.5" fill="#4f5563"/>
      <path d="M 22 6 L 24 22 L 22 21 L 20 22 Z" fill="#d4af37"/>
      <path d="M 22 38 L 24 22 L 20 22 Z" fill="#4f5563"/>
      <text x="22" y="3" fontFamily="JetBrains Mono" fontSize="6" fill="#818796" textAnchor="middle" letterSpacing="0.1em">N</text>
    </svg>
  );
}

/* Info button + popover · explains each layer's meaning, computation,
   and how to frame thinking on it. Sits in the top-right near the compass. */
function InfoButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="info-btn" onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }} title="What am I looking at?">
        <span className="info-i">i</span>
        <span className="info-label">info</span>
      </button>
      {open && (
        <div className="info-panel" onClick={(e) => e.stopPropagation()}>
          <div className="info-panel-head">
            <h3><em>What you're looking at</em></h3>
            <button className="info-close" onClick={() => setOpen(false)}>✕</button>
          </div>

          <section>
            <div className="info-mode">Flow</div>
            <p className="info-summary">Physical commodity flow through the energy value chain.</p>
            <p className="info-detail"><strong>Computed:</strong> Edges are taxonomy-driven, not analysis-derived. Each line is a known commodity hand-off (e.g. E&amp;P → midstream → refiners → retail).</p>
            <p className="info-detail"><strong>What's shown:</strong> Color + style encodes the commodity class (amber crude, blue gas, orange products, pale dotted LNG, black dashed coal, teal long-dash nuclear, violet zigzag electricity, tan dash-dot services, olive tight-dotted feedstock). Capsules ride at speeds matching real-world logistics tempo - electricity instant, coal/nuclear slow rail/batch.</p>
            <p className="info-detail"><strong>Frame as:</strong> "Where does the molecule go?" Answers physical-supply-chain questions. Hover any district to isolate flows in/out of it.</p>
          </section>

          <section>
            <div className="info-mode">Correlation</div>
            <p className="info-summary">Weekly-return co-movement between every pair of baskets.</p>
            <p className="info-detail"><strong>Computed:</strong> Pearson correlation of weekly basket returns over the lookback window (5y), every basket vs every other.</p>
            <p className="info-detail"><strong>What's shown:</strong> Color is a white→gold gradient stretched across the live data's <code>[min ρ, max ρ]</code> so the gradient always uses its full range. Width is bucketed (<code>|ρ|≥0.7</code> strong, <code>0.5–0.7</code> medium, <code>&lt;0.5</code> weak). The chip in the bottom-left is a Tufte strip - every pair plotted at its <code>|ρ|</code> position. Drag the slider to set a threshold; pairs below it dim out.</p>
            <p className="info-detail"><strong>Frame as:</strong> "What moves together right now?" High ρ → baskets share a common factor exposure (oil, rates, growth, dollar). Low ρ → diversification candidate or factor-divergence story.</p>
          </section>

          <section>
            <div className="info-mode">Signal</div>
            <p className="info-summary">Active pair-trade signals + per-basket signal density.</p>
            <p className="info-detail"><strong>Computed:</strong> Six signal types per basket - <em>Pair</em> (60d residual z on a taxonomy-defined L/S spread), <em>Residual</em> (count of constituents stretched ±1.5z), <em>IVR</em> (4w realized vs factor-implied gap), <em>Regime</em> (rolling-β breaks vs long-run mean), <em>Dispersion</em> (idio share of total return magnitude), <em>Momentum</em> (60d return + percentile vs other baskets).</p>
            <p className="info-detail"><strong>What's shown:</strong> Edges = active pair signals (gold solid for <code>|z|&gt;1.5</code>, dashed grey for pre-trigger watch). Each node carries an <code>n/6</code> badge - how many signal types are firing on that basket. ≥3 firing earns a gold halo.</p>
            <p className="info-detail"><strong>Frame as:</strong> "Where is something stretched enough to act on?" Tactical timing, not structural thesis. Click any basket for the full six-card stack.</p>
          </section>
        </div>
      )}
    </>
  );
}

/* ---------------- Ocean + coastline ---------------- */
function OceanAndCoast() {
  // Bathymetric depth contours offshore · faint
  return (
    <g aria-hidden="true">
      <rect x="0" y="0" width="1400" height="900" fill="var(--water-deep)"/>
      <rect x="0" y="0" width="1400" height="900" fill="url(#oceanRipple)" opacity="0.65"/>
      <rect x="0" y="0" width="1400" height="900" fill="url(#oceanRipple2)" opacity="0.5"/>
      {/* Bathymetric depth contours (faint) */}
      <g fill="none" stroke="#2a3648" strokeWidth="0.5" opacity="0.7">
        <path d="M 60 90 Q 220 110 300 200 Q 240 280 130 320 Q 50 270 60 90 Z"/>
        <path d="M 30 50 Q 240 70 320 180 Q 250 300 100 350 Q 0 290 30 50 Z"/>
        <path d="M 40 740 Q 220 720 350 770 Q 280 830 140 840 Q 60 820 40 740 Z"/>
        <path d="M 1330 80 Q 1360 280 1380 480 Q 1370 700 1330 850"/>
        <path d="M 1370 60 Q 1395 280 1395 520 Q 1390 740 1360 870"/>
      </g>
      {/* Compass-rose tick offshore */}
      <g stroke="#4f5563" strokeWidth="0.5" fill="none" opacity="0.6">
        <circle cx="160" cy="120" r="6"/>
        <path d="M 160 112 L 160 128 M 152 120 L 168 120"/>
      </g>
      {/* Lat/long crosshair labels */}
      <g fontFamily="JetBrains Mono" fontSize="8" fill="#4f5563" letterSpacing="0.08em" opacity="0.7">
        <text x="60" y="60">29°N</text>
        <text x="1290" y="60">96°W</text>
        <text x="60" y="870">25°N</text>
      </g>
      {/* Land base · sandy land tone under the zone fills */}
      <path d={COAST_D} fill="var(--land-base)"/>
      {/* Coastline stroke */}
      <path d={COAST_D} fill="none" stroke="#5a6478" strokeWidth="1" opacity="0.85"/>
      {/* Subtle inner shoreline parallel */}
      <path d={COAST_D} fill="none" stroke="#3d4456" strokeWidth="0.5" opacity="0.6" transform="translate(3 3)"/>
    </g>
  );
}

/* ---------------- Zone polygons + textures + dashed boundaries ---------------- */
function Zones() {
  return (
    <g aria-hidden="true" clipPath="url(#land-clip)">
      <defs>
        <clipPath id="land-clip"><path d={COAST_D}/></clipPath>
      </defs>
      {Object.entries(ZONES).map(([key, z]) => (
        <g key={key} className="zone" data-zone={key}>
          <path className="zone-fill" d={z.d} fill={z.fill} opacity="1"/>
          {z.tex && <path d={z.d} fill={z.tex} opacity="0.45"/>}
        </g>
      ))}
      {/* Catan-style zone decoration · small terrain marks */}
      <g aria-hidden="true" opacity="0.7">
        {/* Oilfield: scattered scrub dots + dashed track */}
        <g fill="#6b5430" stroke="none">
          {[[600,610],[640,580],[700,600],[680,640],[750,700],[820,610],[860,690],[900,610],[850,560],[760,630],[660,690],[720,660]].map(([x,y],i)=><circle key={i} cx={x} cy={y} r="1.4"/>)}
        </g>
        <path d="M 530 600 Q 620 590 720 620 Q 820 660 900 620" stroke="#8a6a3a" strokeWidth="0.7" fill="none" strokeDasharray="3 4" opacity="0.6"/>
        {/* Farmland: parallel furrow lines */}
        <g stroke="#3a5a2c" strokeWidth="0.6" fill="none" opacity="0.7">
          <path d="M 1110 440 Q 1180 425 1255 445"/>
          <path d="M 1108 460 Q 1180 446 1262 466"/>
          <path d="M 1106 480 Q 1180 467 1268 487"/>
          <path d="M 1104 500 Q 1180 488 1272 508"/>
          <path d="M 1102 520 Q 1180 509 1276 529"/>
          <path d="M 1102 545 Q 1180 535 1272 555"/>
          <path d="M 1108 568 Q 1180 560 1264 578"/>
        </g>
        {/* Quarry: chevron rocks */}
        <g stroke="#4a3a28" strokeWidth="0.6" fill="none" opacity="0.7">
          {[[900,300],[940,340],[1000,310],[1040,350],[1090,320],[1130,360],[930,380],[1070,290],[1010,370]].map(([x,y],i)=>(
            <path key={i} d={`M ${x-3} ${y+2} L ${x} ${y-3} L ${x+3} ${y+2}`}/>
          ))}
        </g>
        {/* Town: tiny building rects laid on grid */}
        <g fill="#3a2e1a" stroke="none" opacity="0.55">
          {[[615,260],[625,270],[640,260],[655,270],[670,260],[690,265],[710,260],[725,275],[740,265],[625,290],[645,295],[665,290],[690,295],[715,290],[735,295],[630,310],[660,315],[695,310],[725,318]].map(([x,y],i)=><rect key={i} x={x} y={y} width="6" height="5"/>)}
        </g>
        <path d="M 600 282 Q 680 285 760 286" stroke="#6a4f1f" strokeWidth="0.7" fill="none" opacity="0.6"/>
        {/* Power: transmission corridor zigzag */}
        <g stroke="#3d3a55" strokeWidth="0.6" fill="none" opacity="0.7">
          <path d="M 820 660 L 870 670 L 920 660 L 970 670 L 1020 660 L 1070 670"/>
          {[820,870,920,970,1020,1070].map((x,i)=><path key={i} d={`M ${x-2} ${i%2?667:657} L ${x+2} ${i%2?673:663} M ${x-2} ${i%2?673:663} L ${x+2} ${i%2?667:657}`}/>)}
        </g>
        {/* Port: dock cleat row + jetty */}
        <g stroke="#2a2218" strokeWidth="0.7" fill="none" opacity="0.65">
          <path d="M 250 420 L 250 540"/>
          <path d="M 246 440 L 254 440 M 246 470 L 254 470 M 246 500 L 254 500"/>
          <path d="M 320 500 L 250 500"/>
        </g>
        {/* Industrial: parallel pipe corridor */}
        <g stroke="#4a3826" strokeWidth="0.5" fill="none" opacity="0.55">
          <path d="M 470 360 Q 540 350 660 360"/>
          <path d="M 470 370 Q 540 360 660 370"/>
          <path d="M 470 380 Q 540 370 660 380"/>
        </g>
        {/* Equipment yard: small storage rects */}
        <g fill="#3a3022" stroke="none" opacity="0.55">
          {[[695,395],[710,395],[725,395],[740,395],[695,415],[710,415],[725,415],[740,415],[695,435],[710,435],[725,435],[740,435]].map(([x,y],i)=><rect key={i} x={x} y={y} width="9" height="6"/>)}
        </g>
        {/* Nuclear: containment ring + cooling */}
        <g stroke="#1e4a4a" strokeWidth="0.55" fill="none" opacity="0.7">
          <circle cx="1200" cy="690" r="14"/>
          <circle cx="1200" cy="690" r="9"/>
          <path d="M 1186 690 L 1156 690 M 1214 690 L 1244 690" strokeDasharray="2 3"/>
        </g>
      </g>
    </g>
  );
}

/* ---------------- District hit-areas (invisible) ----------------
   No rendered labels - the illustrator's island already provides location.
   Each invisible <rect> still captures hover so flow mode can highlight
   edges entering or leaving a district. */
function DistrictLabels({ onDistrictHover }) {
  return (
    <g>
      {Object.entries(DISTRICTS).map(([key, d]) => (
        <g key={key}
          className="district-label-group"
          transform={`translate(${d.labelX},${d.labelY})`}
          onMouseEnter={() => onDistrictHover && onDistrictHover(key)}
          onMouseLeave={() => onDistrictHover && onDistrictHover(null)}
        >
          <rect className="district-hit" x="-30" y="-20" width="220" height="44" fill="transparent"/>
        </g>
      ))}
    </g>
  );
}

/* ---------------- Edges layer (D3-driven) ---------------- */
const FLOW_PALETTE = {
  crude:       { color: "#e3b577", width: 4.5 },  // bright amber
  gas:         { color: "#82b3d4", width: 3.8 },  // bright blue
  products:    { color: "#e8b87a", width: 3.5 },  // warm orange
  lng:         { color: "#a8cee0", width: 3.2 },  // pale blue
  coal:        { color: "#1a1a1a", width: 4.5 },  // black, dashed · solid-fuel rail
  nuclear:     { color: "#9bc7b8", width: 3.2 },  // brighter teal
  electricity: { color: "#b89cd2", width: 3.5 },  // brighter violet
  services:    { color: "#c4a888", width: 5.5 },  // warm tan, thick · industrial / equipment
  feedstock:   { color: "#c5cf94", width: 3.0 },  // brighter olive
};

/* Per-class line *style*. Color from FLOW_PALETTE; this layer adds geometry:
   dasharray, double-line, wave, or zigzag. */
const FLOW_STYLE = {
  crude:       { dasharray: null,           double: false, wave: false, zigzag: false },
  gas:         { dasharray: null,           double: false, wave: true,  zigzag: false, waveAmp: 4 },
  products:    { dasharray: null,           double: true,  wave: false, zigzag: false },
  lng:         { dasharray: "1 6",          double: false, wave: false, zigzag: false }, // dotted
  coal:        { dasharray: "8 4",          double: false, wave: false, zigzag: false }, // dashed (thick black)
  nuclear:     { dasharray: "12 4 2 4",     double: false, wave: false, zigzag: false }, // long-dash dotted
  electricity: { dasharray: null,           double: false, wave: false, zigzag: true, zigzagAmp: 3 },
  services:    { dasharray: "6 3 1 3",      double: false, wave: false, zigzag: false }, // dash-dot (thick black)
  feedstock:   { dasharray: "1 4",          double: false, wave: false, zigzag: false }, // tight dotted
};

function flowPathFor(d) {
  const st = FLOW_STYLE[d.cls] || FLOW_STYLE.crude;
  if (st.wave)   return wavePath(d.s, d.t, st.waveAmp || 4);
  if (st.zigzag) return zigzagPath(d.s, d.t, st.zigzagAmp || 3);
  return curvePath(d.s, d.t, 0.10);
}

function Edges({ mode, hover, selected, hoverDistrict, corrThreshold = 0 }) {
  const ref = React.useRef(null);

  React.useEffect(() => {
    const g = d3.select(ref.current);
    g.selectAll("*").remove();

    const focus = hover || selected;

    if (mode === "flow") {
      const data = FLOW_EDGES.map(([s, t, cls]) => ({
        s: NODE_BY_ID[s], t: NODE_BY_ID[t], src: s, dst: t, cls: cls || "crude",
      })).filter(e => e.s && e.t);

      const opacityFor = (d) => {
        // District hover takes priority - dim everything not touching the district
        if (hoverDistrict) {
          const sd = d.s.district, td = d.t.district;
          if (sd !== hoverDistrict && td !== hoverDistrict) return 0.15;
          return 0.98;
        }
        if (focus) return (d.src === focus || d.dst === focus) ? 0.98 : 0.20;
        return 0.92;
      };

      const colorFor = (d) => {
        if (focus && (d.src === focus || d.dst === focus)) return "#d4af37";
        return (FLOW_PALETTE[d.cls] || FLOW_PALETTE.crude).color;
      };
      const widthFor = (d) => (FLOW_PALETTE[d.cls] || FLOW_PALETTE.crude).width;
      const dashFor  = (d) => (FLOW_STYLE[d.cls]   || FLOW_STYLE.crude).dasharray;

      const sel = g.selectAll("g.flow-edge")
        .data(data)
        .enter()
        .append("g")
        .attr("class", d => "flow-edge flow-" + d.cls);

      // Main edge - wave / zigzag / curve depending on class. No endpoint
      // arrowheads on the flow layer; direction reads from the moving capsule.
      sel.append("path")
        .attr("class", "edge-base")
        .attr("d", d => flowPathFor(d))
        .attr("stroke", colorFor)
        .attr("stroke-width", widthFor)
        .attr("stroke-opacity", opacityFor)
        .attr("stroke-dasharray", dashFor)
        .attr("fill", "none")
        .attr("stroke-linecap", "round");

      // Double-line variants ("products") - second parallel curve offset by 3px
      sel.filter(d => (FLOW_STYLE[d.cls] || {}).double === true)
        .append("path")
        .attr("class", "edge-base-double")
        .attr("d", d => offsetCurvePath(d.s, d.t, 3, 0.10))
        .attr("stroke", colorFor)
        .attr("stroke-width", d => Math.max(1, widthFor(d) - 0.4))
        .attr("stroke-opacity", d => opacityFor(d) * 0.75)
        .attr("fill", "none")
        .attr("stroke-linecap", "round");

      // Animated traveling dot - class-coded color, class-coded CSS duration.
      // Width tuned a little above the line so the capsule reads as a bright
      // bead riding on the line rather than a dot lost in it.
      sel.append("path")
        .attr("class", "edge-dot")
        .attr("d", d => flowPathFor(d))
        .attr("stroke", d => {
          if (focus && (d.src === focus || d.dst === focus)) return "#f4d97a";
          return (FLOW_PALETTE[d.cls] || FLOW_PALETTE.crude).color;
        })
        .attr("stroke-width", d => Math.max(2.6, (FLOW_PALETTE[d.cls] || FLOW_PALETTE.crude).width * 1.15))
        .attr("stroke-opacity", d => {
          if (hoverDistrict) {
            const sd = d.s.district, td = d.t.district;
            return (sd === hoverDistrict || td === hoverDistrict) ? 0.95 : 0;
          }
          if (focus) return (d.src === focus || d.dst === focus) ? 0.95 : 0;
          return 0.85;
        })
        .attr("fill", "none")
        .attr("stroke-linecap", "round");
    }

    else if (mode === "corr") {
      /* Encoding rules (data-driven, no thresholding):
         - Render ALL live pairs.
         - Color & opacity from a gradient mapped over [cMin, cMax] of |c|
           in the LIVE data (so the visual range is always used end-to-end,
           regardless of whether any negatives exist).
             t=0 → cool teal #4a9d96
             t=0.5 → muted neutral #5a6478
             t=1 → warm gold #d4af37
         - Width is bucketed (categorical reads faster):
             STRONG (|c|≥0.7) 2.6px · MEDIUM (0.5–0.7) 1.4px · WEAK (<0.5) 0.7px
         - Dash: STRONG/MEDIUM solid, WEAK "4 5" dashed.
         - Halo + endpoint markers + animated traveling dot: STRONG only.
         - Paint order ascending by |c| so strong paints last (top of stack).
         - Hover/select: edges touching focus stay full opacity, others drop to 0.05.
      */
      const STRONG = 0.70, MEDIUM = 0.50;
      const bucket = c => Math.abs(c) >= STRONG ? "strong" : Math.abs(c) >= MEDIUM ? "medium" : "weak";

      // All live pairs, joined to nodes
      const allUnfiltered = NODE_CORR_PAIRS.map(([a, b, c]) => ({
        s: NODE_BY_ID[a], t: NODE_BY_ID[b], a, b, c, bucket: bucket(c),
      })).filter(e => e.s && e.t);

      // Compute gradient bounds over the FULL data so the gradient stays stable
      // as the user scrubs the threshold slider.
      const absVals = allUnfiltered.map(e => Math.abs(e.c));
      const cMin = absVals.length ? Math.min(...absVals) : 0;
      const cMax = absVals.length ? Math.max(...absVals) : 1;
      const span = (cMax - cMin) || 1;
      const tOf = c => (Math.abs(c) - cMin) / span;

      // Threshold filter - declutter pairs below |ρ| ≥ corrThreshold.
      // Edges touching the focused node are kept regardless so hover still
      // reveals the full local web.
      const all = allUnfiltered.filter(e =>
        Math.abs(e.c) >= corrThreshold || (focus && (e.a === focus || e.b === focus))
      );

      // white #ffffff → gold #d4af37 (linear lerp; opacity gradient does the contrast work)
      const WHITE = [0xff, 0xff, 0xff];
      const GOLD = [0xd4, 0xaf, 0x37];
      const lerp = (a, b, t) => Math.round(a + (b - a) * t);
      const colorOf = (c) => {
        const t = Math.max(0, Math.min(1, tOf(c)));
        return `rgb(${lerp(WHITE[0],GOLD[0],t)},${lerp(WHITE[1],GOLD[1],t)},${lerp(WHITE[2],GOLD[2],t)})`;
      };

      // Width by bucket
      const widthFor = b => b === "strong" ? 2.6 : b === "medium" ? 1.4 : 0.7;

      // Opacity: gradient base + focus dimming
      const opacityFor = (d) => {
        const baseT = Math.max(0, Math.min(1, tOf(d.c)));
        const base = 0.15 + baseT * 0.80;
        if (focus) {
          const touches = d.a === focus || d.b === focus;
          return touches ? base : 0.05;
        }
        return base;
      };

      // Paint order: weakest first, strongest last so strong sits on top
      const data = all.slice().sort((x, y) => Math.abs(x.c) - Math.abs(y.c));

      const sel = g.selectAll("g.corr-edge")
        .data(data, d => d.a + "|" + d.b)
        .enter()
        .append("g")
        .attr("class", d => "corr-edge bucket-" + d.bucket);

      // HALO - strong only
      sel.filter(d => d.bucket === "strong").append("path")
        .attr("class", "edge-halo")
        .attr("d", d => curvePath(d.s, d.t, 0.18))
        .attr("stroke", d => colorOf(d.c))
        .attr("stroke-width", 8)
        .attr("opacity", d => focus && !(d.a === focus || d.b === focus) ? 0 : 0.12)
        .attr("fill", "none")
        .attr("stroke-linecap", "round")
        .attr("filter", "url(#corrGlow)");

      // Main edge
      sel.append("path")
        .attr("class", "edge-base")
        .attr("d", d => curvePath(d.s, d.t, 0.18))
        .attr("stroke", d => colorOf(d.c))
        .attr("stroke-width", d => widthFor(d.bucket))
        .attr("stroke-dasharray", d => d.bucket === "weak" ? "4 5" : null)
        .attr("opacity", opacityFor)
        .attr("fill", "none")
        .attr("stroke-linecap", "round");

      // Endpoint markers - strong only
      const endpoints = sel.filter(d => d.bucket === "strong");
      endpoints.append("circle")
        .attr("cx", d => d.s.x).attr("cy", d => d.s.y)
        .attr("r", 5.5).attr("fill", "rgba(10,13,18,0.85)")
        .attr("opacity", d => focus && !(d.a === focus || d.b === focus) ? 0 : 0.9);
      endpoints.append("circle")
        .attr("cx", d => d.t.x).attr("cy", d => d.t.y)
        .attr("r", 5.5).attr("fill", "rgba(10,13,18,0.85)")
        .attr("opacity", d => focus && !(d.a === focus || d.b === focus) ? 0 : 0.9);
      endpoints.append("circle")
        .attr("cx", d => d.s.x).attr("cy", d => d.s.y)
        .attr("r", 3.5).attr("fill", d => colorOf(d.c))
        .attr("opacity", d => focus && !(d.a === focus || d.b === focus) ? 0 : 1);
      endpoints.append("circle")
        .attr("cx", d => d.t.x).attr("cy", d => d.t.y)
        .attr("r", 3.5).attr("fill", d => colorOf(d.c))
        .attr("opacity", d => focus && !(d.a === focus || d.b === focus) ? 0 : 1);

      // Animated traveling dot - strong bucket only, gradient color. Bigger
      // stroke so the bead is unmistakable against the line.
      sel.filter(d => d.bucket === "strong").append("path")
        .attr("class", "edge-dot")
        .attr("d", d => curvePath(d.s, d.t, 0.18))
        .attr("stroke", d => colorOf(d.c))
        .attr("stroke-width", 3.8)
        .attr("opacity", d => {
          if (focus) return d.a === focus || d.b === focus ? 0.85 : 0;
          return 0.65;
        })
        .attr("fill", "none")
        .attr("stroke-linecap", "round");
    }

    else if (mode === "signal") {
      const data = SIGNAL_PAIRS.map(p => ({ s: NODE_BY_ID[p.a], t: NODE_BY_ID[p.b], ...p }));
      g.selectAll("path.edge")
        .data(data)
        .enter()
        .append("path")
        .attr("class", "edge")
        .attr("d", d => curvePath(d.s, d.t, 0.16))
        .attr("stroke", d => Math.abs(d.z) > 1.5 ? "#d4af37" : "#5a6478")
        .attr("stroke-width", d => 0.6 + Math.abs(d.z) * 1.2)
        .attr("stroke-dasharray", d => Math.abs(d.z) > 1.5 ? "0" : "3 3")
        .attr("opacity", d => Math.abs(d.z) > 1.5 ? 0.92 : 0.35);
      // Z labels at edge midpoints
      g.selectAll("text.zlabel")
        .data(data.filter(d => Math.abs(d.z) > 1.5))
        .enter()
        .append("text")
        .attr("class", "zlabel")
        .attr("x", d => (d.s.x + d.t.x) / 2)
        .attr("y", d => (d.s.y + d.t.y) / 2 - 6)
        .attr("font-family", "JetBrains Mono, monospace")
        .attr("font-size", "9")
        .attr("text-anchor", "middle")
        .attr("fill", "#d4af37")
        .attr("letter-spacing", "0.08em")
        .text(d => `z ${d.z > 0 ? "+" : ""}${d.z.toFixed(1)}`);
    }
  }, [mode, hover, selected, corrThreshold]);

  return <g ref={ref} className="edges-layer"/>;
}

/* ---------------- Sub-industry nodes ---------------- */
function SubNodes({ hover, selected, onHover, onLeave, onSelect, mode, signalCounts }) {
  return (
    <g>
      {NODES.map(n => {
        const Motif = MOTIF_BY_NODE[n.id];
        const isActive = selected === n.id || hover === n.id;
        const sigCount = (signalCounts && signalCounts[n.id]) || 0;
        const showHalo = mode === "signal" && sigCount >= 3;
        const showBadge = mode === "signal" && sigCount > 0;
        return (
          <g
            key={n.id}
            className={"sub-node" + (isActive ? " active" : "") + (showHalo ? " has-halo" : "")}
            data-node-id={n.id}
            transform={`translate(${n.x},${n.y})`}
            onMouseEnter={(e) => onHover(n.id, e)}
            onMouseMove={(e) => onHover(n.id, e)}
            onMouseLeave={onLeave}
            onClick={(e) => { e.stopPropagation(); onSelect(n.id); }}
          >
            {showHalo && (
              <g className="signal-halo">
                <circle r="26" fill="none" stroke="rgba(212,175,55,0.45)" strokeWidth="1"/>
                <circle r="32" fill="none" stroke="rgba(212,175,55,0.18)" strokeWidth="0.6" strokeDasharray="2 3"/>
              </g>
            )}
            {Motif && <g transform="scale(2.25)"><Motif/></g>}
            <text className="name-label" x="0" y="-30" textAnchor="middle">{n.name.split(" / ")[0].split(" (")[0]}</text>
            {showBadge && (
              <g className="signal-badge" transform="translate(0 38)">
                <rect x="-14" y="-7" width="28" height="14" rx="1"
                  fill="rgba(10,13,18,0.92)" stroke={sigCount >= 3 ? "var(--accent)" : "var(--border-hi)"} strokeWidth="0.5"/>
                <text x="0" y="3.5" textAnchor="middle"
                  fontFamily="JetBrains Mono, monospace" fontSize="9"
                  letterSpacing="0.08em"
                  fill={sigCount >= 3 ? "#d4af37" : "#818796"}>
                  {sigCount}/6
                </text>
              </g>
            )}
            <rect className="hit" x="-30" y="-36" width="60" height="80"/>
          </g>
        );
      })}
    </g>
  );
}

/* ---------------- Mode toggle ---------------- */
function ModeToggle({ mode, setMode }) {
  const Btn = ({ id, label, icon }) => (
    <button className={mode === id ? "active" : ""} onClick={() => setMode(id)} title={label}>
      {icon}
      <span>{label}</span>
    </button>
  );
  const flowIcon = (
    <svg className="ico" viewBox="0 0 14 14"><path d="M 1 7 L 13 7" /><path d="M 9 3 L 13 7 L 9 11"/></svg>
  );
  const corrIcon = (
    <svg className="ico" viewBox="0 0 14 14"><circle cx="3" cy="4" r="1.5"/><circle cx="11" cy="4" r="1.5"/><circle cx="3" cy="10" r="1.5"/><circle cx="11" cy="10" r="1.5"/><path d="M 3 4 L 11 10 M 11 4 L 3 10 M 3 4 L 11 4 M 3 10 L 11 10"/></svg>
  );
  const signalIcon = (
    <svg className="ico" viewBox="0 0 14 14"><path d="M 1 11 L 4 6 L 7 9 L 10 3 L 13 7"/></svg>
  );
  return (
    <div className="mode-toggle">
      <Btn id="flow" label="Flow" icon={flowIcon}/>
      <Btn id="corr" label="Correlation" icon={corrIcon}/>
      <Btn id="signal" label="Signal" icon={signalIcon}/>
    </div>
  );
}

function Legend({ mode, corrThreshold, setCorrThreshold }) {
  if (mode === "flow") {
    // Render a tiny SVG sample matching the actual line style of each class
    // (wave / zigzag / dash / double / solid + color + width).
    const FLOW_CLASSES = ["crude","gas","products","lng","coal","nuclear","electricity","services","feedstock"];
    const sw = (cls) => {
      const pal = FLOW_PALETTE[cls];
      const sty = FLOW_STYLE[cls];
      const W = 38, H = 12, cy = H / 2;
      const s = { x: 2, y: cy }, t = { x: W - 2, y: cy };
      let d;
      if (sty.wave)         d = wavePath(s, t, 2.5, 4);
      else if (sty.zigzag)  d = zigzagPath(s, t, 2.5, 6);
      else                  d = `M ${s.x} ${s.y} L ${t.x} ${t.y}`;
      const strokeW = Math.max(1.5, pal.width * 0.55);
      return (
        <svg width={W} height={H} className="legend-flow-sw" aria-hidden="true">
          <path d={d} stroke={pal.color} strokeWidth={strokeW}
            strokeDasharray={sty.dasharray || null}
            fill="none" strokeLinecap="round"/>
          {sty.double && (
            <path d={`M ${s.x} ${cy + 3} L ${t.x} ${cy + 3}`}
              stroke={pal.color} strokeWidth={Math.max(1, strokeW - 0.4)}
              fill="none" strokeLinecap="round" opacity="0.85"/>
          )}
        </svg>
      );
    };
    return (
      <div className="legend legend-flow">
        {FLOW_CLASSES.map(cls => (
          <span key={cls} className="legend-line">
            {sw(cls)}
            {cls}
          </span>
        ))}
      </div>
    );
  }
  if (mode === "corr") {
    const pairs = (window.NODE_CORR_PAIRS || []).map(p => [p[0], p[1], +p[2]]);
    const absVals = pairs.map(p => Math.abs(p[2]));
    const cMin = absVals.length ? Math.min(...absVals) : 0;
    const cMax = absVals.length ? Math.max(...absVals) : 1;
    const span = (cMax - cMin) || 1;
    const tOf = c => (Math.abs(c) - cMin) / span;
    const lerp = (a, b, t) => Math.round(a + (b - a) * t);
    const colorAt = c => {
      const t = Math.max(0, Math.min(1, tOf(c)));
      return `rgb(${lerp(0xff,0xd4,t)},${lerp(0xff,0xaf,t)},${lerp(0xff,0x37,t)})`;
    };
    const W = 180, H = 14;
    const visible = pairs.filter(p => Math.abs(p[2]) >= corrThreshold).length;
    return (
      <div className="legend">
        <div className="legend-line">ρ {cMin.toFixed(2)} → {cMax.toFixed(2)}</div>
        <svg className="tufte-strip" width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
          {pairs.map((p, i) => {
            const x = tOf(p[2]) * W;
            const live = Math.abs(p[2]) >= corrThreshold;
            return (
              <line key={i}
                x1={x} x2={x} y1={1} y2={H - 1}
                stroke={colorAt(p[2])}
                strokeOpacity={live ? 0.9 : 0.16}
                strokeWidth={1}
              />
            );
          })}
        </svg>
        <input
          type="range"
          className="corr-slider"
          min={cMin.toFixed(2)}
          max={cMax.toFixed(2)}
          step="0.01"
          value={corrThreshold}
          onChange={e => setCorrThreshold(+e.target.value)}
        />
        <div className="legend-line">
          ≥ <span className="accent">{corrThreshold.toFixed(2)}</span> · {visible}/{pairs.length} pairs
        </div>
      </div>
    );
  }
  return (
    <div className="legend">
      <span className="legend-line"><span className="swatch" style={{ background: "#d4af37" }}></span>|z| &gt; 1.5 · active</span>
      <span className="legend-line"><span className="swatch" style={{ background: "#5a6478" }}></span>watch</span>
    </div>
  );
}

/* ---------------- Tooltip ---------------- */
function Tooltip({ x, y, node }) {
  if (!node) return null;
  const sgn = node.r60 >= 0;
  return (
    <div className="tt" style={{ left: x, top: y }}>
      <div className="tt-name">{node.name}</div>
      <div className="tt-row"><span>TICKER · n</span><span className="tt-tk">{node.ticker} · {node.n}</span></div>
      <div className="tt-row"><span>60d</span><span className={"v " + (sgn ? "pct-pos" : "pct-neg")}>{(sgn?"+":"")}{(node.r60*100).toFixed(1)}%</span></div>
      <div className="tt-row"><span>Intra-corr</span><span className="v">{node.intra ? node.intra.toFixed(2) : "-"}</span></div>
    </div>
  );
}

/* ---------------- Signal computation (basket-level) ----------------
   Returns an array of 6 signal objects, one per type. Non-firing slots
   stay in the array so the SignalStack renders the full universe with
   silent placeholders. */
function computeSignals(node) {
  if (!node) return [];

  const allPairs     = window.ALL_PAIRS || [];   // full pair list (no |z| filter)
  const regimeAlerts = (window.ATLAS_RAW && window.ATLAS_RAW.regime_alerts) || [];
  const allNodes     = window.NODES || [];
  const byId         = window.NODE_BY_ID || {};

  const signals = [];

  // 1. PAIR - strongest pair this basket participates in
  const pairCands = allPairs
    .filter(p => p.long === node.id || p.short === node.id)
    .slice()
    .sort((a, b) => Math.abs(b.z || 0) - Math.abs(a.z || 0));
  if (pairCands.length > 0) {
    const p = pairCands[0];
    const isLong = p.long === node.id;
    const partnerId = isLong ? p.short : p.long;
    const z = p.z || 0;
    const firing = Math.abs(z) > 1.5;
    signals.push({
      type: "pair",
      firing,
      score: Math.min(Math.abs(z) / 3, 1),
      direction: !firing ? "neutral" : (isLong ? "bull" : "bear"),
      z, isLong, partnerId,
      partner: byId[partnerId] || { id: partnerId, name: partnerId },
      thesis: p.thesis,
      ret_3m: p.ret_3m,
    });
  } else {
    signals.push({ type: "pair", firing: false, score: 0, direction: "neutral" });
  }

  // 2. RESIDUAL - count of constituents with |z60d| > 1.5
  const consts = node.constituents || [];
  const stretched = consts.filter(c => c.residual_z60d != null && Math.abs(c.residual_z60d) > 1.5);
  const stretchedPos = stretched.filter(c => c.residual_z60d > 0).length;
  const stretchedNeg = stretched.filter(c => c.residual_z60d < 0).length;
  const residFiring = stretched.length >= 2;
  signals.push({
    type: "residual",
    firing: residFiring,
    score: Math.min(stretched.length / Math.max(2, consts.length * 0.2), 1),
    direction: !residFiring ? "neutral"
      : (stretchedPos > stretchedNeg ? "bull"
        : (stretchedNeg > stretchedPos ? "bear" : "neutral")),
    count: stretched.length,
    total: consts.length,
    stretchedPos, stretchedNeg,
  });

  // 3. IVR - gap_4w; positive gap = realized exceeded factor (potential mean-reversion bear)
  const gap = node.ivr && node.ivr.gap_4w;
  if (gap != null) {
    const ivrFiring = Math.abs(gap) > 0.02;
    signals.push({
      type: "ivr",
      firing: ivrFiring,
      score: Math.min(Math.abs(gap) / 0.15, 1),
      direction: !ivrFiring ? "neutral" : (gap > 0 ? "bear" : "bull"),
      gap,
      actual: node.ivr.actual_4w,
      implied: node.ivr.implied_4w,
    });
  } else {
    signals.push({ type: "ivr", firing: false, score: 0, direction: "neutral" });
  }

  // 4. REGIME - strongest β break for this basket (others kept for the card body)
  const myRegime = regimeAlerts
    .filter(r => r.basket === node.id)
    .slice()
    .sort((a, b) => Math.abs(b.z_score || 0) - Math.abs(a.z_score || 0))
    .slice(0, 3);
  if (myRegime.length > 0) {
    const top = myRegime[0];
    signals.push({
      type: "regime",
      firing: true,
      score: Math.min(Math.abs(top.z_score || 0) / 4, 1),
      direction: "neutral",
      driver: top.driver,
      z_score: top.z_score,
      direction_word: top.direction,
      current: top.current_beta,
      mean: top.long_run_mean,
      others: myRegime.slice(1),
    });
  } else {
    signals.push({ type: "regime", firing: false, score: 0, direction: "neutral" });
  }

  // 5. DISPERSION - idio share of total magnitude (3m window)
  const a3 = node.attribution && node.attribution["3m"];
  if (a3) {
    const absIdio = Math.abs(a3.idio || 0);
    const denom = Math.abs(a3.factor || 0) + absIdio;
    const share = denom > 0 ? absIdio / denom : 0;
    const dispFiring = share > 0.5;
    signals.push({
      type: "dispersion",
      firing: dispFiring,
      score: Math.min(share, 1),
      direction: "neutral",
      idio_pct: share * 100,
      idio: a3.idio,
      factor: a3.factor,
    });
  } else {
    signals.push({ type: "dispersion", firing: false, score: 0, direction: "neutral" });
  }

  // 6. MOMENTUM - 60d return + percentile vs other baskets
  const r60 = node.r60;
  if (r60 != null) {
    const all60s = allNodes.map(n => n.r60).filter(v => v != null);
    const rank = all60s.filter(v => v < r60).length;
    const percentile = all60s.length > 0 ? (rank / all60s.length) * 100 : 50;
    const momFiring = Math.abs(r60) > 0.10;
    signals.push({
      type: "momentum",
      firing: momFiring,
      score: Math.min(Math.abs(r60) / 0.30, 1),
      direction: !momFiring ? "neutral" : (r60 > 0 ? "bull" : "bear"),
      value: r60,
      percentile,
    });
  } else {
    signals.push({ type: "momentum", firing: false, score: 0, direction: "neutral" });
  }

  return signals;
}

/* ---------------- Signal stack UI (basket Overview tab) ---------------- */
function SignalStack({ node, onSelectBasket }) {
  const fmtPct = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
  const fmtZ   = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + v.toFixed(2);
  const signals = useMemo(() => computeSignals(node), [node]);
  const firingCount = signals.filter(s => s.firing).length;

  // Watch list - pairs touching this basket with 0.8 < |z| < 1.5
  const watch = (window.ALL_PAIRS || [])
    .filter(p => (p.long === node.id || p.short === node.id))
    .filter(p => p.z != null && Math.abs(p.z) > 0.8 && Math.abs(p.z) < 1.5)
    .sort((a, b) => Math.abs(b.z) - Math.abs(a.z));

  const SIGNAL_LABEL = {
    pair: "Pair", residual: "Residual", ivr: "IVR",
    regime: "Regime", dispersion: "Dispersion", momentum: "Momentum",
  };
  const dirCls = (d) => d === "bull" ? "sig-bull" : d === "bear" ? "sig-bear" : "sig-neutral";

  return (
    <div className="signal-stack">
      {/* Density header */}
      <div className="sig-density">
        <div className="sig-density-label">
          Signal density · <span className="accent">{firingCount}</span> of 6 firing
          {" "}<AsOfChip inline />
        </div>
        <div className="sig-density-bar">
          {signals.map(s => (
            <span key={s.type}
              className={"sig-square " + (s.firing ? dirCls(s.direction) : "sig-off")}
              title={SIGNAL_LABEL[s.type] + (s.firing ? "" : " · silent")}
            />
          ))}
        </div>
      </div>

      {/* Six cards · always rendered, dimmed when silent */}
      <div className="sig-cards">
        {signals.map(s => (
          <div key={s.type} className={"sig-card " + dirCls(s.direction) + (s.firing ? "" : " silent")}>
            <div className="sig-card-head">
              <span className="sig-type">{SIGNAL_LABEL[s.type]}</span>
              {!s.firing && <span className="sig-status">silent</span>}
            </div>
            {s.type === "pair" && (
              s.firing ? (
                <>
                  <div className="sig-summary">
                    {s.isLong ? "Long" : "Short"} vs <a className="sig-link"
                      onClick={() => onSelectBasket && onSelectBasket(s.partnerId)}>{s.partner.name || s.partnerId}</a>
                    {s.thesis ? ` · ${s.thesis}` : ""}
                  </div>
                  <div className="sig-meta">
                    <span className={"sig-num " + (s.z >= 0 ? "pct-pos" : "pct-neg")}>z {fmtZ(s.z)}</span>
                    {s.ret_3m != null && <span className="sig-meta-x">3m {fmtPct(s.ret_3m)}</span>}
                  </div>
                </>
              ) : <div className="sig-summary">No active pair signal touching this basket.</div>
            )}
            {s.type === "residual" && (
              s.firing ? (
                <>
                  <div className="sig-summary">
                    {s.count} of {s.total} names stretched · |z|&gt;1.5
                    {s.stretchedPos !== s.stretchedNeg
                      ? ` · ${s.stretchedPos > s.stretchedNeg ? "more upside" : "more downside"} skew`
                      : ""}
                  </div>
                  <div className="sig-meta">
                    <span className="sig-num">{s.count}/{s.total}</span>
                    <span className="sig-meta-x">↑{s.stretchedPos} ↓{s.stretchedNeg}</span>
                  </div>
                </>
              ) : <div className="sig-summary">All constituents within ±1.5z. Basket is settled.</div>
            )}
            {s.type === "ivr" && (
              s.firing ? (
                <>
                  <div className="sig-summary">
                    Realized {fmtPct(s.actual)} {s.gap > 0 ? "above" : "below"} factor-implied {fmtPct(s.implied)} · {s.gap > 0 ? "may be overdone" : "room to run"}
                  </div>
                  <div className="sig-meta">
                    <span className={"sig-num " + (s.gap >= 0 ? "pct-pos" : "pct-neg")}>gap {fmtPct(s.gap)}</span>
                  </div>
                </>
              ) : <div className="sig-summary">Realized return tracking factor model (|gap| &lt; 2pp).</div>
            )}
            {s.type === "regime" && (
              s.firing ? (
                <>
                  <div className="sig-summary">
                    β to {s.driver} {s.direction_word} · was {s.mean != null ? s.mean.toFixed(2) : "·"} → now {s.current != null ? s.current.toFixed(2) : "·"}
                    {s.others && s.others.length > 0 ? ` · also ${s.others.map(o => o.driver).join(", ")}` : ""}
                  </div>
                  <div className="sig-meta">
                    <span className={"sig-num " + (s.z_score >= 0 ? "pct-pos" : "pct-neg")}>z {fmtZ(s.z_score)}</span>
                  </div>
                </>
              ) : <div className="sig-summary">No structural β breaks for this basket.</div>
            )}
            {s.type === "dispersion" && (
              s.firing ? (
                <>
                  <div className="sig-summary">
                    {s.idio_pct.toFixed(0)}% idio (3m) · stock-picker friendly · factor doesn't explain most of the move
                  </div>
                  <div className="sig-meta">
                    <span className="sig-num accent">{s.idio_pct.toFixed(0)}%</span>
                  </div>
                </>
              ) : <div className="sig-summary">Factor-driven (3m). Idio share &lt;50%.</div>
            )}
            {s.type === "momentum" && (
              s.firing ? (
                <>
                  <div className="sig-summary">
                    60d {fmtPct(s.value)} · {s.percentile.toFixed(0)}th percentile of energy baskets
                  </div>
                  <div className="sig-meta">
                    <span className={"sig-num " + (s.value >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(s.value)}</span>
                    <span className="sig-meta-x">p{s.percentile.toFixed(0)}</span>
                  </div>
                </>
              ) : <div className="sig-summary">|60d| &lt; 10%. Trendless.</div>
            )}
          </div>
        ))}
      </div>

      {/* Watch - pre-trigger pairs */}
      {watch.length > 0 && (
        <div className="sig-watch">
          <div className="section-head">Watch · approaching trigger</div>
          {watch.map((p, i) => {
            const isLong = p.long === node.id;
            const partnerId = isLong ? p.short : p.long;
            const partner = (window.NODE_BY_ID || {})[partnerId];
            return (
              <div key={i} className="sig-watch-row clickable"
                onClick={() => onSelectBasket && onSelectBasket(partnerId)}>
                <span className="sig-type">{isLong ? "L" : "S"} vs</span>
                <span className="sig-summary">{partner?.name || partnerId}</span>
                <span className="sig-num">z {fmtZ(p.z)}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------- Side pane: drawer + screener ---------------- */
/* Data-freshness chip · reads the record atlas.jsx computed at hydration.
   Rendered wherever numbers whose meaning decays with time are shown - drawers, signal strips, density header. `inline` = compact variant. */
function AsOfChip({ inline }) {
  const a = window.DATA_ASOF;
  if (!a) return null;
  return (
    <span className={"asof-chip " + (inline ? "asof-inline " : "") + a.cls}
          title={a.iso ? `dashboard_data.json generated ${a.iso}` : "no timestamp in data"}>
      <span className="asof-dot"></span>{a.label}
    </span>
  );
}

function Drawer({ node, onClose, onSelectStock, onSelectBasket }) {
  const [tab, setTab] = useState("overview");
  if (!node) return null;
  const sgn = node.r60 >= 0;
  const consts = (node.constituents || []).slice().sort((a,b) => {
    const ra = a.return_60d ?? -999, rb = b.return_60d ?? -999;
    return rb - ra;
  });
  const r60Pct = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + (v*100).toFixed(1) + "%";
  const fmtPct = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + (v*100).toFixed(2) + "%";
  const fmtZ = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + v.toFixed(2);
  
  const hasAttribution = node.attribution && Object.keys(node.attribution).length > 0;
  const hasResiduals = consts.some(c => c.residual_z60d != null);
  
  return (
    <div className="side-section drawer">
      <div className="layer-tag">{DISTRICTS[node.district]?.label}</div>
      <div className="ticker-big">{node.ticker}</div>
      <h2><em>{node.name}</em></h2>
      <div className="desc-blk">{DESCRIPTIONS[node.id] || ""}</div>
      <div style={{ margin: "6px 0 2px" }}><AsOfChip inline /></div>
      
      {/* Tab bar - Lookup retired; the global search in the top bar replaces it. */}
      <div className="drawer-tabs">
        <button className={"drawer-tab" + (tab==="overview" ? " active" : "")} onClick={()=>setTab("overview")}>Overview</button>
        <button className={"drawer-tab" + (tab==="constituents" ? " active" : "")} onClick={()=>setTab("constituents")}>Names · {consts.length}</button>
        {hasResiduals && <button className={"drawer-tab" + (tab==="residuals" ? " active" : "")} onClick={()=>setTab("residuals")}>Residuals</button>}
        {hasAttribution && <button className={"drawer-tab" + (tab==="attribution" ? " active" : "")} onClick={()=>setTab("attribution")}>Attribution</button>}
      </div>

      {tab === "overview" && (
        <>
          <div className="stat-grid">
            <div className="stat"><div className="lab">Constituents</div><div className="val">{node.n}</div></div>
            <div className="stat"><div className="lab">60d Return</div><div className={"val " + (sgn ? "pct-pos" : "pct-neg")}>{r60Pct(node.r60)}</div></div>
            <div className="stat"><div className="lab">Intra-corr</div><div className="val">{node.intra ? node.intra.toFixed(2) : "·"}</div></div>
            <div className="stat"><div className="lab">R²</div><div className="val">{node.r2 != null ? node.r2.toFixed(2) : "·"}</div></div>
          </div>

          {/* Signal stack - replaces the old IVR-only block */}
          <SignalStack node={node} onSelectBasket={onSelectBasket} />

          <div className="section-head">Flows into</div>
          <div className="feeds">
            {FLOW_EDGES.filter(e => e[0] === node.id).map(e => (
              <div key={e[1]}><span className="arrow">›</span> {NODE_BY_ID[e[1]]?.name || e[1]}</div>
            ))}
            {FLOW_EDGES.filter(e => e[0] === node.id).length === 0 && <div style={{color:"var(--text-dim)"}}>(terminal node)</div>}
          </div>
        </>
      )}

      {tab === "constituents" && (
        <div className="const-table">
          <div className="const-row const-head">
            <span>Ticker</span>
            <span>Name</span>
            <span style={{textAlign:"right"}}>60d</span>
          </div>
          {consts.map(c => {
            const r = c.return_60d;
            const cls = r == null ? "" : (r >= 0 ? "pct-pos" : "pct-neg");
            return (
              <div className="const-row clickable" key={c.ticker}
                onClick={() => onSelectStock && onSelectStock(c.ticker)}>
                <span className="const-ticker">{c.ticker}</span>
                <span className="const-name">
                  {c.name}
                  {c.sub_tag ? <span className="const-sub"> · {c.sub_tag}</span> : null}
                </span>
                <span className={"const-pct " + cls}>{r60Pct(r)}</span>
              </div>
            );
          })}
        </div>
      )}

      {tab === "residuals" && (
        <div className="resid-panel">
          <div className="panel-explain">
            Residual z60d = how stretched each name is vs basket. |z| &gt; 2 = potential mean-revert candidate.
          </div>
          <div className="const-table">
            <div className="const-row const-head" style={{gridTemplateColumns:"60px 1fr 50px 50px"}}>
              <span>Ticker</span>
              <span>Name</span>
              <span style={{textAlign:"right"}}>60d</span>
              <span style={{textAlign:"right"}}>z</span>
            </div>
            {consts.slice().sort((a,b) => Math.abs(b.residual_z60d ?? 0) - Math.abs(a.residual_z60d ?? 0)).map(c => {
              const z = c.residual_z60d;
              const r = c.return_60d;
              const stretched = z != null && Math.abs(z) > 1.5;
              const rCls = r == null ? "" : (r >= 0 ? "pct-pos" : "pct-neg");
              const zCls = z == null ? "" : (Math.abs(z) > 1.5 ? "pct-stretch" : (z >= 0 ? "pct-pos" : "pct-neg"));
              return (
                <div className="const-row clickable" key={c.ticker}
                  onClick={() => onSelectStock && onSelectStock(c.ticker)}
                  style={{gridTemplateColumns:"60px 1fr 50px 50px", background: stretched ? "rgba(212,175,55,0.05)" : "transparent"}}>
                  <span className="const-ticker">{c.ticker}</span>
                  <span className="const-name">{c.name}</span>
                  <span className={"const-pct " + rCls}>{r60Pct(r)}</span>
                  <span className={"const-pct " + zCls}>{fmtZ(z)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {tab === "attribution" && hasAttribution && (
        <AttributionPanel attribution={node.attribution} fmtPct={fmtPct} />
      )}

      <button className="close" onClick={onClose}>‹ Close</button>
    </div>
  );
}

/* ---------------- Ticker Lookup (still defined for any future re-use) -------- */
// eslint-disable-next-line no-unused-vars
function TickerLookup({ onPick }) {
  const [q, setQ] = useState("");
  const all = window.ALL_TICKERS || [];
  const matches = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return all.slice(0, 40);
    return all.filter(t =>
      t.ticker.toLowerCase().includes(s) ||
      (t.name || "").toLowerCase().includes(s) ||
      (t.basket_name || "").toLowerCase().includes(s)
    ).slice(0, 60);
  }, [q, all]);

  return (
    <div className="lookup-panel">
      <div className="panel-explain">
        Search any of the {all.length} constituents. Click a row to open its stock-level view.
      </div>
      <input
        className="lookup-input"
        type="text"
        placeholder="ticker, name, or basket…"
        value={q}
        onChange={e => setQ(e.target.value)}
        autoFocus
      />
      <div className="const-table" style={{marginTop: 10}}>
        <div className="const-row const-head" style={{gridTemplateColumns:"60px 1fr 1fr"}}>
          <span>Ticker</span>
          <span>Name</span>
          <span>Basket</span>
        </div>
        {matches.map(t => (
          <div key={t.ticker}
            className="const-row clickable"
            style={{gridTemplateColumns:"60px 1fr 1fr"}}
            onClick={() => onPick && onPick(t.ticker)}>
            <span className="const-ticker">{t.ticker}</span>
            <span className="const-name">{t.name}</span>
            <span className="const-name" style={{color:"var(--text-dim)"}}>{t.basket_name}</span>
          </div>
        ))}
        {matches.length === 0 && (
          <div className="panel-explain" style={{paddingTop: 14}}>No matches.</div>
        )}
      </div>
    </div>
  );
}

function AttributionPanel({ attribution, fmtPct }) {
  const [win, setWin] = useState("3m");
  const data = attribution[win];
  if (!data) return <div className="panel-explain">No data for {win}</div>;
  
  const contribs = Object.entries(data.contribs || {}).sort((a,b) => Math.abs(b[1]) - Math.abs(a[1]));
  const factorTotal = contribs.reduce((s, [_, v]) => s + Math.abs(v), 0) || 1;
  
  return (
    <div className="attr-panel">
      <div className="panel-explain">
        Returns decomposed into factor-driven (commodity/macro) vs idiosyncratic (specific to basket).
      </div>
      <div className="attr-window-tabs">
        {["1m", "3m", "ytd", "1y"].map(w => (
          <button key={w} className={"attr-win" + (win === w ? " active" : "")} onClick={() => setWin(w)}>{w}</button>
        ))}
      </div>
      
      <div className="attr-summary">
        <div className="attr-row">
          <span className="attr-lab">Actual</span>
          <span className={"attr-val " + (data.actual >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(data.actual)}</span>
        </div>
        <div className="attr-row">
          <span className="attr-lab">Factor-driven</span>
          <span className={"attr-val " + (data.factor >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(data.factor)}</span>
        </div>
        <div className="attr-row attr-idio">
          <span className="attr-lab">Idiosyncratic</span>
          <span className={"attr-val " + (data.idio >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(data.idio)}</span>
        </div>
      </div>

      <div className="section-head" style={{marginTop:18}}>Factor contributions</div>
      <div className="contrib-list">
        {contribs.map(([name, v]) => {
          const pct = (Math.abs(v) / factorTotal) * 100;
          return (
            <div className="contrib-row" key={name}>
              <span className="contrib-name">{name}</span>
              <div className="contrib-bar-wrap">
                <div className="contrib-bar" style={{width: pct + "%", background: v >= 0 ? "var(--accent)" : "#4a9d96"}}></div>
              </div>
              <span className={"contrib-val " + (v >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(v)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Screener({ onSelect, hover }) {
  return (
    <>
      <div className="side-section">
        <div className="kicker">Screener · Sub-industries</div>
        <h2><em>23 baskets</em></h2>
        <div style={{marginTop: 14}}>
          <div className="screener-row" style={{borderBottom:"1px solid var(--border-hi)", cursor:"default", paddingBottom:6, gridTemplateColumns:"1fr 64px 64px"}}>
            <div style={{fontFamily:"var(--mono)", fontSize:9, letterSpacing:"0.16em", color:"var(--text-dim)", textTransform:"uppercase"}}>Subgroup</div>
            <div style={{fontFamily:"var(--mono)", fontSize:9, letterSpacing:"0.16em", color:"var(--text-dim)", textTransform:"uppercase", textAlign:"right"}}>60D</div>
            <div style={{fontFamily:"var(--mono)", fontSize:9, letterSpacing:"0.16em", color:"var(--text-dim)", textTransform:"uppercase", textAlign:"right"}}>1Y</div>
          </div>
          {NODES.map(n => {
            return (
              <div
                key={n.id}
                className="screener-row"
                onClick={() => onSelect(n.id)}
                style={{gridTemplateColumns:"1fr 64px 64px", ...(hover === n.id ? { background: "rgba(212,175,55,0.08)" } : {})}}
              >
                <div className="nm">{n.name}<span className="sub">{DISTRICTS[n.district]?.label}</span></div>
                <div className={"pct " + (n.r60 == null ? "" : (n.r60 >= 0 ? "pct-pos" : "pct-neg"))} style={{fontSize:11}}>{n.r60 == null ? "\u00b7" : (n.r60 >= 0 ? "+" : "") + (n.r60 * 100).toFixed(1) + "%"}</div>
                <div className={"pct " + (n.r1y == null ? "" : (n.r1y >= 0 ? "pct-pos" : "pct-neg"))} style={{fontSize:11}}>{n.r1y == null ? "\u00b7" : (n.r1y >= 0 ? "+" : "") + (n.r1y * 100).toFixed(1) + "%"}</div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

/* ---------------- App ---------------- */

/* ---------------- Bottom strip: active signal pairs ---------------- */
function SignalsStrip({ onSelect, onSeedLab, onSelectStock }) {
  // Three sub-rows · top 4 of each kind
  const basketBasket = (window.ALL_PAIRS || [])
    .slice()
    .sort((a, b) => Math.abs(b.z || 0) - Math.abs(a.z || 0))
    .slice(0, 4);
  const stockStock  = (window.STOCK_CORR_PAIRS || []).slice(0, 4);
  const basketStock = (window.BASKET_STOCK_CORR_PAIRS || []).slice(0, 4);

  return (
    <div className="signals-strip">
      <div className="signals-head">
        <div className="signals-title">Active Signals</div>
        <div className="signals-sub">between groups + between names · top 4 each</div>
        <AsOfChip inline />
      </div>

      {/* Basket ↔ Basket · z-scored pair signals */}
      <div className="signals-row-label">Basket ↔ Basket</div>
      <div className="signals-grid">
        {basketBasket.length === 0 && <div className="dash-empty">No basket pair signals - run phase3.</div>}
        {basketBasket.map((p, i) => {
          const stretched = Math.abs(p.z || 0) >= 1.5;
          const longId = p.long || p.a, shortId = p.short || p.b;
          const long = NODE_BY_ID[longId], short = NODE_BY_ID[shortId];
          return (
            <div className={"signal-card" + (stretched ? " stretched" : "")} key={i}>
              <div className="signal-thesis">{p.thesis || "(no thesis)"}</div>
              <div className="signal-pair">
                <span className="signal-leg long" onClick={() => onSelect(longId)}>
                  <span className="signal-side">L</span>
                  <span className="signal-name">{long?.name || longId}</span>
                </span>
                <span className="signal-leg short" onClick={() => onSelect(shortId)}>
                  <span className="signal-side">S</span>
                  <span className="signal-name">{short?.name || shortId}</span>
                </span>
              </div>
              <div className="signal-meta">
                <span className={"signal-z " + ((p.z || 0) >= 0 ? "pos" : "neg")}>z {(p.z || 0) >= 0 ? "+" : ""}{(p.z || 0).toFixed(2)}</span>
                {p.ret_3m != null && <span className="signal-meta-x">3m {(p.ret_3m*100 >= 0 ? "+" : "") + (p.ret_3m*100).toFixed(1)}%</span>}
                {p.corr_now != null && <span className="signal-meta-x">corr {p.corr_now.toFixed(2)}</span>}
                {onSeedLab && (
                  <button className="signal-lab-btn"
                    onClick={(e) => { e.stopPropagation(); onSeedLab(longId, shortId); }}
                    title="Open in Backtest Lab">→ Lab</button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Stock ↔ Stock · raw 5y weekly correlations */}
      <div className="signals-row-label">Stock ↔ Stock</div>
      <div className="signals-corr-grid">
        {stockStock.length === 0 && <div className="dash-empty">No stock correlations.</div>}
        {stockStock.map((p, i) => (
          <CorrCard key={i}
            pair={p} kind="stock-stock"
            onSelectBasket={onSelect} onSelectStock={onSelectStock}/>
        ))}
      </div>

      {/* Basket ↔ Stock · cross-grouping correlations */}
      <div className="signals-row-label">Basket ↔ Stock</div>
      <div className="signals-corr-grid">
        {basketStock.length === 0 && <div className="dash-empty">No basket↔stock correlations.</div>}
        {basketStock.map((p, i) => (
          <CorrCard key={i}
            pair={p} kind="basket-stock"
            onSelectBasket={onSelect} onSelectStock={onSelectStock}/>
        ))}
      </div>
    </div>
  );
}

/* ---------------- Correlation card (stock-stock / basket-stock) ----------------
   Used alongside the basket-basket pair-signal cards in the Active Signals
   surfaces. `kind` decides whether each side is a basket id or a ticker. */
function CorrCard({ pair, kind, onSelectBasket, onSelectStock }) {
  const c = pair.c;
  const sign = c >= 0 ? "pos" : "neg";
  const lookupBasket = (id) => (window.NODE_BY_ID || {})[id];
  const lookupTicker = (tk) => (window.CONSTITUENT_BY_TICKER || {})[tk];

  const renderSide = (side, type) => {
    if (type === "basket") {
      const n = lookupBasket(side);
      return (
        <span className="corr-side" onClick={(e) => { e.stopPropagation(); onSelectBasket && onSelectBasket(side); }}>
          <span className="corr-side-label">{n ? n.name : side}</span>
        </span>
      );
    }
    const ct = lookupTicker(side);
    return (
      <span className="corr-side" onClick={(e) => { e.stopPropagation(); onSelectStock && onSelectStock(side); }}>
        <span className="corr-side-ticker">{side}</span>
        {ct && <span className="corr-side-label">{ct.name}</span>}
      </span>
    );
  };

  const aType = kind === "stock-stock" ? "stock" : "basket";
  const bType = kind === "basket-basket" ? "basket" : "stock";

  return (
    <div className={"corr-card corr-card-" + sign}>
      <div className="corr-card-pair">
        {renderSide(pair.a, aType)}
        <span className="corr-link">↔</span>
        {renderSide(pair.b, bType)}
      </div>
      <div className={"corr-card-val " + (c >= 0 ? "pct-pos" : "pct-neg")}>
        ρ {c >= 0 ? "+" : ""}{c.toFixed(2)}
      </div>
    </div>
  );
}

/* ---------------- Sparkline (placeholder, deterministic by seed) ----------------
   Until we wire rolling 60d-z paths from phase3, generate a plausible random walk
   biased toward the pair's current z. Keeps the visual story honest: high-z
   pairs have visibly stretched sparklines, low-z ones look flat. */
function Sparkline({ seed = "x", target = 0, width = 64, height = 18 }) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h = ((h ^ seed.charCodeAt(i)) * 16777619) >>> 0;
  }
  const rng = () => { h = (h * 1664525 + 1013904223) >>> 0; return h / 0xffffffff; };
  const N = 24;
  const pts = [];
  let v = 0;
  for (let i = 0; i < N; i++) { v += (rng() - 0.5) * 0.6; pts.push(v); }
  // Bias the tail toward the actual z so the line "lands" at the displayed value.
  const drift = target - pts[N - 1];
  for (let i = 0; i < N; i++) pts[i] += (i / (N - 1)) * drift;
  const min = Math.min(...pts, target), max = Math.max(...pts, target);
  const span = (max - min) || 1;
  const path = pts.map((p, i) => {
    const x = (i / (N - 1)) * width;
    const y = height - 1 - ((p - min) / span) * (height - 2);
    return (i === 0 ? "M" : "L") + " " + x.toFixed(1) + " " + y.toFixed(1);
  }).join(" ");
  const stroke = Math.abs(target) >= 1.5 ? "var(--accent)" : "var(--text-mute)";
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="sparkline" aria-hidden="true">
      <path d={path} stroke={stroke} strokeWidth="1" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ---------------- Dashboard (left 80% of the landing page) ---------------- */
function Dashboard({ onSelectBasket, onSelectStock, onSeedLab }) {
  const fmtPct = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
  const fmtZ = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + v.toFixed(2);
  const fmtBeta = (v) => v == null ? "·" : v.toFixed(2);

  /* 1. Active Signals - split into two rows.
        Top 4 basket↔basket pair signals (z-scored, between groups).
        Top 4 stock↔stock correlations (raw 5y weekly ρ). */
  const basketSignals = (window.ALL_PAIRS || [])
    .slice()
    .sort((a, b) => Math.abs(b.z || 0) - Math.abs(a.z || 0))
    .slice(0, 4);
  const stockCorrs = (window.STOCK_CORR_PAIRS || []).slice(0, 4);
  const signals = basketSignals;  // back-compat for the diagnostic console.log

  /* 2. Regime Shifts */
  const regime = (window.REGIME_ALERTS || [])
    .slice()
    .sort((a, b) => Math.abs(b.z_score || 0) - Math.abs(a.z_score || 0))
    .slice(0, 5);

  /* 3. Top Residuals (cross-basket) */
  const allResids = [];
  for (const n of (window.NODES || [])) {
    for (const c of (n.constituents || [])) {
      if (c.residual_z60d != null) {
        allResids.push({
          ticker: c.ticker, z: c.residual_z60d, r60: c.return_60d,
          name: c.name, node_id: n.id, node_name: n.name,
        });
      }
    }
  }
  allResids.sort((a, b) => Math.abs(b.z || 0) - Math.abs(a.z || 0));
  const topResiduals = allResids.slice(0, 5);

  /* 4. IVR Divergence */
  const ivrTop = (window.NODES || [])
    .filter(n => n.ivr && n.ivr.gap_4w != null)
    .slice()
    .sort((a, b) => Math.abs(b.ivr.gap_4w) - Math.abs(a.ivr.gap_4w))
    .slice(0, 5);

  /* 5. Dispersion Rising - current idio_share at 3m window.
        Raw idio_share blows up when |actual| ~ 0; for a sane sort we use
        |idio| / (|factor| + |idio|) as the dispersion fraction (0..1). */
  const dispersion = (window.NODES || [])
    .map(n => {
      const a = n.attribution && n.attribution["3m"];
      if (!a) return null;
      const abs_idio = Math.abs(a.idio || 0);
      const denom = Math.abs(a.factor || 0) + abs_idio;
      const share = denom > 0 ? abs_idio / denom : 0;
      return { node: n, share, raw: a.idio_share };
    })
    .filter(Boolean)
    .sort((a, b) => b.share - a.share)
    .slice(0, 5);

  // Diagnostic counts in console - explicit confirmation per task gate.
  React.useEffect(() => {
    console.log("[dashboard] signals:", signals.length,
                "· regime:", regime.length,
                "· residuals:", topResiduals.length,
                "· ivr:", ivrTop.length,
                "· dispersion:", dispersion.length);
  }, [signals.length, regime.length, topResiduals.length, ivrTop.length, dispersion.length]);

  return (
    <div className="dash">
      {/* === 1. Active Signals · split into basket↔basket + stock↔stock === */}
      <section className="dash-sec">
        <header className="dash-sec-head">
          <h3>Active Signals</h3>
          <span className="dash-sec-kicker">between groups + between names · top 4 each</span>
        </header>

        {/* Basket ↔ Basket · z-scored pair signals */}
        <div className="dash-signal-row-label">Basket ↔ Basket</div>
        <div className="dash-signals">
          {basketSignals.length === 0 && <div className="dash-empty">No basket pair signals - re-run phase3.</div>}
          {basketSignals.map((p, i) => {
            const longN = NODE_BY_ID[p.long], shortN = NODE_BY_ID[p.short];
            const stretched = Math.abs(p.z || 0) >= 1.5;
            return (
              <div key={i} className={"dash-signal" + (stretched ? " stretched" : "")}
                onClick={() => onSelectBasket && onSelectBasket(p.long)}>
                <div className="dash-signal-thesis">{p.thesis || "(no thesis)"}</div>
                <div className="dash-signal-pair">
                  <span className="signal-leg long">
                    <span className="signal-side">L</span>
                    <span className="signal-name">{longN?.name || p.long}</span>
                  </span>
                  <span className="signal-leg short">
                    <span className="signal-side">S</span>
                    <span className="signal-name">{shortN?.name || p.short}</span>
                  </span>
                </div>
                <div className="dash-signal-meta">
                  <span className={"signal-z " + ((p.z || 0) >= 0 ? "pos" : "neg")}>
                    z {fmtZ(p.z)}
                  </span>
                  <Sparkline seed={p.long + ":" + p.short} target={p.z || 0} />
                  {p.ret_3m != null && <span className="signal-meta-x">3m {fmtPct(p.ret_3m)}</span>}
                  {p.corr_now != null && <span className="signal-meta-x">corr {p.corr_now.toFixed(2)}</span>}
                  {onSeedLab && (
                    <button className="signal-lab-btn"
                      onClick={(e) => { e.stopPropagation(); onSeedLab(p.long, p.short); }}
                      title="Open in Backtest Lab">→ Lab</button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Stock ↔ Stock · raw 5y weekly correlations */}
        <div className="dash-signal-row-label" style={{marginTop: 12}}>Stock ↔ Stock</div>
        <div className="dash-corr-grid">
          {stockCorrs.length === 0 && <div className="dash-empty">No stock correlations available.</div>}
          {stockCorrs.map((p, i) => (
            <CorrCard key={i}
              pair={p}
              kind="stock-stock"
              onSelectBasket={onSelectBasket}
              onSelectStock={onSelectStock}/>
          ))}
        </div>
      </section>

      {/* === 2-5. Four-column analysis grid === */}
      <div className="dash-grid">

      <section className="dash-sec">
        <header className="dash-sec-head">
          <h3>Regime Shifts</h3>
          <span className="dash-sec-kicker">β breaks · top 5</span>
        </header>
        <div className="dash-list">
          {regime.length === 0 && <div className="dash-empty">No regime alerts.</div>}
          {regime.map((r, i) => {
            const n = NODE_BY_ID[r.basket];
            const dirCls = r.direction === "decreased" ? "pct-neg" : "pct-pos";
            return (
              <div key={i} className="dash-row clickable"
                onClick={() => onSelectBasket && onSelectBasket(r.basket)}>
                <div className="dash-row-main">
                  <span className="dash-row-id">{n ? n.name : r.basket}</span>
                  <span className="dash-row-tag">β to {r.driver}</span>
                </div>
                <div className="dash-row-meta">
                  <span>was {fmtBeta(r.long_run_mean)}</span>
                  <span>→</span>
                  <span className={dirCls}>now {fmtBeta(r.current_beta)}</span>
                  <span className={"dash-row-z " + (r.z_score >= 0 ? "pct-pos" : "pct-neg")}>
                    z={fmtZ(r.z_score)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* === 3. Top Residuals === */}
      <section className="dash-sec">
        <header className="dash-sec-head">
          <h3>Top Residuals</h3>
          <span className="dash-sec-kicker">cross-basket · top 5</span>
        </header>
        <div className="dash-list">
          {topResiduals.length === 0 && <div className="dash-empty">No residuals.</div>}
          {topResiduals.map((r) => {
            const stretched = Math.abs(r.z) > 1.5;
            return (
              <div key={r.ticker} className={"dash-row clickable" + (stretched ? " stretched" : "")}
                onClick={() => onSelectStock && onSelectStock(r.ticker)}>
                <div className="dash-row-main">
                  <span className="dash-row-id">{r.ticker}</span>
                  <span className={"dash-row-z " + (r.z >= 0 ? "pct-pos" : "pct-neg")}>
                    {fmtZ(r.z)}z
                  </span>
                  <span className="dash-row-tag">{r.node_id}</span>
                </div>
                <div className="dash-row-meta">
                  <span className="signal-name">{r.name}</span>
                  <span className={r.r60 != null && r.r60 >= 0 ? "pct-pos" : "pct-neg"}>
                    60d {fmtPct(r.r60)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* === 4. IVR Divergence === */}
      <section className="dash-sec">
        <header className="dash-sec-head">
          <h3>IVR Divergence</h3>
          <span className="dash-sec-kicker">implied vs realized · top 5</span>
        </header>
        <div className="dash-list">
          {ivrTop.length === 0 && <div className="dash-empty">No IVR data.</div>}
          {ivrTop.map((n) => (
            <div key={n.id} className="dash-row clickable"
              onClick={() => onSelectBasket && onSelectBasket(n.id)}>
              <div className="dash-row-main">
                <span className="dash-row-id">{n.name}</span>
                <span className={"dash-row-z " + (n.ivr.gap_4w >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(n.ivr.gap_4w)}</span>
              </div>
              <div className="dash-row-meta">
                <span>R <span className={n.ivr.actual_4w >= 0 ? "pct-pos" : "pct-neg"}>{fmtPct(n.ivr.actual_4w)}</span></span>
                <span>I <span className={n.ivr.implied_4w >= 0 ? "pct-pos" : "pct-neg"}>{fmtPct(n.ivr.implied_4w)}</span></span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* === 5. Dispersion Rising === */}
      <section className="dash-sec">
        <header className="dash-sec-head">
          <h3>Dispersion Rising</h3>
          <span className="dash-sec-kicker">idio share · 3m · top 5</span>
        </header>
        <div className="dash-list">
          {dispersion.length === 0 && <div className="dash-empty">No attribution data.</div>}
          {dispersion.map(({ node: n, share }) => (
            <div key={n.id} className="dash-row clickable"
              onClick={() => onSelectBasket && onSelectBasket(n.id)}>
              <div className="dash-row-main">
                <span className="dash-row-id">{n.name}</span>
                <span className="dash-row-z" style={{color: "var(--accent)"}}>{(share * 100).toFixed(0)}%</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      </div>{/* /dash-grid */}
    </div>
  );
}

/* ---------------- Map Column (right 20% of the landing page) ----------------
   Five abstract motifs (Ocean → Port → Subsurface → Surface → End User) layered
   with a unifying dot-grid + edge ruler so the column reads as a single
   technical-drawing tableau, not five separate cards. The "Map" CTA sits
   centered with the arrow stacked underneath; the arrow grows from a dot on
   hover. The whole column is the click target. */
function MapColumn({ onOpenAtlas }) {
  const W = 280;
  const BAND_H = 175;
  const TOTAL_H = BAND_H * 5;

  // Strokes lean toward an ivory/parchment ink so the gold CTA reads on top.
  const ink    = "rgba(240,232,208,0.42)";
  const inkDim = "rgba(240,232,208,0.22)";

  // Layer 01 · Ocean - bathymetric depth contours
  const oceanMotif = (
    <g stroke={ink} fill="none">
      <g strokeWidth="0.6">
        <path d="M 0 70 Q 70 60 140 70 T 280 70"/>
        <path d="M 0 92 Q 70 80 140 92 T 280 92"/>
        <path d="M 0 114 Q 70 102 140 114 T 280 114"/>
        <path d="M 0 136 Q 70 124 140 136 T 280 136"/>
        <path d="M 0 158 Q 70 146 140 158 T 280 158"/>
      </g>
      <g stroke={inkDim} strokeWidth="0.5">
        <circle cx="38" cy="42" r="5"/>
        <path d="M 38 37 L 38 47 M 33 42 L 43 42"/>
      </g>
    </g>
  );

  // Layer 02 · Port & Terminal - engineering plan view
  const portMotif = (
    <g fill="none">
      <g stroke={ink} strokeWidth="0.7">
        <circle cx="58" cy="76" r="14"/>
        <circle cx="58" cy="76" r="9"/>
        <circle cx="98" cy="76" r="11"/>
        <circle cx="98" cy="76" r="6"/>
        <circle cx="178" cy="76" r="14"/>
        <circle cx="178" cy="76" r="9"/>
      </g>
      <g stroke={ink} strokeWidth="0.55">
        <line x1="0" y1="116" x2="280" y2="116"/>
        <line x1="0" y1="122" x2="280" y2="122"/>
        <line x1="0" y1="128" x2="280" y2="128"/>
      </g>
      <g stroke={inkDim} strokeWidth="0.5">
        {[0,1,2,3,4,5,6,7].map(i => (
          <path key={i} d={`M ${24 + i*32} 152 L ${40 + i*32} 168 L ${24 + i*32} 168 Z`} fill="none"/>
        ))}
      </g>
    </g>
  );

  // Layer 03 · Onshore Subsurface - well log / stratigraphic column
  const subsurfaceMotif = (
    <g fill="none">
      <g stroke={ink} strokeWidth="0.55">
        <rect x="36" y="36" width="76" height="22"/>
        <rect x="36" y="58" width="76" height="32"/>
        <rect x="36" y="90" width="76" height="22"/>
        <rect x="36" y="112" width="76" height="32"/>
      </g>
      <g stroke={inkDim} strokeWidth="0.4">
        {[0,1,2,3,4].map(i => <line key={i} x1={36} y1={61 + i*6} x2={112} y2={61 + i*6}/>)}
        {[0,1,2,3,4].map(i => <line key={"b"+i} x1={36} y1={115 + i*6} x2={112} y2={115 + i*6}/>)}
      </g>
      <line x1="178" y1="36" x2="178" y2="148" stroke={ink} strokeWidth="0.7" strokeDasharray="2 3"/>
      <path d="M 173 146 L 178 154 L 183 146 Z" fill={ink}/>
    </g>
  );

  // Layer 04 · Onshore Surface - closed elevation contours
  const surfaceMotif = (
    <g fill="none">
      <g stroke={ink} strokeWidth="0.6">
        <ellipse cx="140" cy="92" rx="108" ry="42"/>
        <ellipse cx="140" cy="92" rx="84" ry="32"/>
        <ellipse cx="140" cy="92" rx="60" ry="22"/>
        <ellipse cx="140" cy="92" rx="34" ry="12"/>
      </g>
      <path d="M 138 87 L 144 87 L 141 82 Z" fill="none" stroke={ink} strokeWidth="0.6"/>
      <line x1="20" y1="148" x2="240" y2="128" stroke={inkDim} strokeWidth="0.5" strokeDasharray="3 3"/>
    </g>
  );

  // Layer 05 · End User - one-line schematic
  const endUserMotif = (
    <g fill="none">
      <line x1="20" y1="44" x2="260" y2="44" stroke={ink} strokeWidth="1.4"/>
      <line x1="80" y1="44" x2="80" y2="64" stroke={ink} strokeWidth="0.7"/>
      <circle cx="80" cy="74" r="9" stroke={ink} strokeWidth="0.7"/>
      <circle cx="80" cy="88" r="9" stroke={ink} strokeWidth="0.7"/>
      <line x1="80" y1="98" x2="80" y2="112" stroke={ink} strokeWidth="0.7"/>
      <line x1="20" y1="112" x2="260" y2="112" stroke={ink} strokeWidth="1"/>
      <line x1="160" y1="112" x2="160" y2="138" stroke={ink} strokeWidth="0.7"/>
      <line x1="200" y1="112" x2="200" y2="138" stroke={ink} strokeWidth="0.7"/>
      <g stroke={ink} strokeWidth="0.7">
        <path d="M 156 138 L 164 146 M 164 138 L 156 146"/>
        <path d="M 196 138 L 204 146 M 204 138 L 196 146"/>
      </g>
    </g>
  );

  const motifs = [oceanMotif, portMotif, subsurfaceMotif, surfaceMotif, endUserMotif];

  return (
    <div className="map-column" onClick={onOpenAtlas} title="Open the full Atlas">
      <svg className="map-column-svg" width={W} height={TOTAL_H} viewBox={`0 0 ${W} ${TOTAL_H}`} preserveAspectRatio="xMidYMin meet">
        <defs>
          {/* Universal dot grid - ties every layer together visually */}
          <pattern id="mapBgGrid" width="14" height="14" patternUnits="userSpaceOnUse">
            <circle cx="0.5" cy="0.5" r="0.5" fill="rgba(240,232,208,0.10)"/>
          </pattern>
          {/* Diagonal hatch - quiet texture in the corners */}
          <pattern id="mapBgHatch" width="22" height="22" patternUnits="userSpaceOnUse" patternTransform="rotate(28)">
            <line x1="0" y1="0" x2="0" y2="22" stroke="rgba(240,232,208,0.05)" strokeWidth="0.5"/>
          </pattern>
        </defs>

        {/* Universal background layers */}
        <rect x="0" y="0" width={W} height={TOTAL_H} fill="url(#mapBgGrid)"/>
        <rect x="0" y="0" width={W} height={TOTAL_H} fill="url(#mapBgHatch)"/>

        {/* Edge rulers · technical-drawing margins that span every layer */}
        <g stroke="rgba(240,232,208,0.18)" strokeWidth="0.4" fill="none">
          <line x1="8" y1="0" x2="8" y2={TOTAL_H}/>
          <line x1={W - 8} y1="0" x2={W - 8} y2={TOTAL_H}/>
          {Array.from({length: Math.floor(TOTAL_H / 14)}, (_, i) => i * 14).map(y => (
            <g key={y}>
              <line x1="4" y1={y} x2="8" y2={y}/>
              <line x1={W - 8} y1={y} x2={W - 4} y2={y}/>
            </g>
          ))}
          {Array.from({length: Math.floor(TOTAL_H / 70)}, (_, i) => i * 70).map(y => (
            <g key={"M" + y}>
              <line x1="2" y1={y} x2="10" y2={y} strokeWidth="0.6"/>
              <line x1={W - 10} y1={y} x2={W - 2} y2={y} strokeWidth="0.6"/>
            </g>
          ))}
        </g>

        {/* Corner brackets · top-left, top-right, bottom-left, bottom-right */}
        <g stroke="rgba(240,232,208,0.30)" strokeWidth="0.7" fill="none">
          <path d="M 4 16 L 4 4 L 16 4"/>
          <path d={`M ${W - 4} 16 L ${W - 4} 4 L ${W - 16} 4`}/>
          <path d={`M 4 ${TOTAL_H - 16} L 4 ${TOTAL_H - 4} L 16 ${TOTAL_H - 4}`}/>
          <path d={`M ${W - 4} ${TOTAL_H - 16} L ${W - 4} ${TOTAL_H - 4} L ${W - 16} ${TOTAL_H - 4}`}/>
        </g>

        {/* Continuous "trace" snaking through every motif - the connective thread */}
        <path
          d={`M 140 30
              C 200 ${BAND_H * 0.6}, 80 ${BAND_H * 1.2}, 140 ${BAND_H * 1.6}
              S 80 ${BAND_H * 2.6}, 140 ${BAND_H * 3.0}
              S 200 ${BAND_H * 4.0}, 140 ${BAND_H * 4.7}`}
          stroke="rgba(212,175,55,0.13)"
          strokeWidth="0.7"
          fill="none"
          strokeDasharray="3 5"
        />

        {/* Motifs · positioned within each band, no labels */}
        {motifs.map((m, i) => (
          <g key={i} transform={`translate(0 ${i * BAND_H})`}>{m}</g>
        ))}
      </svg>

      <div className="map-cta">
        <em className="map-cta-label">Map</em>
        <span className="map-cta-arrow" aria-hidden="true">
          <span className="dot"/>
          <span className="line"/>
          <span className="chevron"/>
        </span>
      </div>
    </div>
  );
}

/* ---------------- Stock-level signal computation (slim) ---------------- */
function computeStockSignals(c, node) {
  if (!c || !node) return [];
  const sigs = [];

  // 1. Residual - stock's own z60d
  const z = c.residual_z60d;
  if (z != null) {
    const firing = Math.abs(z) > 1.5;
    sigs.push({
      type: "residual",
      firing,
      score: Math.min(Math.abs(z) / 3, 1),
      direction: !firing ? "neutral" : (z > 0 ? "bear" : "bull"),
      z,
    });
  } else {
    sigs.push({ type: "residual", firing: false, score: 0, direction: "neutral" });
  }

  // 2. Attribution - stock's idio share of total magnitude (3m)
  const a3 = c.attribution && c.attribution["3m"];
  if (a3) {
    const absIdio = Math.abs(a3.idio || 0);
    const denom = Math.abs(a3.factor || 0) + absIdio;
    const share = denom > 0 ? absIdio / denom : 0;
    const firing = share > 0.6;
    sigs.push({
      type: "attribution",
      firing,
      score: Math.min(share, 1),
      direction: "neutral",
      idio_pct: share * 100,
      idio: a3.idio,
      factor: a3.factor,
    });
  } else {
    sigs.push({ type: "attribution", firing: false, score: 0, direction: "neutral",
                missing: true });
  }

  // 3. Peer divergence - stock 60d vs basket median 60d
  const peers = (node.constituents || [])
    .filter(p => p.ticker !== c.ticker && p.return_60d != null)
    .map(p => p.return_60d)
    .sort((a, b) => a - b);
  if (peers.length >= 3 && c.return_60d != null) {
    const median = peers[Math.floor(peers.length / 2)];
    const delta = c.return_60d - median;
    const firing = Math.abs(delta) > 0.10;
    sigs.push({
      type: "peer",
      firing,
      score: Math.min(Math.abs(delta) / 0.30, 1),
      direction: !firing ? "neutral" : (delta > 0 ? "bull" : "bear"),
      delta,
      basket_median: median,
      stock_r60: c.return_60d,
    });
  } else {
    sigs.push({ type: "peer", firing: false, score: 0, direction: "neutral" });
  }

  // 4. Earnings - placeholder; consolidator doesn't emit earnings dates today
  sigs.push({ type: "earnings", firing: false, score: 0, direction: "neutral",
              missing: true });

  return sigs;
}

function StockSignalStack({ ticker, c, node }) {
  const fmtPct = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
  const fmtZ   = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + v.toFixed(2);
  const sigs = useMemo(() => computeStockSignals(c, node), [ticker, c, node]);
  const firing = sigs.filter(s => s.firing).length;
  const dirCls = (d) => d === "bull" ? "sig-bull" : d === "bear" ? "sig-bear" : "sig-neutral";
  const LABEL = { residual: "Residual", attribution: "Attribution", peer: "Peer Divergence", earnings: "Earnings" };
  return (
    <div className="signal-stack">
      <div className="sig-density">
        <div className="sig-density-label">
          Stock signals · <span className="accent">{firing}</span> of {sigs.length} firing
        </div>
        <div className="sig-density-bar">
          {sigs.map(s => (
            <span key={s.type}
              className={"sig-square " + (s.firing ? dirCls(s.direction) : "sig-off")}
              title={LABEL[s.type] + (s.firing ? "" : " · silent")}
            />
          ))}
        </div>
      </div>
      <div className="sig-cards">
        {sigs.map(s => (
          <div key={s.type} className={"sig-card " + dirCls(s.direction) + (s.firing ? "" : " silent")}>
            <div className="sig-card-head">
              <span className="sig-type">{LABEL[s.type]}</span>
              {s.missing && <span className="sig-status">data pending</span>}
              {!s.firing && !s.missing && <span className="sig-status">silent</span>}
            </div>
            {s.type === "residual" && (
              s.firing ? (
                <>
                  <div className="sig-summary">Stretched {s.z > 0 ? "above" : "below"} basket trend · |z|&gt;1.5 · potential mean-revert</div>
                  <div className="sig-meta"><span className={"sig-num " + (s.z >= 0 ? "pct-pos" : "pct-neg")}>{fmtZ(s.z)}z</span></div>
                </>
              ) : <div className="sig-summary">{s.z != null ? `z = ${fmtZ(s.z)}` : "No residual data"} · within ±1.5z of basket.</div>
            )}
            {s.type === "attribution" && !s.missing && (
              s.firing ? (
                <>
                  <div className="sig-summary">{s.idio_pct.toFixed(0)}% idio (3m) · stock-specific story dominates factor exposure</div>
                  <div className="sig-meta"><span className="sig-num accent">{s.idio_pct.toFixed(0)}%</span></div>
                </>
              ) : <div className="sig-summary">Mostly factor-driven (3m). Idio share &lt;60%.</div>
            )}
            {s.type === "attribution" && s.missing && (
              <div className="sig-summary">Name-level attribution not in current JSON - re-run consolidate.</div>
            )}
            {s.type === "peer" && (
              s.firing ? (
                <>
                  <div className="sig-summary">
                    60d {fmtPct(s.stock_r60)} vs basket median {fmtPct(s.basket_median)} · {s.delta > 0 ? "leading" : "lagging"} peers
                  </div>
                  <div className="sig-meta"><span className={"sig-num " + (s.delta >= 0 ? "pct-pos" : "pct-neg")}>Δ {fmtPct(s.delta)}</span></div>
                </>
              ) : <div className="sig-summary">In line with basket median (within ±10pp).</div>
            )}
            {s.type === "earnings" && (
              <div className="sig-summary">Earnings calendar not embedded in pipeline · pending.</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- Stock-level drawer view ---------------- */
function StockDrawer({ ticker, onClose, onSelectBasket }) {
  const c = (window.CONSTITUENT_BY_TICKER || {})[ticker];
  if (!c) return (
    <div className="side-section drawer">
      <div className="layer-tag">Stock</div>
      <h2><em>{ticker}</em></h2>
      <div className="panel-explain">No record found for this ticker.</div>
      <button className="close" onClick={onClose}>‹ Close</button>
    </div>
  );

  const node = NODE_BY_ID[c.node_id];
  const asOfRow = <div style={{ margin: "6px 0 2px" }}><AsOfChip inline /></div>;
  const fmtPct = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
  const fmtZ = (v) => v == null ? "·" : (v >= 0 ? "+" : "") + v.toFixed(2);
  const z = c.residual_z60d;
  const zStretched = z != null && Math.abs(z) > 1.5;

  // Loadings: stock vs basket
  const loadings = c.driver_loadings || {};
  const basketLoadings = node ? (node.basket_loadings || {}) : {};
  const driverKeys = new Set([...Object.keys(loadings), ...Object.keys(basketLoadings)]);
  const driverRows = Array.from(driverKeys).map(k => ({
    driver: k,
    name: (loadings[k] && loadings[k].beta) ?? null,
    basket: (basketLoadings[k] && basketLoadings[k].beta) ?? null,
  }));
  driverRows.sort((a, b) => Math.abs(b.name || 0) - Math.abs(a.name || 0));

  // Peers: top 3 in same basket by 60d-return proximity (intra-basket pair-corr
  // matrix is not embedded per-pair, so this is the documented fallback).
  const peers = (node ? (node.constituents || []) : [])
    .filter(p => p.ticker !== c.ticker && p.return_60d != null && c.return_60d != null)
    .map(p => ({ ...p, dist: Math.abs((p.return_60d || 0) - (c.return_60d || 0)) }))
    .sort((a, b) => a.dist - b.dist)
    .slice(0, 3);

  return (
    <div className="side-section drawer stock-drawer">
      {/* Identity */}
      <div className="layer-tag">Stock</div>
      <div className="ticker-big">{c.ticker}</div>
      <h2><em>{c.name}</em></h2>
      {asOfRow}
      <button className="close" style={{marginTop:12}}
        onClick={() => onSelectBasket && onSelectBasket(c.node_id)}>
        ‹ Back to {node ? node.name : c.node_id}
      </button>

      {/* Stock signal stack - slim version of the basket signal universe */}
      <div className="section-head" style={{marginTop:22}}>Signals</div>
      <StockSignalStack ticker={ticker} c={c} node={node} />

      {/* Performance vs basket */}
      <div className="section-head" style={{marginTop:22}}>Performance vs basket · 60d</div>
      <div className="ivr-grid">
        <div className="ivr-cell">
          <div className="ivr-lab">Stock</div>
          <div className={"ivr-val " + ((c.return_60d || 0) >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(c.return_60d)}</div>
        </div>
        <div className="ivr-cell">
          <div className="ivr-lab">Basket</div>
          <div className={"ivr-val " + ((node?.r60 || 0) >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(node?.r60)}</div>
        </div>
        <div className="ivr-cell">
          <div className="ivr-lab">z60d (resid)</div>
          <div className={"ivr-val bold " + (z == null ? "" : (zStretched ? "pct-stretch" : (z >= 0 ? "pct-pos" : "pct-neg")))}>{fmtZ(z)}</div>
        </div>
      </div>

      {/* Factor exposures · bars centered at 50%, each half scaled to a per-row max */}
      <div className="section-head" style={{marginTop:22}}>Factor exposures · stock vs basket</div>
      {Object.keys(loadings).length === 0 ? (
        <div className="panel-explain">
          name-level loadings not in current JSON - re-run consolidate
        </div>
      ) : (
        <div className="contrib-list">
          {driverRows.map(r => {
            // Per-row max so each row's bars stay readable. Each side maxes at
            // 50% of the bar wrap (positive = right of midline, negative = left).
            const maxAbs = Math.max(Math.abs(r.name || 0), Math.abs(r.basket || 0), 0.01);
            const stockHalf  = (Math.abs(r.name   || 0) / maxAbs) * 50;
            const basketHalf = (Math.abs(r.basket || 0) / maxAbs) * 50;
            return (
              <div key={r.driver} className="loading-row">
                <span className="contrib-name">{r.driver}</span>
                <div className="loading-bars">
                  <div className="loading-bar-wrap">
                    <div className="loading-bar stock" style={{
                      width: stockHalf + "%",
                      marginLeft: (r.name || 0) >= 0 ? "50%" : (50 - stockHalf) + "%",
                    }}/>
                  </div>
                  <div className="loading-bar-wrap">
                    <div className="loading-bar basket" style={{
                      width: basketHalf + "%",
                      marginLeft: (r.basket || 0) >= 0 ? "50%" : (50 - basketHalf) + "%",
                    }}/>
                  </div>
                </div>
                <span className="contrib-val" style={{fontSize:10, color:"var(--text-mute)"}}>
                  {r.name != null ? r.name.toFixed(2) : "·"}
                  {r.basket != null && <span style={{color:"var(--text-dim)"}}> / {r.basket.toFixed(2)}</span>}
                </span>
              </div>
            );
          })}
          <div className="panel-explain" style={{marginTop:8, opacity:0.7}}>gold = stock · grey = basket</div>
        </div>
      )}

      {/* Attribution */}
      <div className="section-head" style={{marginTop:22}}>Attribution · factor vs idio</div>
      {(c.attribution && Object.keys(c.attribution).length > 0)
        ? <AttributionPanel attribution={c.attribution} fmtPct={fmtPct} />
        : <div className="panel-explain">name-level attribution not in current JSON - re-run consolidate</div>}

      {/* Peers */}
      <div className="section-head" style={{marginTop:22}}>Peers · same basket, closest 60d return</div>
      <div className="const-table">
        {peers.length === 0 && <div className="panel-explain">Insufficient return data for peer match.</div>}
        {peers.map(p => (
          <div key={p.ticker} className="const-row clickable"
            onClick={() => onSelectBasket && onSelectBasket(c.node_id) /* drop back to basket; user re-clicks for stock */}>
            <span className="const-ticker">{p.ticker}</span>
            <span className="const-name">{p.name}</span>
            <span className={"const-pct " + (p.return_60d >= 0 ? "pct-pos" : "pct-neg")}>{fmtPct(p.return_60d)}</span>
          </div>
        ))}
      </div>

      <button className="close" style={{marginTop:18}} onClick={onClose}>‹ Close</button>
    </div>
  );
}

/* ---------------- Atlas view (the original full-map experience) ---------------- */
function AtlasView({ corrThreshold, setCorrThreshold, mode, setMode, hover, setHover, selected, setSelected, tt, setTt, sideCollapsed, setSideCollapsed, onSelectStock, onSeedLab }) {
  const [hoverDistrict, setHoverDistrict] = useState(null);
  const onHover = useCallback((id, e) => {
    setHover(id);
    setTt({ x: e.clientX, y: e.clientY, node: NODE_BY_ID[id] });
  }, [setHover, setTt]);
  const onLeave = useCallback(() => { setHover(null); setTt(null); }, [setHover, setTt]);
  const deselect = useCallback(() => { setSelected(null); }, [setSelected]);

  // Per-basket signal density · only computed when entering signal mode
  const signalCounts = useMemo(() => {
    if (mode !== "signal") return {};
    const out = {};
    for (const n of NODES) {
      out[n.id] = computeSignals(n).filter(s => s.firing).length;
    }
    return out;
  }, [mode]);

  return (
    <div className={"app atlas-view" + (sideCollapsed ? " side-collapsed" : "")}>
      <div className="map-stack">
        <div className={"map-pane " + ((hover || selected) ? "faded" : "")}>
          <MapTitle />
          <Compass />
          <InfoButton />
          {selected && (
            <button
              onClick={deselect}
              title="Deselect"
              style={{position:"absolute", top:24, right:88, zIndex:5,
                      background:"rgba(10,13,18,0.92)", border:"1px solid var(--border-hi)",
                      color:"var(--text-mute)", width:32, height:32, cursor:"pointer",
                      fontFamily:"var(--mono)", fontSize:14}}>×</button>
          )}
          <svg className="atlas" viewBox="0 0 1400 900" preserveAspectRatio="xMidYMid meet" onClick={() => deselect()}>
            <OceanAndCoast />
            <Zones />
            <Edges mode={mode} hover={hover} selected={selected} hoverDistrict={hoverDistrict} corrThreshold={corrThreshold} />
            <SubNodes hover={hover} selected={selected} onHover={onHover} onLeave={onLeave} onSelect={(id) => setSelected(id)} mode={mode} signalCounts={signalCounts} />
            <DistrictLabels onDistrictHover={mode === "flow" ? setHoverDistrict : null} />
          </svg>
          <ModeToggle mode={mode} setMode={setMode} />
          <Legend mode={mode} corrThreshold={corrThreshold} setCorrThreshold={setCorrThreshold} />
          {tt && <Tooltip {...tt} />}
        </div>
        <SignalsStrip onSelect={setSelected} onSeedLab={onSeedLab} onSelectStock={onSelectStock}/>
      </div>
      <button
        className="side-toggle"
        onClick={() => setSideCollapsed(c => !c)}
        title={sideCollapsed ? "Show screener" : "Collapse screener"}
      >{sideCollapsed ? "‹" : "›"}</button>
      <div className="side-pane">
        {selected
          ? <Drawer node={NODE_BY_ID[selected]} onClose={() => setSelected(null)} onSelectStock={onSelectStock} onSelectBasket={setSelected} />
          : <Screener onSelect={setSelected} hover={hover} />}
      </div>
    </div>
  );
}

/* ============================================================
   BACKTEST · in-browser engine + 3-column page
   ============================================================
   Data: derived from window.NODES[i].cumulative_returns (weekly samples).
   Single-name and factor-residual strategies need per-name weekly returns
   that aren't in the current JSON; those branches surface a documented
   pipeline-extension placeholder rather than fake data.

   Conventions:
   - returns are weekly log-style returns: r_t such that (1+r_t) compounds
   - position is in {-1, 0, +1}
   - PnL_t = position_{t-1} * r_t  (position decided on close, applied next bar)
   - turnover cost = 5bps each side, applied when |Δposition| > 0
*/

const TXN_COST_BPS = 5;

/* Convert a node's cumulative_returns ([{date, value}]) to a weekly return
   series. r_t = (1 + cum_t) / (1 + cum_{t-1}) - 1 with a leading 0. */
function basketWeeklyReturns(node) {
  const cum = (node && node.cumulative_returns) || [];
  if (cum.length < 2) return { dates: [], rets: [] };
  const dates = cum.map(p => p.date);
  const vals = cum.map(p => p.value);
  const rets = [0];
  for (let i = 1; i < vals.length; i++) {
    const a = 1 + vals[i - 1], b = 1 + vals[i];
    rets.push(a > 0 ? (b / a) - 1 : 0);
  }
  return { dates, rets };
}

/* Per-ticker weekly returns. Reads from window.WEEKLY_RETURNS_BY_TICKER
   (cumulative-return values aligned to window.WEEKLY_INDEX) and converts
   to weekly returns, trimmed to the ticker's first valid bar. */
function singleTickerWeeklyReturns(ticker) {
  const idx = window.WEEKLY_INDEX || [];
  const cum = (window.WEEKLY_RETURNS_BY_TICKER || {})[ticker];
  if (!cum || idx.length < 2 || cum.length < 2) return { dates: [], rets: [] };
  // Find first non-null value in cum
  let start = 0;
  while (start < cum.length && cum[start] == null) start++;
  if (cum.length - start < 2) return { dates: [], rets: [] };
  const dates = idx.slice(start);
  const vals = cum.slice(start);
  const rets = [0];
  for (let i = 1; i < vals.length; i++) {
    const a = 1 + (vals[i - 1] || 0), b = 1 + (vals[i] || 0);
    rets.push(a > 0 ? (b / a) - 1 : 0);
  }
  return { dates, rets };
}

/* Resolve a leg spec into a weekly-return series. A leg is either:
     { type: "basket", value: <node_id> }
     { type: "single", value: <ticker> }
   Returns { dates, rets, label, error } */
function resolveLeg(leg) {
  if (!leg || !leg.value) return { error: "leg unset" };
  if (leg.type === "basket") {
    const node = (window.NODES || []).find(n => n.id === leg.value);
    if (!node) return { error: `basket "${leg.value}" not found` };
    const w = basketWeeklyReturns(node);
    return { dates: w.dates, rets: w.rets, label: node.name };
  }
  if (leg.type === "single") {
    const w = singleTickerWeeklyReturns(leg.value);
    if (w.dates.length < 2) return { error: `ticker "${leg.value}" has no weekly returns` };
    const rec = (window.CONSTITUENT_BY_TICKER || {})[leg.value];
    return { dates: w.dates, rets: w.rets, label: rec ? `${leg.value} · ${rec.name}` : leg.value };
  }
  return { error: `unknown leg type "${leg.type}"` };
}

/* Align two leg series on their common date range. Returns
   { dates, retsA, retsB } where retsA[i] and retsB[i] are aligned. */
function alignLegs(a, b) {
  const aMap = new Map(a.dates.map((d, i) => [d, a.rets[i]]));
  const dates = [], retsA = [], retsB = [];
  for (let i = 0; i < b.dates.length; i++) {
    const d = b.dates[i];
    if (aMap.has(d)) {
      dates.push(d);
      retsA.push(aMap.get(d));
      retsB.push(b.rets[i]);
    }
  }
  return { dates, retsA, retsB };
}

/* === Position-style post-processor ===
   The base engines (mean-revert / trend-follow) emit {-1, 0, +1} positions.
   This layer applies the user's optional position style:
     - "binary"     · pass through (default)
     - "long-only"  · clip negative positions to 0 (only long trades taken)
     - "short-only" · clip positive positions to 0
   Then a leverage multiplier scales the result (0.5x .. 2.0x). */
function applyPositionStyle(positions, style, leverage = 1.0) {
  if (!positions) return positions;
  const lev = Number.isFinite(leverage) ? leverage : 1.0;
  return positions.map(p => {
    let q = p;
    if (style === "long-only" && q < 0) q = 0;
    else if (style === "short-only" && q > 0) q = 0;
    return q * lev;
  });
}

/* Rolling z-score of a series. Returns NaN until window full. */
function rollingZ(arr, win) {
  const out = new Array(arr.length).fill(NaN);
  for (let i = win - 1; i < arr.length; i++) {
    let sum = 0;
    for (let j = i - win + 1; j <= i; j++) sum += arr[j];
    const mean = sum / win;
    let v = 0;
    for (let j = i - win + 1; j <= i; j++) v += (arr[j] - mean) ** 2;
    const sd = Math.sqrt(v / Math.max(1, win - 1));
    out[i] = sd > 0 ? (arr[i] - mean) / sd : 0;
  }
  return out;
}

/* Cumulative log return into a level series for z-scoring or trend. */
function cumLogLevel(rets) {
  const out = new Array(rets.length);
  let acc = 0;
  for (let i = 0; i < rets.length; i++) {
    acc += Math.log(1 + (rets[i] || 0));
    out[i] = acc;
  }
  return out;
}

/* === Continuous-position engine ============================================
   Replaces the old binary generators. Both meanRevertPositions and
   trendFollowPositions now produce continuous positions in [-leverage,
   +leverage] plus an actions log capturing every position change.

   Signal mapping is via targetPosition() - a piecewise-linear ramp from
   `entryThreshold` (0% of leverage) to `saturation` (100% of leverage). For
   z-score strategies saturation is in z-units; for trend it's a return %.

   The position state machine prioritises:
     1. Stop-loss + trailing-stop (closes at any open bar)
     2. Max-holding period
     3. Sign-flip close (when target wants opposite side; same-bar re-entry)
     4. Walk toward target (entry / scale_in / scale_out / exit)
*/

function targetPosition(signal, params, type) {
  if (signal == null || !Number.isFinite(signal)) return 0;
  const entry      = params.entryThreshold;
  const exitT      = params.exitThreshold;
  const saturation = params.saturation;
  const leverage   = params.leverage;
  const direction = (type === "mean_revert" || type === "pair") ? -1
                  : (type === "trend") ? +1
                  : (type === "factor_residual") ? (params.fadeOrRide === "fade" ? -1 : +1)
                  : -1;
  const absSig = Math.abs(signal);
  if (absSig <= exitT) return 0;
  if (absSig <= entry) return 0;  // dead zone
  const span = Math.max(0.001, saturation - entry);
  const t = Math.min((absSig - entry) / span, 1);
  const sign = signal >= 0 ? 1 : -1;
  return direction * sign * leverage * t;
}

/* Long-horizon momentum on a price series · used by the trend filter to
   suppress counter-trend trades. Returns simple return over `lookback` bars,
   null until enough history. */
function computeMomentum(prices, lookback) {
  const N = prices.length;
  const out = new Array(N).fill(null);
  for (let t = lookback; t < N; t++) {
    const p0 = prices[t - lookback], p1 = prices[t];
    if (p0 != null && p0 > 0 && p1 != null) out[t] = (p1 - p0) / p0;
  }
  return out;
}

/* Shared engine. type ∈ {mean_revert, trend, pair, factor_residual}. */
function _generatePositions(signal, prices, params, type, opts) {
  const N = signal.length;
  const positions = new Array(N).fill(0);
  const actions = [];

  let pos = 0;
  let entryBar = null;
  let entryPrice = null;
  let peakPnL = 0;

  // Trend-filter momentum series (weekly bars: 50 ≈ ~1y; spec says 250
  // daily ~ same span). Enabled per opts; null when off.
  const longMom = (opts && opts.trendFilterEnabled)
    ? computeMomentum(prices, opts.trendFilterLookback || 50)
    : null;

  for (let t = 1; t < N; t++) {
    if (signal[t] == null || !Number.isFinite(signal[t])) {
      positions[t] = pos;
      continue;
    }

    let target = targetPosition(signal[t], params, type);

    // Trend filter - suppress counter-trend leg when long-horizon momentum
    // points the other way and is bigger than the threshold.
    if (longMom != null && longMom[t] != null) {
      const m = longMom[t];
      const thr = (opts && opts.trendFilterThreshold) || 0.30;
      if (target < 0 && m > thr) target = 0;
      if (target > 0 && m < -thr) target = 0;
    }

    // Persistence check (factor_residual fade only) - skip when residual has
    // been stretched > 70% of the lookback bars on the same side.
    if (type === "factor_residual" && params.fadeOrRide === "fade" && opts && opts.persistenceCheck) {
      const lookback = opts.persistenceLookback || 4;
      let stretchedCount = 0;
      const sgn = Math.sign(signal[t]);
      for (let k = Math.max(0, t - lookback); k < t; k++) {
        const s = signal[k];
        if (s != null && Math.sign(s) === sgn && Math.abs(s) > params.entryThreshold) stretchedCount++;
      }
      if (stretchedCount / Math.max(1, lookback) > 0.7) target = 0;
    }

    // Long-only / short-only post-clamp on the continuous target
    if (params.posStyle === "long-only"  && target < 0) target = 0;
    if (params.posStyle === "short-only" && target > 0) target = 0;

    // Priority 1: stop loss + trailing stop (trend only)
    if (Math.abs(pos) > 0.001 && entryPrice != null) {
      const pnl = pos * (prices[t] - entryPrice) / Math.max(0.0001, Math.abs(entryPrice));
      peakPnL = Math.max(peakPnL, pnl);
      if (pnl <= -params.stopLoss) {
        actions.push({ bar: t, type: "stop", from: pos, to: 0, signal: signal[t], price: prices[t] });
        pos = 0; entryBar = null; entryPrice = null; peakPnL = 0;
        positions[t] = pos;
        continue;
      }
      if (type === "trend" && peakPnL - pnl >= params.trailingStop) {
        actions.push({ bar: t, type: "trailing_stop", from: pos, to: 0, signal: signal[t], price: prices[t] });
        pos = 0; entryBar = null; entryPrice = null; peakPnL = 0;
        positions[t] = pos;
        continue;
      }
    }

    // Priority 2: max holding
    if (entryBar != null && (t - entryBar) >= params.maxHolding) {
      actions.push({ bar: t, type: "max_hold", from: pos, to: 0, signal: signal[t], price: prices[t] });
      pos = 0; entryBar = null; entryPrice = null; peakPnL = 0;
      positions[t] = pos;
      continue;
    }

    // Priority 3: sign-flip close (then fall through to potentially re-enter)
    if (Math.abs(pos) > 0.001 && Math.abs(target) > 0.001 && Math.sign(target) !== Math.sign(pos)) {
      actions.push({ bar: t, type: "flip_close", from: pos, to: 0, signal: signal[t], price: prices[t] });
      pos = 0; entryBar = null; entryPrice = null; peakPnL = 0;
    }

    // Priority 4: walk toward target
    const delta = target - pos;
    if (Math.abs(delta) > 0.01) {
      let actionType;
      if (Math.abs(pos) < 0.001 && Math.abs(target) > 0.001) actionType = "entry";
      else if (Math.abs(target) < 0.001) actionType = "exit";
      else if (Math.abs(target) > Math.abs(pos)) actionType = "scale_in";
      else actionType = "scale_out";

      actions.push({ bar: t, type: actionType, from: pos, to: target, signal: signal[t], price: prices[t] });

      if (Math.abs(pos) < 0.001 && Math.abs(target) > 0.001) {
        entryBar = t; entryPrice = prices[t]; peakPnL = 0;
      }
      pos = target;
      if (Math.abs(pos) < 0.001) {
        entryBar = null; entryPrice = null; peakPnL = 0;
      }
    }

    positions[t] = pos;
  }

  return { positions, actions };
}

/* Public entry points · keep historical names but delegate to the unified
   engine. Returns { positions, actions } now (not just positions). */
function meanRevertPositions(signal, prices, params, type, opts) {
  return _generatePositions(signal, prices, params, type || "mean_revert", opts || {});
}
function trendFollowPositions(signal, prices, params, opts) {
  return _generatePositions(signal, prices, params, "trend", opts || {});
}

/* PnL series given positions and forward returns. Position lags by 1 to
   avoid look-ahead. Transaction cost is now turnover-weighted: a 0.25 scale-in
   costs 0.25 × 5bps, not a full unit cost. This is required for honest
   evaluation of layered strategies. */
function applyPositionsToReturns(positions, weeklyRets) {
  const N = positions.length;
  const pnl = new Array(N).fill(0);
  let prev = 0;
  const txn = TXN_COST_BPS / 10000;
  for (let i = 1; i < N; i++) {
    const p = positions[i - 1];
    pnl[i] = p * (weeklyRets[i] || 0);
    const turnover = Math.abs(positions[i] - prev);
    if (turnover > 0.001) pnl[i] -= turnover * txn;
    prev = positions[i];
  }
  return pnl;
}

/* Build a price series from weekly returns · price[0] = 100, price[t] =
   price[t-1] × (1 + ret[t]). Used for stop-loss math + trend-filter momentum. */
function pricesFromReturns(rets) {
  const N = rets.length;
  const out = new Array(N).fill(100);
  for (let i = 1; i < N; i++) out[i] = out[i - 1] * (1 + (rets[i] || 0));
  return out;
}

function equityCurveFromPnl(pnl) {
  const out = new Array(pnl.length);
  let acc = 0;
  for (let i = 0; i < pnl.length; i++) { acc += pnl[i]; out[i] = acc; }
  return out;
}

/* Trade extraction · scan positions for entry/exit transitions and report.
   `ctx` carries the strategy's entry/exit thresholds, signal series, and a
   formatter so each trade can record HUMAN-READABLE criteria for both legs:
     - entry_criteria: "z +2.18 crossed ±1.50"
     - exit_criteria:  "z +0.42 inside ±0.50" / "stop loss" / "max hold"
*/
function extractTrades(positions, weeklyRets, dates, ctx = {}) {
  const {
    signal = null,
    signalLabel = "signal",
    entry = null, exit = null,
    stopPct = null, maxHoldWeeks = null,
    fmtSignal = (v) => Number.isFinite(v) ? (v >= 0 ? "+" : "") + v.toFixed(2) : "·",
    fmtThresh = (v) => Number.isFinite(v) ? v.toFixed(2) : "·",
  } = ctx;

  // Build entry/exit criteria text for one closed trade.
  const closeOne = (entryIdx, i, entryPos) => {
    let pnl = 0;
    for (let k = entryIdx + 1; k <= i; k++) pnl += entryPos * (weeklyRets[k] || 0);
    pnl -= 2 * (TXN_COST_BPS / 10000);   // round-trip cost
    const weeksHeld = i - entryIdx;
    const entrySig = signal ? signal[entryIdx] : null;
    const exitSig  = signal ? signal[i] : null;

    // Infer exit reason from the strategy ctx + final state
    let exit_reason = "signal";
    if (stopPct != null && pnl < -stopPct) exit_reason = "stop";
    else if (maxHoldWeeks != null && weeksHeld >= maxHoldWeeks) exit_reason = "expired";

    const entry_criteria = (signal && entrySig != null && entry != null)
      ? `${signalLabel} ${fmtSignal(entrySig)} crossed ±${fmtThresh(entry)}`
      : null;

    const exit_criteria =
      exit_reason === "stop"    ? `stop loss · pnl ${(pnl * 100).toFixed(1)}%` :
      exit_reason === "expired" ? `max hold · ${weeksHeld}w` :
      (signal && exitSig != null && exit != null)
        ? `${signalLabel} ${fmtSignal(exitSig)} inside ±${fmtThresh(exit)}`
        : "signal";

    return {
      entry_date: dates[entryIdx], exit_date: dates[i],
      direction: entryPos > 0 ? "Long" : "Short",
      weeks: weeksHeld, pnl,
      entry_signal: entrySig, exit_signal: exitSig,
      entry_criteria, exit_criteria, exit_reason,
      signal_label: signalLabel,
    };
  };

  const trades = [];
  let entryIdx = -1, entryPos = 0;
  for (let i = 0; i < positions.length; i++) {
    const p = positions[i];
    if (entryIdx < 0 && p !== 0) { entryIdx = i; entryPos = p; }
    else if (entryIdx >= 0 && (p === 0 || p !== entryPos)) {
      trades.push(closeOne(entryIdx, i, entryPos));
      if (p !== 0 && p !== entryPos) { entryIdx = i; entryPos = p; }
      else { entryIdx = -1; entryPos = 0; }
    }
  }
  // Open trade still running at the end of series · capped at last bar
  if (entryIdx >= 0) {
    const last = positions.length - 1;
    let pnl = 0;
    for (let k = entryIdx + 1; k <= last; k++) pnl += entryPos * (weeklyRets[k] || 0);
    pnl -= TXN_COST_BPS / 10000;
    const entrySig = signal ? signal[entryIdx] : null;
    const entry_criteria = (signal && entrySig != null && entry != null)
      ? `${signalLabel} ${fmtSignal(entrySig)} crossed ±${fmtThresh(entry)}`
      : null;
    trades.push({
      entry_date: dates[entryIdx], exit_date: "(open)",
      direction: entryPos > 0 ? "Long" : "Short",
      weeks: last - entryIdx, pnl,
      entry_signal: entrySig, exit_signal: null,
      entry_criteria, exit_criteria: "still open", exit_reason: "open",
      signal_label: signalLabel,
    });
  }
  return trades;
}

/* Performance metrics. Sharpe is annualized assuming 52 weekly bars. */
function metricsFromPnl(pnl, trades) {
  const n = pnl.length;
  let total = 0;
  for (const v of pnl) total += v;
  const mean = total / Math.max(1, n);
  let varSum = 0;
  for (const v of pnl) varSum += (v - mean) ** 2;
  const sd = Math.sqrt(varSum / Math.max(1, n - 1));
  const sharpe = sd > 0 ? (mean * 52) / (sd * Math.sqrt(52)) : 0;
  // CAGR estimated from cumulative (additive log-ish approximation OK for v1)
  const years = n / 52;
  const cagr = years > 0 ? Math.exp(total) ** (1 / years) - 1 : 0;
  // max drawdown on equity curve
  const eq = equityCurveFromPnl(pnl);
  let peak = -Infinity, maxDD = 0;
  for (const v of eq) {
    peak = Math.max(peak, v);
    maxDD = Math.min(maxDD, v - peak);
  }
  const wins = trades.filter(t => t.pnl > 0).length;
  const winRate = trades.length > 0 ? wins / trades.length : 0;
  const avgHold = trades.length > 0
    ? trades.reduce((s, t) => s + t.weeks, 0) / trades.length
    : 0;
  return {
    sharpe, cagr, maxDD,
    nTrades: trades.length, winRate, avgHoldWeeks: avgHold,
    finalPnl: total,
  };
}

/* Block bootstrap · resample contiguous blocks of weekly PnL and recompute
   stats. Used to build a CI / null distribution. */
function blockBootstrap(pnl, blockSize, nRuns, rng = Math.random) {
  const N = pnl.length;
  const out = [];
  for (let r = 0; r < nRuns; r++) {
    const sample = new Array(N);
    let i = 0;
    while (i < N) {
      const start = Math.floor(rng() * Math.max(1, N - blockSize));
      for (let k = 0; k < blockSize && i < N; k++) sample[i++] = pnl[start + k];
    }
    let total = 0;
    for (const v of sample) total += v;
    out.push(total);
  }
  out.sort((a, b) => a - b);
  return out;
}

/* Pair spread · long A / short B. Position +1 means long-A short-B, -1
   means short-A long-B. Spread signal = z-score of cumulative return spread. */
function buildPairSeries(nodeA, nodeB) {
  const a = basketWeeklyReturns(nodeA);
  const b = basketWeeklyReturns(nodeB);
  const N = Math.min(a.rets.length, b.rets.length);
  const dates = a.dates.slice(0, N);
  const spread = new Array(N);
  for (let i = 0; i < N; i++) spread[i] = (a.rets[i] || 0) - (b.rets[i] || 0);
  return { dates, rets: spread };
}

/* Single backtest run · returns full result bundle. */
function runBacktest({ subject, strategy, params }) {
  // Resolve subject into a return series + dates. Pair legs may now be either
  // a basket id or a single ticker; single subject also fully supported.
  let rets = [], dates = [], series = "";
  if (subject.type === "basket") {
    const node = (window.NODES || []).find(n => n.id === subject.basket);
    if (!node) return { error: `basket not found (id="${subject.basket}")` };
    const w = basketWeeklyReturns(node);
    rets = w.rets; dates = w.dates; series = node.name;
    console.log(`[backtest] basket "${node.id}" · ${rets.length} weekly bars`);
  } else if (subject.type === "single") {
    if (!subject.ticker) return { error: "no ticker selected" };
    const w = singleTickerWeeklyReturns(subject.ticker);
    if (w.dates.length < 2) return { error: `ticker "${subject.ticker}" has no weekly returns embedded` };
    const rec = (window.CONSTITUENT_BY_TICKER || {})[subject.ticker];
    rets = w.rets; dates = w.dates;
    series = rec ? `${subject.ticker} · ${rec.name}` : subject.ticker;
    console.log(`[backtest] single "${subject.ticker}" · ${rets.length} weekly bars`);
  } else if (subject.type === "pair") {
    const ra = resolveLeg(subject.long);
    const rb = resolveLeg(subject.short);
    if (ra.error) return { error: `long leg: ${ra.error}` };
    if (rb.error) return { error: `short leg: ${rb.error}` };
    const aligned = alignLegs(ra, rb);
    if (aligned.dates.length < 2) return { error: "pair legs have no overlapping dates" };
    rets = aligned.retsA.map((v, i) => v - aligned.retsB[i]);
    rets[0] = 0;
    dates = aligned.dates;
    series = `${ra.label} − ${rb.label}`;
    console.log(`[backtest] pair "${ra.label}" / "${rb.label}" · ${rets.length} aligned weekly bars`);
  } else {
    return { error: "unknown subject" };
  }

  if (rets.length < 60) return { error: `not enough history (got ${rets.length} weekly bars, need ≥60).` };

  // === New continuous-position engine ===
  // Build prices for stop-loss math (price-from-rets · simple compounding)
  const prices = pricesFromReturns(rets);

  // Engine opts · trend filter and persistence check are off by default in
  // this stage; UI controls land in a follow-up. Long-only/short-only and
  // leverage are baked into engine params (replaces the old post-process).
  const opts = {
    trendFilterEnabled:  params.trendFilterEnabled === true,
    trendFilterThreshold: params.trendFilterThreshold ?? 0.30,
    persistenceCheck:    params.persistenceCheck === true,
    persistenceLookback: params.persistenceLookback ?? 4,
  };

  let positions, actions, tradeCtx;

  if (strategy.type === "mean-revert" || strategy.type === "factor-residual" || strategy.type === "pair-spread") {
    const level = cumLogLevel(rets);
    const signal = rollingZ(level, 26);
    const maxHoldWeeks = Math.ceil(params.maxHoldDays / 7);
    const enginePar = {
      entryThreshold: params.entry,
      exitThreshold:  params.exit,
      saturation:     params.saturation ?? 3.0,
      leverage:       params.leverage ?? 1.0,
      stopLoss:       strategy.type === "pair-spread" ? 0.20 : params.stopPct,
      trailingStop:   0.05,
      maxHolding:     maxHoldWeeks,
      posStyle:       params.posStyle ?? "binary",
      fadeOrRide:     strategy.type === "factor-residual" ? (params.residualDir ?? "fade") : null,
    };
    const engineType = strategy.type === "mean-revert" ? "mean_revert"
                     : strategy.type === "pair-spread" ? "pair"
                     : "factor_residual";
    const result = meanRevertPositions(signal, prices, enginePar, engineType, opts);
    positions = result.positions;
    actions   = result.actions;

    const label = strategy.type === "factor-residual" ? "resid z"
                : strategy.type === "pair-spread"     ? "spread z"
                : "z";
    tradeCtx = {
      signal, signalLabel: label,
      entry: params.entry, exit: params.exit,
      stopPct: enginePar.stopLoss, maxHoldWeeks,
    };
  } else if (strategy.type === "trend-follow") {
    const lookbackWeeks = Math.ceil(params.lookbackDays / 7);
    const maxHoldWeeks  = Math.ceil(params.maxHoldDays / 7);
    // n-period return is the trend signal (unit: simple return %)
    const logLevel = cumLogLevel(rets);
    const N = rets.length;
    const nReturn = new Array(N).fill(NaN);
    for (let i = lookbackWeeks; i < N; i++) {
      nReturn[i] = Math.exp(logLevel[i] - logLevel[i - lookbackWeeks]) - 1;
    }
    const enginePar = {
      entryThreshold: params.trendEntry,
      exitThreshold:  0,
      saturation:     params.trendSaturation ?? 0.25,   // % return at full position
      leverage:       params.leverage ?? 1.0,
      stopLoss:       0.99,                              // effectively off · trailing handles it
      trailingStop:   params.trailingStopPct,
      maxHolding:     maxHoldWeeks,
      posStyle:       params.posStyle ?? "binary",
    };
    const result = trendFollowPositions(nReturn, prices, enginePar, opts);
    positions = result.positions;
    actions   = result.actions;

    tradeCtx = {
      signal: nReturn,
      signalLabel: `${params.lookbackDays}d ret`,
      entry: params.trendEntry, exit: null,
      stopPct: params.trailingStopPct, maxHoldWeeks,
      fmtSignal: (v) => Number.isFinite(v) ? (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%" : "·",
      fmtThresh: (v) => Number.isFinite(v) ? (v * 100).toFixed(0) + "%" : "·",
    };
  } else {
    return { error: "unknown strategy" };
  }

  const pnl = applyPositionsToReturns(positions, rets);
  // Trade reconstruction uses the legacy extractTrades over (continuous)
  // positions for now - actionsToTrades / scale-event UI lands in the next
  // round. The action log is already captured for that future use.
  const trades = extractTrades(positions, rets, dates, tradeCtx);
  const equity = equityCurveFromPnl(pnl);
  const metrics = metricsFromPnl(pnl, trades);

  // Buy-and-hold reference, same dates · always-long position
  const bhPnl = rets.slice();
  bhPnl[0] = 0;
  const bhEquity = equityCurveFromPnl(bhPnl);
  const bhMetrics = metricsFromPnl(bhPnl, []);

  // Bootstrap distribution if requested
  let bootstrap = null;
  if (params.testMode === "bootstrap" || params.testMode === "walk-forward") {
    const dist = blockBootstrap(pnl, 4, 1000);
    bootstrap = {
      p05: dist[Math.floor(dist.length * 0.05)],
      p50: dist[Math.floor(dist.length * 0.50)],
      p95: dist[Math.floor(dist.length * 0.95)],
      distribution: dist,
    };
  }

  return {
    series, dates, rets, positions, pnl, equity, bhEquity,
    trades, metrics, bhMetrics, bootstrap,
  };
}

/* ---------------- Pair-leg picker (basket OR single ticker) ---------------- */
function PairLegPicker({ label, leg, onChange, nodesList, optionLabel }) {
  return (
    <div className="bt-leg">
      <div className="bt-leg-head">
        <span className="bt-leg-label">{label}</span>
        <div className="bt-leg-type-toggle">
          {[["basket", "Basket"], ["single", "Ticker"]].map(([v, l]) => (
            <button key={v}
              className={"bt-leg-type-btn" + (leg.type === v ? " active" : "")}
              onClick={() => onChange({ type: v, value: "" })}>
              {l}
            </button>
          ))}
        </div>
      </div>
      {leg.type === "basket" ? (
        <select className="bt-select" value={leg.value || ""}
          onChange={e => onChange({ type: "basket", value: e.target.value })}>
          <option value=""> - pick a basket - </option>
          {nodesList.map(n => <option key={n.id} value={n.id}>{optionLabel(n)}</option>)}
        </select>
      ) : (
        <TickerSelect value={leg.value || ""} onChange={(tk) => onChange({ type: "single", value: tk })}/>
      )}
    </div>
  );
}

/* ---------------- Compact ticker autocomplete (used by single + pair legs) -- */
function TickerSelect({ value, onChange }) {
  const [q, setQ] = useState(value || "");
  const [focused, setFocused] = useState(false);
  React.useEffect(() => { setQ(value || ""); }, [value]);
  const all = window.ALL_TICKERS || [];
  const matches = useMemo(() => {
    const s = (q || "").trim().toLowerCase();
    if (!s) return all.slice(0, 12);
    return all.filter(t =>
      t.ticker.toLowerCase().includes(s) ||
      (t.name || "").toLowerCase().includes(s)
    ).slice(0, 12);
  }, [q, all]);
  return (
    <div className="bt-ticker-select">
      <input
        className="bt-input"
        type="text"
        placeholder="Ticker (e.g. XOM)"
        value={q}
        onChange={e => setQ(e.target.value.toUpperCase())}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 200)}
      />
      {focused && matches.length > 0 && (
        <div className="bt-ticker-dropdown">
          {matches.map(t => (
            <div key={t.ticker} className="bt-ticker-item"
              onMouseDown={(e) => {
                e.preventDefault();
                onChange(t.ticker);
                setQ(t.ticker);
                setFocused(false);
              }}>
              <span className="ts-ticker">{t.ticker}</span>
              <span className="ts-name">{t.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- Backtest · UI ---------------- */
function Backtest({ seed }) {
  const NODES_LIST = (window.NODES || []).slice().sort((a, b) => a.name.localeCompare(b.name));
  const ALL_TICKERS = window.ALL_TICKERS || [];
  // Per-basket weekly-bar count so the picker can flag short-history baskets
  // up-front instead of failing at run-time.
  const barCount = (n) => (n && n.cumulative_returns ? n.cumulative_returns.length : 0);
  const optionLabel = (n) => {
    const w = barCount(n);
    const tag = w < 60 ? " · ⚠ thin" : (w < 150 ? " · short" : "");
    return `${n.name}  · ${w}w${tag}`;
  };

  // Subject state · seed pre-fills if provided (from a clicked signal card)
  const [subjectType, setSubjectType] = useState(seed?.subjectType || "basket");
  const [basketId, setBasketId]       = useState(seed?.basketId || NODES_LIST[0]?.id || "");
  // Pair legs are now {type:"basket"|"single", value:id_or_ticker} so a leg
  // can be either a basket OR a single ticker.
  const [pairLong, setPairLong]   = useState(seed?.pairLong  || { type: "basket", value: NODES_LIST[0]?.id || "" });
  const [pairShort, setPairShort] = useState(seed?.pairShort || { type: "basket", value: NODES_LIST[1]?.id || "" });
  const [singleTk, setSingleTk]   = useState(seed?.singleTk  || "");

  const [strategyType, setStrategyType] = useState(seed?.strategyType || "mean-revert");
  const [testMode, setTestMode]         = useState("walk-forward");

  // Strategy params (sliders) - one combined object the UI mutates
  const [params, setParams] = useState({
    entry: 1.5, exit: 0.5, stopPct: 0.08, maxHoldDays: 30,        // mean-revert
    lookbackDays: 60, trendEntry: 0.10, trailingStopPct: 0.05, trendMaxHoldDays: 60,
    pairEntry: 2.0, pairExit: 0.5, pairMaxHoldDays: 45, pairSpreadDef: "z-score",
    residualEntry: 1.5, residualExit: 0.5, residualMaxHoldDays: 20, residualDir: "fade",
    // Continuous-position controls · `saturation` is the signal level at
    // which |position| reaches `leverage` (full size). z-strategies use
    // z-units; trend uses simple-return %.
    saturation: 3.0,
    trendSaturation: 0.25,
    // Trend filter · suppresses counter-trend trades for fade strategies
    trendFilterEnabled: true,
    trendFilterThreshold: 0.30,
    // Persistence check · skips factor-residual fades when the residual has
    // been stretched on the same side most of the recent window (likely
    // structural break, not a mean-reverting opportunity).
    persistenceCheck: true,
    persistenceLookback: 4,
    // Position style + leverage · apply to every strategy
    posStyle: "binary", leverage: 1.0,
  });
  const setP = (k) => (v) => setParams(prev => ({ ...prev, [k]: v }));

  const [result, setResult] = useState(null);
  const [highlightTrade, setHighlightTrade] = useState(null);
  // Trade hovered on the equity chart's vertical entry lines · separate from
  // click-pinned highlight so hovering doesn't clobber a clicked selection.
  const [hoverTrade, setHoverTrade] = useState(null);
  // Combined "focused" trade · hover takes precedence while active.
  const focusedTrade = hoverTrade || highlightTrade;

  // Scroll the matching row in the trade log into view when focus changes.
  React.useEffect(() => {
    if (!focusedTrade) return;
    const row = document.querySelector(`[data-trade-id="${focusedTrade.entry_date}-${focusedTrade.direction}"]`);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusedTrade]);

  const run = () => {
    const subject = subjectType === "basket"
      ? { type: "basket", basket: basketId }
      : subjectType === "pair"
        ? { type: "pair", long: pairLong, short: pairShort }
        : { type: "single", ticker: singleTk };
    const strategy = { type: strategyType };
    const cfg = { testMode, posStyle: params.posStyle, leverage: params.leverage };
    if (strategyType === "mean-revert") {
      Object.assign(cfg, { entry: params.entry, exit: params.exit, stopPct: params.stopPct, maxHoldDays: params.maxHoldDays });
    } else if (strategyType === "trend-follow") {
      Object.assign(cfg, { lookbackDays: params.lookbackDays, entry: params.trendEntry, trailingStopPct: params.trailingStopPct, maxHoldDays: params.trendMaxHoldDays });
    } else if (strategyType === "pair-spread") {
      Object.assign(cfg, { entry: params.pairEntry, exit: params.pairExit, maxHoldDays: params.pairMaxHoldDays });
    } else if (strategyType === "factor-residual") {
      Object.assign(cfg, { entry: params.residualEntry, exit: params.residualExit, stopPct: 0.10, maxHoldDays: params.residualMaxHoldDays });
    }
    const r = runBacktest({ subject, strategy, params: cfg });
    console.log("[backtest]", subject, strategy, cfg, "→", r && (r.error || `equity ${r.equity?.length} · trades ${r.trades?.length} · sharpe ${r.metrics?.sharpe?.toFixed(2)}`));
    setResult(r);
    setHighlightTrade(null);
  };

  // Auto-run once on mount with default (or seeded) params
  React.useEffect(() => { run(); /* eslint-disable-next-line */ }, []);

  return (
    <div className="bt">
      <aside className="bt-config">
        <div className="kicker">Strategy</div>
        <h2><em>Backtest Lab</em></h2>

        <div className="bt-step">
          <div className="bt-step-head">1 · Subject</div>
          <div className="bt-radios">
            {[["basket","Basket"],["pair","Pair"],["single","Single name"]].map(([v,l]) => (
              <label key={v} className={"bt-radio" + (subjectType === v ? " active" : "")}>
                <input type="radio" name="subj" value={v} checked={subjectType===v} onChange={e=>setSubjectType(e.target.value)}/>
                {l}
              </label>
            ))}
          </div>
          {subjectType === "basket" && (
            <select className="bt-select" value={basketId} onChange={e=>setBasketId(e.target.value)}>
              {NODES_LIST.map(n => <option key={n.id} value={n.id}>{optionLabel(n)}</option>)}
            </select>
          )}
          {subjectType === "pair" && (
            <>
              <PairLegPicker label="L" leg={pairLong}  onChange={setPairLong}  nodesList={NODES_LIST} optionLabel={optionLabel}/>
              <PairLegPicker label="S" leg={pairShort} onChange={setPairShort} nodesList={NODES_LIST} optionLabel={optionLabel}/>
            </>
          )}
          {subjectType === "single" && (
            <TickerSelect value={singleTk} onChange={setSingleTk}/>
          )}
        </div>

        <div className="bt-step">
          <div className="bt-step-head">2 · Strategy</div>
          <div className="bt-radios">
            {[["mean-revert","Mean-revert"],["trend-follow","Trend follow"],["pair-spread","Pair spread"],["factor-residual","Factor residual"]].map(([v,l]) => (
              <label key={v} className={"bt-radio" + (strategyType === v ? " active" : "")}>
                <input type="radio" name="strat" value={v} checked={strategyType===v} onChange={e=>setStrategyType(e.target.value)}/>
                {l}
              </label>
            ))}
          </div>
          {strategyType === "factor-residual" && (
            <div className="bt-warn">v1 fallback: factor-residual runs on basket cum-log z-score. True per-name residual series needs the pipeline extension.</div>
          )}
        </div>

        <div className="bt-step">
          <div className="bt-step-head">3 · Parameters</div>
          {strategyType === "mean-revert" && (
            <>
              <BtSlider label="Entry threshold (|z|)" value={params.entry} min={0.5} max={3.0} step={0.1} onChange={setP("entry")}/>
              <BtSlider label="Exit threshold (|z|)" value={params.exit} min={0.0} max={1.5} step={0.1} onChange={setP("exit")}/>
              <BtSlider label="Saturation (|z|) · full size at" value={params.saturation} min={1.5} max={5.0} step={0.1} onChange={setP("saturation")}/>
              <BtSlider label="Stop loss" value={params.stopPct} min={0.02} max={0.20} step={0.01} onChange={setP("stopPct")} fmt={v=>(v*100).toFixed(0)+"%"}/>
              <BtSlider label="Max holding (days)" value={params.maxHoldDays} min={5} max={60} step={1} onChange={setP("maxHoldDays")} fmt={v=>v+"d"}/>
              <BtCheckbox label="Suppress against long-term trend" checked={params.trendFilterEnabled} onChange={setP("trendFilterEnabled")}/>
              {params.trendFilterEnabled && (
                <BtSlider label="Trend filter threshold" value={params.trendFilterThreshold} min={0.10} max={0.50} step={0.05} onChange={setP("trendFilterThreshold")} fmt={v=>(v*100).toFixed(0)+"%"}/>
              )}
            </>
          )}
          {strategyType === "trend-follow" && (
            <>
              <BtSlider label="Lookback (days)" value={params.lookbackDays} min={20} max={120} step={1} onChange={setP("lookbackDays")} fmt={v=>v+"d"}/>
              <BtSlider label="Entry threshold (return)" value={params.trendEntry} min={0.02} max={0.20} step={0.01} onChange={setP("trendEntry")} fmt={v=>(v*100).toFixed(0)+"%"}/>
              <BtSlider label="Saturation (return) · full size at" value={params.trendSaturation} min={0.10} max={0.50} step={0.01} onChange={setP("trendSaturation")} fmt={v=>(v*100).toFixed(0)+"%"}/>
              <BtSlider label="Trailing stop" value={params.trailingStopPct} min={0.02} max={0.15} step={0.01} onChange={setP("trailingStopPct")} fmt={v=>(v*100).toFixed(0)+"%"}/>
              <BtSlider label="Max holding (days)" value={params.trendMaxHoldDays} min={30} max={180} step={5} onChange={setP("trendMaxHoldDays")} fmt={v=>v+"d"}/>
            </>
          )}
          {strategyType === "pair-spread" && (
            <>
              <div className="bt-radios">
                {[["log-ratio","log ratio"],["z-score","z-score"]].map(([v,l]) => (
                  <label key={v} className={"bt-radio" + (params.pairSpreadDef === v ? " active" : "")}>
                    <input type="radio" name="psd" value={v} checked={params.pairSpreadDef===v} onChange={e=>setP("pairSpreadDef")(e.target.value)}/>
                    {l}
                  </label>
                ))}
              </div>
              <BtSlider label="Entry threshold (|z|)" value={params.pairEntry} min={1.0} max={3.0} step={0.1} onChange={setP("pairEntry")}/>
              <BtSlider label="Exit threshold (|z|)" value={params.pairExit} min={0.0} max={1.5} step={0.1} onChange={setP("pairExit")}/>
              <BtSlider label="Saturation (|z|) · full size at" value={params.saturation} min={1.5} max={5.0} step={0.1} onChange={setP("saturation")}/>
              <BtSlider label="Max holding (days)" value={params.pairMaxHoldDays} min={10} max={90} step={5} onChange={setP("pairMaxHoldDays")} fmt={v=>v+"d"}/>
              <BtCheckbox label="Suppress against long-term trend" checked={params.trendFilterEnabled} onChange={setP("trendFilterEnabled")}/>
              {params.trendFilterEnabled && (
                <BtSlider label="Trend filter threshold" value={params.trendFilterThreshold} min={0.10} max={0.50} step={0.05} onChange={setP("trendFilterThreshold")} fmt={v=>(v*100).toFixed(0)+"%"}/>
              )}
            </>
          )}
          {strategyType === "factor-residual" && (
            <>
              <BtSlider label="Entry threshold (|z|)" value={params.residualEntry} min={1.0} max={3.0} step={0.1} onChange={setP("residualEntry")}/>
              <BtSlider label="Exit threshold (|z|)" value={params.residualExit} min={0.0} max={1.5} step={0.1} onChange={setP("residualExit")}/>
              <BtSlider label="Saturation (|z|) · full size at" value={params.saturation} min={1.5} max={5.0} step={0.1} onChange={setP("saturation")}/>
              <div className="bt-radios">
                {[["fade","Fade stretches"],["ride","Ride momentum"]].map(([v,l]) => (
                  <label key={v} className={"bt-radio" + (params.residualDir === v ? " active" : "")}>
                    <input type="radio" name="rd" value={v} checked={params.residualDir===v} onChange={e=>setP("residualDir")(e.target.value)}/>
                    {l}
                  </label>
                ))}
              </div>
              <BtSlider label="Max holding (days)" value={params.residualMaxHoldDays} min={5} max={60} step={1} onChange={setP("residualMaxHoldDays")} fmt={v=>v+"d"}/>
              {params.residualDir === "fade" && (
                <>
                  <BtCheckbox label="Suppress against long-term trend" checked={params.trendFilterEnabled} onChange={setP("trendFilterEnabled")}/>
                  {params.trendFilterEnabled && (
                    <BtSlider label="Trend filter threshold" value={params.trendFilterThreshold} min={0.10} max={0.50} step={0.05} onChange={setP("trendFilterThreshold")} fmt={v=>(v*100).toFixed(0)+"%"}/>
                  )}
                  <BtCheckbox label="Skip persistent stretches (likely structural break)" checked={params.persistenceCheck} onChange={setP("persistenceCheck")}/>
                  {params.persistenceCheck && (
                    <BtSlider label="Persistence lookback (bars)" value={params.persistenceLookback} min={3} max={10} step={1} onChange={setP("persistenceLookback")} fmt={v=>v+"b"}/>
                  )}
                </>
              )}
            </>
          )}
        </div>

        <div className="bt-step">
          <div className="bt-step-head">4 · Test mode</div>
          <div className="bt-radios bt-radios-col">
            {[
              ["in-sample",   "In-sample (full history)"],
              ["walk-forward","Walk-forward (3y / 1y rolling)"],
              ["bootstrap",   "Block bootstrap (1000 × 4w)"],
            ].map(([v,l]) => (
              <label key={v} className={"bt-radio" + (testMode === v ? " active" : "")}>
                <input type="radio" name="mode" value={v} checked={testMode===v} onChange={e=>setTestMode(e.target.value)}/>
                {l}
              </label>
            ))}
          </div>
          {testMode === "in-sample" && (
            <div className="bt-warn">In-sample results overstate strategy quality. Use walk-forward or bootstrap for honest evaluation.</div>
          )}
          {testMode === "walk-forward" && (
            <div className="bt-note">v1 walk-forward = full-history fit + bootstrap CI. True 3y/1y rolling fit will land in v1.1.</div>
          )}
        </div>

        <div className="bt-step">
          <div className="bt-step-head">5 · Positioning</div>
          <div className="bt-radios">
            {[
              ["binary",     "± long/short"],
              ["long-only",  "Long-only"],
              ["short-only", "Short-only"],
            ].map(([v, l]) => (
              <label key={v} className={"bt-radio" + (params.posStyle === v ? " active" : "")}>
                <input type="radio" name="posStyle" value={v} checked={params.posStyle === v} onChange={e=>setP("posStyle")(e.target.value)}/>
                {l}
              </label>
            ))}
          </div>
          <BtSlider label="Leverage (×)" value={params.leverage} min={0.5} max={3.0} step={0.1} onChange={setP("leverage")} fmt={v=>v.toFixed(1)+"×"}/>
          <div className="bt-note">Base engine produces {"{-1, 0, +1}"} positions. Long-only zeros shorts; short-only zeros longs; leverage scales the final notional.</div>
        </div>

        <button className="bt-run" onClick={run}>Run backtest</button>
        <div className="bt-caveat">v1 simplifications · 5 bps round-trip cost · no leverage · no shorting cost · no borrow.</div>
      </aside>

      <main className="bt-results">
        {!result ? (
          <div className="dash-empty">Configure a strategy and hit Run.</div>
        ) : result.error ? (
          <div className="dash-empty" style={{color:"var(--accent)"}}>· {result.error}</div>
        ) : (
          <>
            <div className="bt-results-head">
              <h3><em>{result.series}</em></h3>
              <span className="dash-sec-kicker">{result.equity.length} weekly bars · {result.trades.length} trades</span>
            </div>
            <BtEquityChart
              result={result}
              focused={focusedTrade}
              setHighlight={(t) => setHighlightTrade(t === highlightTrade ? null : t)}
              setHover={setHoverTrade}
            />
            <BtMetricsRow metrics={result.metrics} bhMetrics={result.bhMetrics} bootstrap={result.bootstrap}/>
            {result.bootstrap && <BtDistribution result={result}/>}
            <div className="bt-caveat" style={{marginTop:14}}>
              v1 simplifications · 5 bps round-trip cost · no leverage · no shorting cost · no borrow.
            </div>
          </>
        )}
      </main>

      <aside className="bt-trades">
        <div className="kicker">Trade log · chronological</div>
        <h2><em>{result?.trades?.length || 0} trades</em></h2>
        {result && !result.error && (
          <div className="bt-trade-list">
            <div className="bt-trade-row bt-trade-head">
              <div className="bt-trade-row-main">
                <span>Entry</span><span>Dir</span><span>Weeks</span><span>PnL</span>
              </div>
            </div>
            {result.trades.slice().sort((a,b) => (a.entry_date || "").localeCompare(b.entry_date || "")).map((t, i) => {
              const id = `${t.entry_date}-${t.direction}`;
              const isFocused = focusedTrade && focusedTrade.entry_date === t.entry_date && focusedTrade.direction === t.direction;
              return (
                <div key={i}
                  data-trade-id={id}
                  className={"bt-trade-row clickable" + (highlightTrade === t ? " active" : "") + (isFocused ? " focused" : "")}
                  onMouseEnter={() => setHoverTrade(t)}
                  onMouseLeave={() => setHoverTrade(null)}
                  onClick={() => setHighlightTrade(highlightTrade === t ? null : t)}>
                  <div className="bt-trade-row-main">
                    <span className="ts-name">{(t.entry_date || "").slice(0,10)}</span>
                    <span className={t.direction === "Long" ? "pct-pos" : "pct-neg"}>{t.direction[0]}</span>
                    <span>{t.weeks}w</span>
                    <span className={t.pnl >= 0 ? "pct-pos" : "pct-neg"}>{(t.pnl*100).toFixed(1)}%</span>
                  </div>
                  {(t.entry_criteria || t.exit_criteria) && (
                    <div className="bt-trade-row-criteria">
                      {t.entry_criteria && <span title="entry">▶ {t.entry_criteria}</span>}
                      {t.exit_criteria && <span title="exit" style={{color:"var(--text-dim)"}}>· ◀ {t.exit_criteria}</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </aside>
    </div>
  );
}

function BtSlider({ label, value, min, max, step, onChange, fmt }) {
  const display = fmt ? fmt(value) : (typeof value === "number" ? value.toFixed(2) : value);
  return (
    <div className="bt-slider">
      <div className="bt-slider-head">
        <span>{label}</span>
        <span className="bt-slider-val">{display}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}/>
    </div>
  );
}

function BtCheckbox({ label, checked, onChange }) {
  return (
    <label className="bt-checkbox">
      <input type="checkbox" checked={!!checked} onChange={e => onChange(e.target.checked)}/>
      <span className="bt-checkbox-box" aria-hidden="true">{checked ? "✓" : ""}</span>
      <span className="bt-checkbox-label">{label}</span>
    </label>
  );
}

function BtMetricsRow({ metrics, bhMetrics, bootstrap }) {
  // Each cell carries the strategy number on top and a small "B&H" comparison
  // line beneath. Sharpe / CAGR / Max DD / Final PnL are the four where direct
  // comparison to buy-and-hold makes sense.
  const fmt = (v, p = 2) => (Number.isFinite(v) ? v.toFixed(p) : "·");
  const pct = (v, p = 1) => (Number.isFinite(v) ? (v * 100).toFixed(p) + "%" : "·");
  const cell = (label, val, opts = {}) => (
    <div className="bt-metric">
      <div className="bt-metric-lab">{label}</div>
      <div className="bt-metric-val">{val}</div>
      {opts.bh != null && <div className="bt-metric-bh">B&amp;H · {opts.bh}</div>}
      {opts.ci  != null && <div className="bt-metric-ci">{opts.ci}</div>}
    </div>
  );
  return (
    <div className="bt-metrics">
      {cell("Sharpe",   fmt(metrics.sharpe), { bh: bhMetrics ? fmt(bhMetrics.sharpe) : null })}
      {cell("CAGR",     pct(metrics.cagr),   { bh: bhMetrics ? pct(bhMetrics.cagr) : null })}
      {cell("Max DD",   pct(metrics.maxDD),  { bh: bhMetrics ? pct(bhMetrics.maxDD) : null })}
      {cell("Win rate", pct(metrics.winRate, 0))}
      {cell("Trades",   metrics.nTrades)}
      {cell("Avg hold", fmt(metrics.avgHoldWeeks, 1) + "w")}
      {cell("Final PnL", pct(metrics.finalPnl), {
        bh: bhMetrics ? pct(bhMetrics.finalPnl) : null,
        ci: bootstrap ? `5–95 · ${pct(bootstrap.p05, 0)} → ${pct(bootstrap.p95, 0)}` : null,
      })}
    </div>
  );
}

function BtEquityChart({ result, focused, setHighlight, setHover: setHoverTrade }) {
  const ref = React.useRef(null);
  // Hover state · {idx, px, py} where idx is the bar/date index
  const [hover, setHover] = useState(null);
  const SKY = "#7ec0dd"; // sky blue · replaces the prior gold strategy line

  // Pre-compute "trade-at-index" lookup so the hover tooltip can show which
  // trade (if any) is open at the hovered date, plus an "entry-idx" map for
  // drawing one vertical line per trade.
  const { tradesByIdx, tradesWithIdx } = React.useMemo(() => {
    if (!result || !result.dates) return { tradesByIdx: [], tradesWithIdx: [] };
    const dates = result.dates;
    const tbi = new Array(dates.length).fill(null);
    const twi = [];
    for (const t of (result.trades || [])) {
      const a = dates.indexOf(t.entry_date);
      const b = t.exit_date === "(open)" ? dates.length - 1 : dates.indexOf(t.exit_date);
      if (a < 0) continue;
      const stop = b >= 0 ? b : dates.length - 1;
      for (let i = a; i <= stop; i++) tbi[i] = t;
      twi.push({ t, entryIdx: a, exitIdx: stop });
    }
    return { tradesByIdx: tbi, tradesWithIdx: twi };
  }, [result]);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const W = el.clientWidth || 700, H = 240, pad = { l: 40, r: 12, t: 14, b: 22 };
    const innerW = W - pad.l - pad.r, innerH = H - pad.t - pad.b;
    const eq = result.equity, bh = result.bhEquity;
    const x = i => pad.l + (i / Math.max(1, eq.length - 1)) * innerW;
    const minY = Math.min(0, ...eq, ...bh), maxY = Math.max(0, ...eq, ...bh);
    const y = v => pad.t + innerH * (1 - (v - minY) / Math.max(1e-6, maxY - minY));
    const g = d3.select(el);
    g.selectAll("*").remove();
    g.attr("viewBox", `0 0 ${W} ${H}`);

    // Axes
    g.append("line").attr("x1", pad.l).attr("x2", W - pad.r).attr("y1", y(0)).attr("y2", y(0))
      .attr("stroke", "var(--text-dim)").attr("stroke-dasharray", "2 4").attr("stroke-width", 0.5);

    // Focused-trade band · highlights the [entry → exit] window of either the
    // hovered or click-pinned trade.
    if (focused) {
      const dates = result.dates;
      const startIdx = dates.indexOf(focused.entry_date);
      const endIdx = focused.exit_date === "(open)" ? dates.length - 1 : dates.indexOf(focused.exit_date);
      if (startIdx >= 0 && endIdx >= 0) {
        g.append("rect")
          .attr("x", x(startIdx)).attr("y", pad.t)
          .attr("width", Math.max(2, x(endIdx) - x(startIdx))).attr("height", innerH)
          .attr("fill", focused.direction === "Long" ? "rgba(93,176,117,0.15)" : "rgba(196,84,74,0.15)");
      }
    }

    // Buy & hold reference (dim grey)
    g.append("path")
      .attr("d", "M " + bh.map((v, i) => `${x(i)} ${y(v)}`).join(" L "))
      .attr("stroke", "var(--text-dim)").attr("stroke-width", 1).attr("fill", "none");

    // Strategy equity (sky blue)
    g.append("path")
      .attr("d", "M " + eq.map((v, i) => `${x(i)} ${y(v)}`).join(" L "))
      .attr("stroke", SKY).attr("stroke-width", 1.8).attr("fill", "none");

    // === Vertical lines per action - colored by BUY (green) vs SELL (red) ===
    // Long trade   : entry = BUY  (green), exit = SELL (red)
    // Short trade  : entry = SELL (red),   exit = BUY  (green)
    // Two lines per trade so the user can read the direction of each action,
    // not just the resulting position.
    const GREEN = "rgba(93,176,117,0.85)";   // BUY
    const RED   = "rgba(196,84,74,0.85)";    // SELL
    const tradeLines = g.append("g").attr("class", "bt-trade-lines");
    const drawActionLine = (t, idx, stroke, isFocused) => {
      if (idx == null || idx < 0) return;
      const cx = x(idx);
      tradeLines.append("line")
        .attr("x1", cx).attr("x2", cx).attr("y1", pad.t).attr("y2", H - pad.b)
        .attr("stroke", stroke)
        .attr("stroke-width", isFocused ? 2.0 : 0.8)
        .attr("opacity", isFocused ? 1 : 0.55)
        .attr("pointer-events", "none");
      tradeLines.append("line")
        .attr("x1", cx).attr("x2", cx).attr("y1", pad.t).attr("y2", H - pad.b)
        .attr("stroke", "transparent")
        .attr("stroke-width", 8)
        .style("cursor", "pointer")
        .on("mouseenter", () => setHoverTrade && setHoverTrade(t))
        .on("mouseleave", () => setHoverTrade && setHoverTrade(null))
        .on("click", () => setHighlight && setHighlight(t));
    };
    tradesWithIdx.forEach(({ t, entryIdx, exitIdx }) => {
      const isFocused = focused && focused.entry_date === t.entry_date && focused.direction === t.direction;
      const longTrade = t.direction === "Long";
      // Entry line · BUY for long, SELL for short
      drawActionLine(t, entryIdx, longTrade ? GREEN : RED, isFocused);
      // Exit line · only for closed trades; SELL closes long, BUY covers short
      const isOpen = !t.exit_date || t.exit_date === "(open)";
      if (!isOpen && exitIdx > entryIdx) {
        drawActionLine(t, exitIdx, longTrade ? RED : GREEN, isFocused);
      }
    });

    // Hover crosshair · vertical line + tracking dot at the hovered date
    if (hover && hover.idx >= 0 && hover.idx < eq.length) {
      const cx = x(hover.idx), cy = y(eq[hover.idx]);
      g.append("line")
        .attr("x1", cx).attr("x2", cx).attr("y1", pad.t).attr("y2", H - pad.b)
        .attr("stroke", SKY).attr("stroke-width", 0.6).attr("stroke-dasharray", "2 3")
        .attr("opacity", 0.7).attr("pointer-events", "none");
      g.append("circle").attr("cx", cx).attr("cy", cy).attr("r", 4)
        .attr("fill", SKY).attr("stroke", "var(--bg-card)").attr("stroke-width", 1.5)
        .attr("pointer-events", "none");
    }

    // Y-axis labels
    g.append("text").attr("x", pad.l - 6).attr("y", y(0)).attr("text-anchor", "end").attr("dy", "0.32em")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9).attr("fill", "var(--text-dim)").text("0");
    g.append("text").attr("x", pad.l - 6).attr("y", y(maxY)).attr("text-anchor", "end").attr("dy", "0.32em")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9).attr("fill", "var(--text-dim)").text((maxY * 100).toFixed(0) + "%");
    g.append("text").attr("x", pad.l - 6).attr("y", y(minY)).attr("text-anchor", "end").attr("dy", "0.32em")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9).attr("fill", "var(--text-dim)").text((minY * 100).toFixed(0) + "%");

    // Series labels
    g.append("text").attr("x", W - pad.r).attr("y", pad.t + 10).attr("text-anchor", "end")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9.5).attr("fill", SKY)
      .attr("letter-spacing", "0.08em").text("STRATEGY");
    g.append("text").attr("x", W - pad.r).attr("y", pad.t + 24).attr("text-anchor", "end")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9.5).attr("fill", "var(--text-dim)")
      .attr("letter-spacing", "0.08em").text("BUY & HOLD");
    // Trade-line legend · color codes the action (BUY / SELL), not the
    // resulting net position direction.
    g.append("text").attr("x", W - pad.r).attr("y", pad.t + 38).attr("text-anchor", "end")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9).attr("fill", "rgba(93,176,117,0.85)")
      .attr("letter-spacing", "0.08em").text("│ BUY");
    g.append("text").attr("x", W - pad.r).attr("y", pad.t + 50).attr("text-anchor", "end")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9).attr("fill", "rgba(196,84,74,0.85)")
      .attr("letter-spacing", "0.08em").text("│ SELL");
  }, [result, focused, hover, tradesWithIdx, setHoverTrade, setHighlight]);

  // Mouse handler · convert pixel → bar index, drive the React tooltip
  const onMove = (e) => {
    const el = ref.current;
    if (!el || !result) return;
    const rect = el.getBoundingClientRect();
    const W = el.clientWidth || 700, pad = 40, padR = 12;
    const innerW = W - pad - padR;
    const px = e.clientX - rect.left;
    const innerPx = px - pad;
    const idx = Math.round((innerPx / innerW) * Math.max(1, result.equity.length - 1));
    const clamped = Math.max(0, Math.min(result.equity.length - 1, idx));
    setHover({
      idx: clamped,
      px: e.clientX - rect.left,
      py: e.clientY - rect.top,
    });
  };

  const tooltip = hover && result ? (() => {
    const i = hover.idx;
    const dt = (result.dates[i] || "").slice(0, 10);
    const eqVal = result.equity[i];
    const bhVal = result.bhEquity[i];
    const pos = result.positions[i];
    // Show position as a signed quantity (±1.00 in this scalar-position
    // framework) + the LONG/SHORT/FLAT status.
    const posQty = (pos > 0 ? "+" : "") + Number(pos).toFixed(2);
    const posLabel = pos === 1 ? "LONG" : pos === -1 ? "SHORT" : "FLAT";
    const posCls = pos === 1 ? "pct-pos" : pos === -1 ? "pct-neg" : "";
    const t = tradesByIdx[i];
    // Running PnL within the active trade (entry → hover idx) so the user sees
    // where the trade IS right now, not just its eventual outcome.
    let runningPnl = null, weeksIn = null;
    if (t) {
      const entryIdx = result.dates.indexOf(t.entry_date);
      if (entryIdx >= 0) {
        let acc = 0;
        for (let k = entryIdx + 1; k <= i; k++) acc += result.pnl[k] || 0;
        runningPnl = acc;
        weeksIn = i - entryIdx;
      }
    }
    return (
      <div className="bt-eq-tooltip" style={{ left: hover.px + 14, top: hover.py + 8 }}>
        <div className="bt-tt-date">{dt}</div>
        <div className="bt-tt-row"><span>strategy</span><span>{(eqVal*100).toFixed(1)}%</span></div>
        <div className="bt-tt-row"><span>buy & hold</span><span>{(bhVal*100).toFixed(1)}%</span></div>
        <div className="bt-tt-row"><span>position</span><span className={posCls}>{posQty} · {posLabel}</span></div>
        {t && (
          <div className="bt-tt-trade">
            <div className="bt-tt-row"><span>{t.direction === "Long" ? "▲" : "▼"} trade</span><span>{(t.entry_date || "").slice(0,10)} → {t.exit_date === "(open)" ? "open" : (t.exit_date || "").slice(0,10)}</span></div>
            <div className="bt-tt-row"><span>· in trade</span><span>{weeksIn != null ? `${weeksIn}/${t.weeks}w` : `${t.weeks}w`}</span></div>
            {runningPnl != null && (
              <div className="bt-tt-row"><span>· open pnl</span><span className={runningPnl >= 0 ? "pct-pos" : "pct-neg"}>{(runningPnl*100).toFixed(1)}%</span></div>
            )}
            <div className="bt-tt-row"><span>· final pnl</span><span className={t.pnl >= 0 ? "pct-pos" : "pct-neg"}>{(t.pnl*100).toFixed(1)}%</span></div>
            {t.entry_criteria && <div className="bt-tt-criteria">▶ {t.entry_criteria}</div>}
            {t.exit_criteria  && <div className="bt-tt-criteria" style={{color:"var(--text-dim)"}}>◀ {t.exit_criteria}</div>}
          </div>
        )}
      </div>
    );
  })() : null;

  return (
    <div className="bt-eq-wrap">
      <svg className="bt-eq" ref={ref}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}/>
      {tooltip}
    </div>
  );
}

function BtDistribution({ result }) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const el = ref.current;
    if (!el || !result.bootstrap) return;
    const dist = result.bootstrap.distribution;
    const W = el.clientWidth || 700, H = 110, pad = { l: 40, r: 12, t: 8, b: 22 };
    const innerW = W - pad.l - pad.r, innerH = H - pad.t - pad.b;
    const min = Math.min(...dist, result.metrics.finalPnl);
    const max = Math.max(...dist, result.metrics.finalPnl);
    const nBins = 40;
    const bin = (max - min) / nBins;
    const counts = new Array(nBins).fill(0);
    for (const v of dist) {
      const i = Math.min(nBins - 1, Math.max(0, Math.floor((v - min) / Math.max(1e-9, bin))));
      counts[i]++;
    }
    const maxC = Math.max(...counts, 1);
    const x = i => pad.l + (i / nBins) * innerW;
    const y = c => pad.t + innerH * (1 - c / maxC);
    const g = d3.select(el);
    g.selectAll("*").remove();
    g.attr("viewBox", `0 0 ${W} ${H}`);
    counts.forEach((c, i) => {
      g.append("rect").attr("x", x(i)).attr("y", y(c))
        .attr("width", Math.max(1, innerW / nBins - 1)).attr("height", innerH * (c / maxC))
        .attr("fill", "rgba(126,192,221,0.55)");  // sky-blue bars
    });
    // actual final pnl marker · gold for emphasis against the blue distribution
    const ax = pad.l + ((result.metrics.finalPnl - min) / Math.max(1e-9, max - min)) * innerW;
    g.append("line").attr("x1", ax).attr("x2", ax).attr("y1", pad.t).attr("y2", H - pad.b)
      .attr("stroke", "var(--accent)").attr("stroke-width", 1.5);
    g.append("text").attr("x", ax + 4).attr("y", pad.t + 10)
      .attr("font-family", "JetBrains Mono").attr("font-size", 10).attr("fill", "var(--accent)")
      .text("actual " + (result.metrics.finalPnl * 100).toFixed(0) + "%");
    // x-axis labels
    g.append("text").attr("x", pad.l).attr("y", H - 6).attr("font-family", "JetBrains Mono").attr("font-size", 9)
      .attr("fill", "var(--text-dim)").text((min * 100).toFixed(0) + "%");
    g.append("text").attr("x", W - pad.r).attr("y", H - 6).attr("text-anchor", "end")
      .attr("font-family", "JetBrains Mono").attr("font-size", 9).attr("fill", "var(--text-dim)")
      .text((max * 100).toFixed(0) + "%");
  }, [result]);
  return (
    <div className="bt-dist-wrap">
      <div className="section-head" style={{marginTop:14}}>Bootstrap distribution · final PnL · 1000 runs</div>
      <svg className="bt-dist" ref={ref}/>
    </div>
  );
}

/* ---------------- Top-bar global ticker search ----------------
   Always-visible search input pinned over the top-bar. Type any ticker /
   name / basket; click a match to open the stock drawer (which pops in
   from the right). */
function TopBarSearch({ onPick }) {
  const [q, setQ] = useState("");
  const [focused, setFocused] = useState(false);
  const all = window.ALL_TICKERS || [];
  const matches = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    return all.filter(t =>
      t.ticker.toLowerCase().includes(s) ||
      (t.name || "").toLowerCase().includes(s) ||
      (t.basket_name || "").toLowerCase().includes(s)
    ).slice(0, 10);
  }, [q, all]);

  return (
    <div className="topbar-search">
      <span className="topbar-search-icon" aria-hidden="true">⌕</span>
      <input
        type="text"
        placeholder="Search ticker, name, basket…"
        value={q}
        onChange={e => setQ(e.target.value)}
        onFocus={() => setFocused(true)}
        // 200ms grace so the click on a dropdown item registers before blur
        onBlur={() => setTimeout(() => setFocused(false), 200)}
      />
      {focused && q.trim().length > 0 && (
        <div className="topbar-search-dropdown">
          {matches.length === 0 ? (
            <div className="topbar-search-empty">No matches.</div>
          ) : matches.map(t => (
            <div key={t.ticker} className="topbar-search-item"
              onMouseDown={(e) => {
                e.preventDefault();
                onPick(t.ticker);
                setQ("");
                setFocused(false);
              }}>
              <span className="ts-ticker">{t.ticker}</span>
              <span className="ts-name">{t.name}</span>
              <span className="ts-basket">{t.basket_name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- Refresh-pipeline button ----------------
   The loading screen lives in dashboard.html as #boot-loading. We don't
   render a React copy - we just toggle the existing element via a useEffect
   keyed on `loading` state in App, so the same DOM is the single source of
   truth for both boot hydration and in-app refreshes. */
function RefreshButton({ onRefresh, busy }) {
  // The pipeline runs in a local Python dev server; on hosted builds
  // (Vercel etc.) there is no /api/refresh, so hide the button entirely.
  const host = typeof window !== "undefined" ? window.location.hostname : "";
  const isLocal = host === "localhost" || host === "127.0.0.1" || host === "0.0.0.0" || host === "";
  if (!isLocal) return null;
  return (
    <button
      className={"refresh-btn" + (busy ? " spinning" : "")}
      onClick={onRefresh}
      disabled={busy}
      title="Re-run the data pipeline (consolidate_data.py) and reload"
    >
      <span className="arrow">↻</span>
      <span>refresh</span>
    </button>
  );
}

/* ---------------- View nav (top-bar tabs) ---------------- */
function ViewNav({ view, setView }) {
  return (
    <div className="view-nav">
      {[
        ["dashboard", "Dashboard"],
        ["atlas",     "Atlas"],
        ["backtest",  "Lab"],
      ].map(([id, label]) => (
        <button key={id}
          className={"view-nav-btn" + (view === id ? " active" : "")}
          onClick={() => setView(id)}>
          {label}
        </button>
      ))}
      <a className="view-nav-btn" href="methodology.html">Methodology</a>
    </div>
  );
}

/* ---------------- App ---------------- */
function App() {
  const [view, setView] = useState("dashboard");          // 'dashboard' | 'atlas' | 'backtest'
  // labSeed pre-fills the Backtest config when navigating from a clicked
  // signal card. Bumping `id` forces Backtest to remount (via key=) so its
  // useState seed prop is re-read.
  const [labSeed, setLabSeed] = useState(null);
  const seedLabFromPair = (longId, shortId) => {
    setLabSeed({
      id: Date.now(),
      subjectType: "pair",
      pairLong:  { type: "basket", value: longId },
      pairShort: { type: "basket", value: shortId },
      strategyType: "pair-spread",
    });
    setView("backtest");
  };

  // Loading flow · used for the pipeline-refresh action. We toggle the
  // existing #boot-loading element in dashboard.html (rendered before React
  // mounts) instead of duplicating the markup in React - single source of
  // truth for both boot hydration and in-app refreshes.
  const [loading, setLoading] = useState(null);
  React.useEffect(() => {
    const boot = document.getElementById("boot-loading");
    if (!boot) return;
    if (loading) {
      boot.style.display = "flex";
      const status = boot.querySelector(".loading-status");
      if (status) {
        status.textContent = loading.status;
        status.classList.toggle("error", !!loading.error);
      }
    } else {
      boot.style.display = "none";
    }
  }, [loading]);
  const onRefresh = async () => {
    setLoading({ status: "Re-running pipeline · this can take ~30s" });
    try {
      const resp = await fetch("/api/refresh", { method: "POST" });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok || json.status !== "ok") {
        const msg = (json.stderr || json.error || `HTTP ${resp.status}`).toString().split("\n").slice(-3).join(" · ");
        setLoading({ status: "Pipeline failed · " + msg, error: true });
        setTimeout(() => setLoading(null), 5000);
        return;
      }
      setLoading({ status: "Pipeline complete · reloading" });
      // Hard reload so atlas.jsx re-hydrates from the new JSON.
      setTimeout(() => window.location.reload(), 600);
    } catch (e) {
      setLoading({ status: "Network error · " + e.message + ". If you launched with `python3 -m http.server`, switch to `python3 server.py` to enable /api/refresh.", error: true });
      setTimeout(() => setLoading(null), 6000);
    }
  };
  const [hover, setHover] = useState(null);
  const [selected, setSelected] = useState(null);          // basket id
  const [selectedStock, setSelectedStock] = useState(null);// ticker
  const [mode, setMode] = useState("corr");
  const [tt, setTt] = useState(null);
  const [sideCollapsed, setSideCollapsed] = useState(false);
  const [corrThreshold, setCorrThreshold] = useState(0.7);

  // ESC closes any open drawer
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") {
        if (selectedStock) setSelectedStock(null);
        else if (selected) setSelected(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, selectedStock]);

  // In atlas view, selecting a subgroup auto-pops the screener column open
  // (the same effect as clicking the side-toggle), so the basket drawer
  // pushes the map aside instead of overlaying it.
  React.useEffect(() => {
    if (view === "atlas" && selected && sideCollapsed) {
      setSideCollapsed(false);
    }
  }, [view, selected, sideCollapsed]);

  /* Slide-drawer routing:
       - Stock drawer always pops in from the right (any view, any source).
       - Basket drawer in DASHBOARD view slides in from the left, leaving
         the map column visible.
       - Basket drawer in ATLAS view always renders in the side-pane.
         If the screener column was collapsed when the user clicked a
         basket, the auto-uncollapse effect above pops it open - same
         transition as clicking the side-toggle, pushing the map aside. */
  const rightSlideContent = selectedStock
    ? <StockDrawer
        ticker={selectedStock}
        onClose={() => setSelectedStock(null)}
        onSelectBasket={(id) => { setSelectedStock(null); setSelected(id); }}
      />
    : null;
  const rightSlideOpen = !!rightSlideContent;

  const leftSlideContent = (view === "dashboard" && selected && !selectedStock)
    ? <Drawer
        node={NODE_BY_ID[selected]}
        onClose={() => setSelected(null)}
        onSelectStock={setSelectedStock}
        onSelectBasket={setSelected}
      />
    : null;
  const leftSlideOpen = !!leftSlideContent;

  const slideOpen = leftSlideOpen || rightSlideOpen;

  return (
    <>
      <TopBarSearch onPick={setSelectedStock} />

      <ViewNav view={view} setView={setView}/>
      <RefreshButton onRefresh={onRefresh} busy={!!loading}/>

      {view === "atlas" ? (
        <>
          <button className="view-back" onClick={() => setView("dashboard")}>‹ Dashboard</button>
          <AtlasView
            corrThreshold={corrThreshold} setCorrThreshold={setCorrThreshold}
            mode={mode} setMode={setMode}
            hover={hover} setHover={setHover}
            selected={selected} setSelected={setSelected}
            tt={tt} setTt={setTt}
            sideCollapsed={sideCollapsed} setSideCollapsed={setSideCollapsed}
            onSelectStock={setSelectedStock}
            onSeedLab={seedLabFromPair}
          />
        </>
      ) : view === "backtest" ? (
        <Backtest key={labSeed?.id || "default"} seed={labSeed}/>
      ) : (
        <div className="app dashboard-view">
          <div className="dash-main">
            <Dashboard
              onSelectBasket={setSelected}
              onSelectStock={setSelectedStock}
              onSeedLab={seedLabFromPair}
            />
          </div>
          <MapColumn onOpenAtlas={() => setView("atlas")}/>
        </div>
      )}

      {/* Left-slide · dashboard basket drawer only */}
      <div className={"drawer-slide" + (leftSlideOpen ? " open" : "")}>
        {leftSlideContent}
      </div>

      {/* Right-slide · stock drawer (any view) and atlas basket-when-collapsed */}
      <div className={"drawer-slide-right" + (rightSlideOpen ? " open" : "")}>
        {rightSlideContent}
      </div>

      {slideOpen && (
        <div className="drawer-scrim" onClick={() => {
          setSelected(null);
          setSelectedStock(null);
        }}/>
      )}

      {/* Loading overlay is the static #boot-loading element in dashboard.html
          toggled by the loading-state useEffect above - no React render here. */}
    </>
  );
}

/* Wait for atlas hydration before mounting */
window.ATLAS_READY.then(() => {
  ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
}).catch(() => { /* hydration handler already showed error */ });
