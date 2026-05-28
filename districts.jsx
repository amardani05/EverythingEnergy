/* ============================================================
   Districts · the illustrated island map (SVG)
   Top-down aerial, hairline + flat-fill, editorial restraint
   ============================================================ */

const C = { // stroke shorthand
  line: "#4f5563",
  hi:   "#6b7388",
  dim:  "#344056",
  ink:  "#1d2330",
};

/* ---------------------------------------------------------------
   COASTLINE · a single hand-drawn-feel polygon enclosing the island
   --------------------------------------------------------------- */
const COAST_D = "M 285 248 Q 320 230 360 232 L 372 268 Q 360 285 348 290 Q 380 296 410 286 Q 444 252 478 244 Q 538 224 610 222 Q 690 214 760 210 Q 850 208 940 214 Q 1040 218 1140 226 Q 1224 232 1294 248 Q 1322 254 1334 290 Q 1348 360 1340 460 Q 1334 560 1322 660 Q 1314 730 1290 778 Q 1270 808 1224 818 Q 1140 832 1050 826 Q 940 822 830 822 Q 720 824 614 818 Q 530 810 462 798 Q 412 790 380 770 Q 350 750 332 720 Q 300 690 286 656 Q 268 612 256 564 L 234 540 Q 218 510 224 470 Q 230 432 252 410 Q 270 392 286 372 Q 296 350 292 320 Q 286 290 285 248 Z";

/* ---------------------------------------------------------------
   ZONE polygons · each district as a soft-edged region
   --------------------------------------------------------------- */
const ZONES = {
  port:       { d: "M 200 200 L 460 200 L 460 820 L 200 820 Z", fill: "var(--z-port)",       tex: "url(#hatchPort)" },
  town:       { d: "M 460 200 L 720 200 L 720 380 L 460 380 Z", fill: "var(--z-town)",       tex: "url(#townGrid)"  },
  industrial: { d: "M 460 380 L 720 380 L 720 600 L 460 600 Z", fill: "var(--z-industrial)", tex: null              },
  equipment:  { d: "M 720 380 L 880 380 L 880 600 L 720 600 Z", fill: "var(--z-equipment)",  tex: "url(#hatchSlope)" },
  quarry:     { d: "M 720 200 L 1080 200 L 1080 380 L 720 380 Z", fill: "var(--z-quarry)",   tex: "url(#quarryHatch)" },
  farmland:   { d: "M 1080 200 L 1340 200 L 1340 600 L 1080 600 Z", fill: "var(--z-farm)",   tex: "url(#rowsCrop)"   },
  oilfield:   { d: "M 460 600 L 880 600 L 880 820 L 460 820 Z", fill: "var(--z-oilfield)",   tex: "url(#sand)"       },
  power:      { d: "M 880 600 L 1080 600 L 1080 820 L 880 820 Z", fill: "var(--z-power)",    tex: "url(#powerGrid)"  },
  nuclear:    { d: "M 1080 600 L 1340 600 L 1340 820 L 1080 820 Z", fill: "var(--z-nuclear)", tex: "url(#hatchSlope)" },
};

/* ---------------------------------------------------------------
   MOTIFS · one per sub-industry
   Each is a JSX fragment drawn around (0,0) ~ 40-50px box
   Class conventions:
     .motif-stroke  → hairline outlines (highlights gold on hover)
     .motif-fill    → flat fill blocks  (highlights gold on hover)
   --------------------------------------------------------------- */

// Oil derrick · truss tower, top-down-tilted view
const Derrick = () => (
  <g>
    <path className="motif-stroke" d="M -7 14 L -3 -16 L 3 -16 L 7 14 Z" stroke={C.hi} strokeWidth="0.8" fill="none"/>
    <path className="motif-stroke" d="M -5 6 L 5 6 M -4 -2 L 4 -2 M -3 -10 L 3 -10" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <path className="motif-stroke" d="M -7 14 L 3 -16 M 7 14 L -3 -16" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <rect className="motif-fill" x="-9" y="14" width="18" height="3" fill={C.ink}/>
    <path className="motif-stroke" d="M 0 -16 L 0 -22" stroke={C.hi} strokeWidth="0.7" fill="none"/>
  </g>
);

// Pumpjack · horsehead pumping unit
const Pumpjack = () => (
  <g>
    <rect className="motif-fill" x="-12" y="6" width="24" height="3" fill={C.ink}/>
    <path className="motif-stroke" d="M -4 6 L -4 -4 L -10 -10 M 4 6 L 4 -4" stroke={C.hi} strokeWidth="0.8" fill="none"/>
    <path className="motif-stroke" d="M -10 -10 L 10 -8 L 13 -4 L 13 -10 L 10 -8" stroke={C.hi} strokeWidth="0.8" fill="none" strokeLinejoin="round"/>
    <circle className="motif-fill" cx="0" cy="-5" r="1.4" fill={C.line}/>
  </g>
);

// Gas pad · wellhead with flare stack
const GasPad = () => (
  <g>
    <rect className="motif-fill" x="-12" y="6" width="24" height="3" fill={C.ink}/>
    <rect className="motif-stroke" x="-9" y="-2" width="6" height="8" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <rect className="motif-stroke" x="-1" y="-4" width="6" height="10" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    {/* Flare */}
    <path className="motif-stroke" d="M 9 6 L 9 -10" stroke={C.hi} strokeWidth="0.8" fill="none"/>
    <path className="motif-fill" d="M 7 -10 L 11 -10 L 9 -16 Z" fill={C.line} stroke="none"/>
  </g>
);

// Distillation column · refiner stacks
const Column = () => (
  <g>
    <rect className="motif-fill" x="-14" y="10" width="28" height="3" fill={C.ink}/>
    <rect className="motif-stroke" x="-9" y="-12" width="5" height="22" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <rect className="motif-stroke" x="-2" y="-16" width="5" height="26" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <rect className="motif-stroke" x="5" y="-8" width="5" height="18" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -7 -8 L -6 -8 M -7 -4 L -6 -4 M -7 0 L -6 0 M 0 -12 L 1 -12 M 0 -8 L 1 -8 M 0 -4 L 1 -4 M 7 -4 L 8 -4 M 7 0 L 8 0" stroke={C.line} strokeWidth="0.4" fill="none"/>
  </g>
);

// LNG sphere · spherical tank cluster
const LNGSphere = () => (
  <g>
    <circle className="motif-stroke" cx="-7" cy="0" r="7" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <circle className="motif-stroke" cx="7" cy="0" r="7" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -7 -7 L -7 -10 M 7 -7 L 7 -10" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <path className="motif-stroke" d="M -10 7 L -4 7 M 4 7 L 10 7" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <circle className="motif-fill" cx="-7" cy="0" r="2" fill={C.line}/>
    <circle className="motif-fill" cx="7" cy="0" r="2" fill={C.line}/>
  </g>
);

// Petrochem cracker · boxy furnace
const Cracker = () => (
  <g>
    <rect className="motif-stroke" x="-12" y="-6" width="24" height="14" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -8 -6 L -8 -16 M 0 -6 L 0 -16 M 8 -6 L 8 -16" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <rect className="motif-fill" x="-9" y="-18" width="2" height="2" fill={C.line}/>
    <rect className="motif-fill" x="-1" y="-18" width="2" height="2" fill={C.line}/>
    <rect className="motif-fill" x="7" y="-18" width="2" height="2" fill={C.line}/>
    <path className="motif-stroke" d="M -10 8 L 10 8" stroke={C.line} strokeWidth="0.5" fill="none"/>
  </g>
);

// Pipeline · parallel pipes with valve dots
const Pipes = () => (
  <g>
    <path className="motif-stroke" d="M -16 -4 L 16 -4 M -16 0 L 16 0 M -16 4 L 16 4" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <circle className="motif-fill" cx="-8" cy="-4" r="1.4" fill={C.line}/>
    <circle className="motif-fill" cx="6" cy="0" r="1.4" fill={C.line}/>
    <circle className="motif-fill" cx="-2" cy="4" r="1.4" fill={C.line}/>
    <path className="motif-stroke" d="M 16 -4 L 22 -8 M 16 4 L 22 8" stroke={C.line} strokeWidth="0.5" fill="none"/>
  </g>
);

// Compressor · circular compressor station
const Compressor = () => (
  <g>
    <circle className="motif-stroke" cx="-6" cy="0" r="6" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <circle className="motif-stroke" cx="-6" cy="0" r="3" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <rect className="motif-stroke" x="2" y="-5" width="12" height="10" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M 0 0 L 2 0 M 14 -2 L 18 -4 M 14 2 L 18 4" stroke={C.line} strokeWidth="0.5" fill="none"/>
  </g>
);

// IOC anchor block · diamond monogram
const IOCBlock = () => (
  <g>
    <path className="motif-stroke" d="M 0 -10 L 12 0 L 0 10 L -12 0 Z" stroke={C.hi} strokeWidth="0.8" fill="none"/>
    <path className="motif-stroke" d="M 0 -6 L 8 0 L 0 6 L -8 0 Z" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <circle className="motif-fill" cx="0" cy="0" r="1.4" fill={C.line}/>
  </g>
);

// Equipment stack · drillpipe + crown block
const PipeStack = () => (
  <g>
    <rect className="motif-stroke" x="-12" y="-2" width="24" height="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <rect className="motif-stroke" x="-12" y="2" width="24" height="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <rect className="motif-stroke" x="-12" y="6" width="24" height="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <path className="motif-stroke" d="M -10 -2 L -10 -12 L 10 -12 L 10 -2 M -6 -12 L -6 -2 M 6 -12 L 6 -2" stroke={C.hi} strokeWidth="0.7" fill="none"/>
  </g>
);

// Open-pit mine · terraces
const Pit = () => (
  <g>
    <ellipse className="motif-stroke" cx="0" cy="0" rx="14" ry="9" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <ellipse className="motif-stroke" cx="0" cy="0" rx="10" ry="6.5" stroke={C.hi} strokeWidth="0.5" fill="none"/>
    <ellipse className="motif-stroke" cx="0" cy="0" rx="6" ry="4" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <ellipse className="motif-fill" cx="0" cy="0" rx="2" ry="1.4" fill={C.line}/>
    <path className="motif-stroke" d="M 14 0 L 18 -4 L 16 -8" stroke={C.line} strokeWidth="0.5" fill="none"/>
  </g>
);

// Minerals claim grid · surveyor parcel
const Claim = () => (
  <g>
    <rect className="motif-stroke" x="-13" y="-9" width="26" height="18" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -13 -3 L 13 -3 M -13 3 L 13 3 M -7 -9 L -7 9 M 0 -9 L 0 9 M 7 -9 L 7 9" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <circle className="motif-fill" cx="-7" cy="-3" r="0.9" fill={C.line}/>
    <circle className="motif-fill" cx="7" cy="3" r="0.9" fill={C.line}/>
  </g>
);

// Biofuels silo · corn silo
const Silo = () => (
  <g>
    <path className="motif-stroke" d="M -10 8 L -10 -6 L -2 -12 L 6 -12 L 6 8 Z" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -10 -2 L 6 -2 M -10 4 L 6 4" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <path className="motif-stroke" d="M 8 8 L 8 0 L 14 0 L 14 8 Z" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <rect className="motif-fill" x="-12" y="8" width="28" height="2" fill={C.ink}/>
  </g>
);

// Gas turbine peaker · angled box + stack
const Peaker = () => (
  <g>
    <rect className="motif-stroke" x="-12" y="-2" width="20" height="10" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -10 -2 L -10 -10 L -6 -10 L -6 -2" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M 8 8 L 14 8 L 14 0 L 8 0" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <circle className="motif-fill" cx="2" cy="3" r="2.4" fill="none" stroke={C.hi} strokeWidth="0.5"/>
    <path className="motif-stroke" d="M 2 0.5 L 2 5.5 M -0.5 3 L 4.5 3" stroke={C.line} strokeWidth="0.4" fill="none"/>
  </g>
);

// Wind turbine · three vertical poles with blades
const WindFarm = () => (
  <g>
    {[-12, 0, 12].map((cx, i) => (
      <g key={i} transform={`translate(${cx},0)`}>
        <path d="M 0 8 L 0 -8" stroke={C.hi} strokeWidth="0.7" fill="none"/>
        <circle cx="0" cy="-8" r="1.2" fill={C.line}/>
        <path d="M 0 -8 L -5 -12 M 0 -8 L 5 -12 M 0 -8 L 0 -2" stroke={C.line} strokeWidth="0.5" fill="none" className="motif-stroke"/>
      </g>
    ))}
    <path className="motif-stroke" d="M -16 8 L 16 8" stroke={C.line} strokeWidth="0.4" fill="none"/>
  </g>
);

// Substation · busbar + transformers
const Substation = () => (
  <g>
    <path className="motif-stroke" d="M -14 -6 L 14 -6 M -14 -3 L 14 -3" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <circle className="motif-stroke" cx="-8" cy="3" r="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <circle className="motif-stroke" cx="-8" cy="7" r="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <circle className="motif-stroke" cx="0" cy="3" r="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <circle className="motif-stroke" cx="0" cy="7" r="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <circle className="motif-stroke" cx="8" cy="3" r="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <circle className="motif-stroke" cx="8" cy="7" r="3" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <path className="motif-stroke" d="M -8 -3 L -8 0 M 0 -3 L 0 0 M 8 -3 L 8 0" stroke={C.line} strokeWidth="0.5" fill="none"/>
  </g>
);

// Gas regulator station · meter + pipe
const Regulator = () => (
  <g>
    <rect className="motif-stroke" x="-10" y="-6" width="20" height="12" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <circle className="motif-stroke" cx="-3" cy="0" r="3" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <circle className="motif-stroke" cx="5" cy="0" r="3" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <path className="motif-stroke" d="M -10 0 L -14 0 M 10 0 L 14 0" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <circle className="motif-fill" cx="-3" cy="0" r="0.8" fill={C.line}/>
    <circle className="motif-fill" cx="5" cy="0" r="0.8" fill={C.line}/>
  </g>
);

// Uranium hex · molecule
const HexOre = () => (
  <g>
    <path className="motif-stroke" d="M -8 -5 L 0 -10 L 8 -5 L 8 5 L 0 10 L -8 5 Z" stroke={C.hi} strokeWidth="0.8" fill="none"/>
    <path className="motif-stroke" d="M -8 -5 L 8 5 M 8 -5 L -8 5 M 0 -10 L 0 10" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <circle className="motif-fill" cx="0" cy="0" r="1.6" fill={C.line}/>
  </g>
);

// SMR · small modular reactor cylinder + dome
const SMR = () => (
  <g>
    <path className="motif-stroke" d="M -7 -8 Q -7 -12 0 -12 Q 7 -12 7 -8 L 7 8 L -7 8 Z" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -7 -2 L 7 -2 M -7 4 L 7 4" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <circle className="motif-fill" cx="0" cy="-6" r="2" fill={C.line}/>
    <rect className="motif-fill" x="-10" y="8" width="20" height="2" fill={C.ink}/>
  </g>
);

// Town gas-station canopy
const Canopy = () => (
  <g>
    <rect className="motif-stroke" x="-12" y="-8" width="24" height="3" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -10 -5 L -10 6 M 10 -5 L 10 6" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <rect className="motif-stroke" x="-3" y="-2" width="6" height="8" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <path className="motif-fill" d="M 1 -1 L 2 -1 L 2 1 L 1 1 Z" fill={C.line}/>
    <path className="motif-stroke" d="M -14 6 L 14 6" stroke={C.line} strokeWidth="0.5" fill="none"/>
  </g>
);

// Tanker ship · top-down hull
const Ship = () => (
  <g>
    <path className="motif-stroke" d="M -16 -4 L 12 -4 Q 18 0 12 4 L -16 4 Q -18 0 -16 -4 Z" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <rect className="motif-stroke" x="-12" y="-2" width="3" height="4" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <rect className="motif-stroke" x="-6" y="-2" width="3" height="4" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <rect className="motif-stroke" x="0" y="-2" width="3" height="4" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <rect className="motif-fill" x="6" y="-2" width="4" height="4" fill={C.line}/>
  </g>
);

// Jackup rig · three-leg offshore platform
const Jackup = () => (
  <g>
    <path className="motif-stroke" d="M -14 -8 L 14 -8 L 14 -2 L -14 -2 Z" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <path className="motif-stroke" d="M -10 -8 L -10 14 M 0 -8 L 0 16 M 10 -8 L 10 14" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <path className="motif-stroke" d="M -2 -14 L 0 -8 L 2 -14" stroke={C.line} strokeWidth="0.5" fill="none"/>
    <circle className="motif-fill" cx="-10" cy="14" r="1.2" fill={C.line}/>
    <circle className="motif-fill" cx="0" cy="16" r="1.2" fill={C.line}/>
    <circle className="motif-fill" cx="10" cy="14" r="1.2" fill={C.line}/>
  </g>
);

// ROV / supply vessel · boat with crane
const Rov = () => (
  <g>
    <path className="motif-stroke" d="M -14 -2 L 10 -2 Q 16 1 10 4 L -14 4 Z" stroke={C.hi} strokeWidth="0.7" fill="none"/>
    <rect className="motif-stroke" x="-10" y="-6" width="6" height="4" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <path className="motif-stroke" d="M -2 -2 L -2 -10 L 6 -10" stroke={C.hi} strokeWidth="0.6" fill="none"/>
    <path className="motif-stroke" d="M 6 -10 L 6 -6" stroke={C.line} strokeWidth="0.4" fill="none"/>
    <circle className="motif-fill" cx="6" cy="-5" r="1" fill={C.line}/>
  </g>
);

const MOTIF_BY_NODE = {
  upstream_oil_eandp: Pumpjack,
  upstream_gas_eandp: GasPad,
  ofs_onshore: Derrick,
  ofs_equipment: PipeStack,
  iocs_integrated: IOCBlock,
  midstream_pipelines: Pipes,
  midstream_gpt: Compressor,
  lng_terminals: LNGSphere,
  downstream_refiners: Column,
  petrochem: Cracker,
  coal: Pit,
  minerals_royalty: Claim,
  downstream_biofuels: Silo,
  power_ipps_merchant: Peaker,
  power_renewables: WindFarm,
  power_utilities_regulated_gas: Substation,
  power_utilities_regulated_ldc: Regulator,
  uranium_nuclear_fuel: HexOre,
  nuclear_smr_developers: SMR,
  downstream_retail: Canopy,
  tanker_shipping: Ship,
  upstream_offshore_drillers: Jackup,
  ofs_offshore: Rov,
};

window.MOTIF_BY_NODE = MOTIF_BY_NODE;
window.ZONES = ZONES;
window.COAST_D = COAST_D;
