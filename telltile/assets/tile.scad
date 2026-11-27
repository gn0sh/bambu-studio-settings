// gnoshcodes tuning tile v1 - a one-plate calibration print read from 2-4 phone photos.
// Every feature carries its value as raised (embossed) or cut (engraved) text.
// Coordinates: X = left->right, Y = front(0)->back, Z up. Front edge faces the camera.
// Units mm. All Z steps are multiples of 0.2 (designed for 0.20 mm layers, 0.20 first layer).
//
// A gnoshcodes project. 112 x 84 mm, fits any bed of 120 x 90 mm or more (about 4 mm clear on each side).
// 0.4 mm nozzle, 0.20 mm layers, no supports.
// ID panel text is set here or from the command line, e.g.
//   openscad -D 'PRINTER="P1S"' -D 'FIL="PLA-NAVY-01"' -o tile.stl tile.scad
// Leave PRINTER and FIL empty for the generic tile: line 1 reads "0.4 | 0.20 | v1" and line 2 is a flat strip
// to write the filament on with a marker.

/* [ID panel] */
// Printer name on the ID panel (empty = generic)
PRINTER = "";
// Filament code on the ID panel, e.g. PLA-NAVY-01 (empty = blank strip for a marker)
FIL     = "";
// Layer height the tile is printed at (text only; the geometry is designed for 0.20)
LAYER   = "0.20";
VER     = "v1";
/* [Hidden] */
PART    = "all";   // all | base (everything but pins) | pins

FONT  = "Liberation Sans:style=Bold";
W = 112;   D = 84;          // footprint (core 100 x 84 + a 12 mm brand-benchmark column on the right)
W0 = 100;                   // core width the engineered rows are laid out in
B = 0.6;                    // base plate thickness (3 layers)
EMB = 0.4;                  // emboss height for labels on the base (2 layers)
ENG = 0.6;                  // engrave depth
$fn = 64;

// raised label: sinks 0.2 into the surface it stands on so it always fuses; engraved cuts use sink=0
module label(t, s=4, h=EMB, ha="center", va="center", sink=0.2)
    translate([0, 0, -sink]) linear_extrude(h + sink) text(t, size=s, font=FONT, halign=ha, valign=va);

// ---------------------------------------------------------------- base with the boss window
WIN = [2, 33, 28, 59];      // x0,y0,x1,y1 of the window round the 20 mm boss (boss stands on the bed)
module base() {
    difference() {
        cube([W, D, B]);
        translate([WIN[0], WIN[1], -1]) cube([WIN[2]-WIN[0], WIN[3]-WIN[1], B+2]);
        hole_pad_holes();
        tol_holes();
    }
    // strip joining the boss to the plate (back side of the window)
    translate([14, 55, 0]) cube([4, WIN[3]-55+0.01, B]);
}

// ---------------------------------------------------------------- R1: overhang fins (front edge)
ANG = [30, 40, 45, 50, 55, 60, 70];     // degrees from vertical
FP = 5;  FT = 3;  FW = 9;  FPITCH = 13.4; FX0 = 9.8;  FY = 9;   // projection, root, width, pitch
function fin_h(a) = round((FP / tan(a)) / 0.2) * 0.2;          // snapped to layers
module fins() {
    for (i = [0:len(ANG)-1]) {
        a = ANG[i]; h = fin_h(a); p = h * tan(a);            // true angle kept after snapping
        translate([FX0 + i*FPITCH - FW/2, 0, B]) {
            rotate([90, 0, 90]) linear_extrude(FW)
                polygon([[FY+FT, -0.2], [FY+FT, h], [FY-p, h], [FY, 0], [FY, -0.2]]);   // root sunk 0.2 into the plate so it fuses
            // label on the fin top
            translate([FW/2, FY + FT - 4.2, h]) label(str(a, "°"), s=3.6, h=0.4);
        }
    }
}

// ---------------------------------------------------------------- R2: ID panel + thin ribs / slot gauge
module id_panel() {
    line1 = PRINTER == "" ? str("0.4 | ", LAYER, " | ", VER) : str(PRINTER, " | ", LAYER, " | ", VER);
    difference() {
        translate([2, 15, B]) cube([52, 15, 1.4]);
        translate([28, 26, B+1.4-ENG]) label(line1, s=4.2, h=1, sink=0);
        if (FIL != "") translate([28, 19, B+1.4-ENG]) label(FIL, s=4.2, h=1, sink=0);
    }
}
RIB = [0.4, 0.6, 0.8, 1.0, 1.2, 1.6];
RX0 = 61; RP = 6.8;
module ribs() {
    for (i = [0:len(RIB)-1]) {
        x = RX0 + i*RP;
        translate([x - RIB[i]/2, 25, B]) cube([RIB[i], 5, 5]);           // rib
        lab = RIB[i] < 1 ? str(".", round(RIB[i]*10)) : (RIB[i] == 1 ? "1.0" : str(RIB[i]));
        translate([x, 16.6, B]) label(lab, s=2.8);
    }
    // slot gauge: same widths as slots, same columns as the ribs
    difference() {
        translate([RX0 - 3.4, 19.6, B]) cube([RP*len(RIB), 4.2, 3]);
        for (i = [0:len(RIB)-1]) translate([RX0 + i*RP - RIB[i]/2, 19, B+0.6]) cube([RIB[i], 6, 4]);
    }
}

// ---------------------------------------------------------------- R3: 20 mm boss, tolerance pins, hole pad
module boss() {
    difference() {
        translate([6, 36, 0]) cube([20, 20, 6]);
        translate([16, 38.6, 6-0.4]) label("20.00", s=3, h=1, sink=0);          // small, leaves the top flat
    }
}
TOL = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40];
PIN_D = 5; COL_D = 10; TH = 4;     // pin diameter, collar outside diameter, collar top z
function tol_xy(i) = [38.5 + (i%3)*12.6, 40.5 + floor(i/3)*15];
module tol_holes() for (i=[0:5]) translate([tol_xy(i)[0], tol_xy(i)[1], -1]) cylinder(d=PIN_D+2*TOL[i], h=TH+2, $fn=96);
module tol_block() {
    for (i=[0:5]) difference() {
        translate([tol_xy(i)[0], tol_xy(i)[1], 0]) cylinder(d=COL_D, h=TH, $fn=96);
        tol_holes();
    }
    for (i=[0:5]) {
        t = TOL[i]; lab = (round(t*100) % 10 == 0) ? str(t, "0") : str(t);
        translate([tol_xy(i)[0], tol_xy(i)[1] - 7.5, B]) label(lab, s=3);
    }
}
module pins() for (i=[0:5]) translate([tol_xy(i)[0], tol_xy(i)[1], 0]) {
    cylinder(d=PIN_D - 0.6, h=0.4, $fn=96);                // relief so the first layer cannot weld it
    translate([0,0,0.4]) cylinder(d=PIN_D, h=6.4-0.4, $fn=96);
    translate([0,0,6.4]) cylinder(d1=PIN_D, d2=PIN_D-1, h=0.6, $fn=96);   // grip chamfer, top at 7.0
}
HP = [71, 33, 99, 60]; HPH = 3;
HOLES = [[85, 52.6, 10], [75.4, 37.6, 3], [81.0, 37.6, 4], [87.6, 37.6, 5], [94.6, 37.6, 6]];
module hole_pad_holes() for (h = HOLES) translate([h[0], h[1], -1]) cylinder(d=h[2], h=HPH+2, $fn=96);
module hole_pad() {
    difference() { translate([HP[0], HP[1], 0]) cube([HP[2]-HP[0], HP[3]-HP[1], HPH]); hole_pad_holes(); }
    translate([75.2, 52.6, HPH]) label("10", s=3.2, h=0.4);
    for (h = [for (i=[1:4]) HOLES[i]]) translate([h[0], 43.8, HPH]) label(str(h[2]), s=3.2, h=0.4);
}

// ---------------------------------------------------------------- R4: brand text samples, seam cylinder, tip, strings
// gnoshcodes lockup geometry from assets/brand/ (the two ink layers merged: one-color print).
// SVG units (1 unit = 1 mm at scale 1): hook mark + dot x 3..37, y 1.7..40.6; wordmark x 54.8..215.7,
// baseline y 8.6, ascender top y 36.1 (ascender height 27.5), descender bottom y 2.0. Measured from the files.
module inks() union() {
    import("brand/mural-ink-navy.svg", dpi = 25.4);
    import("brand/mural-ink-orange.svg", dpi = 25.4);
}
// hook mark (with its dot), bottom-left at the origin, hgt mm tall
module hook(hgt) scale(hgt / 38.9) translate([-3, -1.7]) intersection() { inks(); square([45, 44]); }
// "gnoshcodes" wordmark, baseline at y 0, left at x 0, ascender (h, d) asc mm tall
function wm_len(asc) = 160.9 * asc / 27.5;
module wordmark(asc) scale(asc / 27.5) translate([-54.8, -8.6]) intersection() { inks(); translate([50, 0]) square([170, 44]); }

WM  = [3.5, 3, 2.5];                  // wordmark ascender heights, mm ("smallest legible size")
WMY = [72.6, 68.2, 64.2];             // baselines (back row = biggest)
module text_sample() {
    // left column: raised 0.6 mm, each size labelled
    for (i = [0:2]) {
        translate([5.8, WMY[i], B - 0.2]) linear_extrude(0.8) wordmark(WM[i]);
        translate([5.2, WMY[i] + WM[i]*0.4, B]) label(str(WM[i]), s=2, ha="right");
    }
    // right column: engraved 0.6 mm into a 1.4 mm pad, same sizes, same rows
    difference() {
        translate([28, 62, B]) cube([30, 15, 1.4]);
        for (i = [0:2]) {
            translate([33, WMY[i], B + 1.4 - ENG]) linear_extrude(1) wordmark(WM[i]);
            translate([32.4, WMY[i] + WM[i]*0.4, B + 1.4 - ENG]) label(str(WM[i]), s=2, h=1, sink=0, ha="right");
        }
    }
}

// ---------------------------------------------------------------- brand-benchmark column (x 100..112)
HOOKS = [[10, 48.6], [6, 33], [3, 21.6]];     // hook height mm, bottom y
DOTS  = [[2, 11.2], [1.5, 7.2], [1, 3.4]];    // dot pin diameter mm, center y
module brand_column() {
    for (h = HOOKS) {
        translate([106 - h[0]*34/38.9/2, h[1], B - 0.2]) linear_extrude(0.8) hook(h[0]);
        translate([106, h[1] - 2.6, B]) label(str(h[0]), s=2.6);
    }
    for (d = DOTS) {
        translate([108.6, d[1], B - 0.2]) cylinder(d=d[0], h=1.2 + 0.2, $fn=48);   // dot pins, 1.2 mm tall
        translate([103.6, d[1], B]) label(str(d[0]), s=2.4);
    }
}
module tall_things() {
    translate([64, 71, B]) cylinder(d=10, h=8, $fn=128);                      // seam cylinder
    translate([64, 63.4, B]) label("SEAM", s=2.8);
    translate([77, 71, B]) cylinder(d=4, h=10);                                 // string tower 1
    translate([88, 71, B]) cylinder(d1=8, d2=0.01, h=10, $fn=96);             // pointed tip (cooling)
    translate([88, 63.4, B]) label("TIP", s=2.8);
    translate([99, 71, B]) cylinder(d=4, h=10);                                 // string tower 2
    translate([99, 65.8, B]) label("STR", s=2.6);
    translate([77, 65.8, B]) label("STR", s=2.6);
}

// ---------------------------------------------------------------- R5: bridges (back edge)
SPANS = [5, 10, 15, 20, 25];
PW = 3; BRH = 4; DECK = 0.8; BY0 = 77.4; BDEP = 6.4;
module bridges() {
    x0 = (W0 - (len(SPANS)+1)*PW - 75) / 2;
    for (i = [0:len(SPANS)]) {
        px = x0 + i*PW + (i == 0 ? 0 : sum(SPANS, i));
        translate([px, BY0, B]) cube([PW, BDEP, BRH + DECK]);
        if (i < len(SPANS)) {
            translate([px + PW, BY0, B + BRH]) cube([SPANS[i], BDEP, DECK]);
            translate([px + PW + SPANS[i]/2, BY0 + BDEP/2, B + BRH + DECK])
                label(SPANS[i] >= 15 ? str(SPANS[i], "mm") : str(SPANS[i]), s=3.4, h=0.4);
        }
    }
}
function sum(v, n) = n <= 0 ? 0 : v[n-1] + sum(v, n-1);

module tile_body() {
    union() {
        base(); fins(); id_panel(); ribs(); boss(); tol_block(); hole_pad();
        text_sample(); tall_things(); bridges(); brand_column();
    }
}
if (PART == "all") { tile_body(); pins(); }
else if (PART == "base") tile_body();
else if (PART == "pins") pins();

// ---------------------------------------------------------------- feature table for scripts/check_gcode.py
// Each ECHO line: FEAT;name;x;y;expected top z;search radius;kind  (kind top = highest extrusion near x,y;
// bridge = a "Bridge" extrusion at z near x,y)
module feat(n, x, y, z, r, k="top") echo(str("FEAT;", n, ";", x, ";", y, ";", z, ";", r, ";", k));
for (i = [0:len(ANG)-1]) feat(str("overhang fin ", ANG[i], "deg"), FX0 + i*FPITCH, FY + FT - 0.6, B + fin_h(ANG[i]), 0.5);
for (i = [0:len(SPANS)-1]) {
    x0 = (W0 - (len(SPANS)+1)*PW - 75) / 2;
    cx = x0 + (i+1)*PW + sum(SPANS, i) + SPANS[i]/2;
    feat(str("bridge ", SPANS[i], "mm deck"), cx, BY0 + 0.6, B + BRH + DECK, 0.4);
    feat(str("bridge ", SPANS[i], "mm first layer"), cx, BY0 + BDEP/2, B + BRH + 0.2, 1.5, "bridge");
}
for (i = [0:5]) feat(str("pin ", TOL[i]), tol_xy(i)[0], tol_xy(i)[1], 7.0, 1.2);
for (i = [0:5]) feat(str("collar ", TOL[i]), tol_xy(i)[0] + COL_D/2 - 0.6, tol_xy(i)[1], TH, 0.4);
for (i = [0:len(RIB)-1]) feat(str("rib ", RIB[i]), RX0 + i*RP, 27.5, B + 5, 0.45);
for (i = [0:len(RIB)-1]) feat(str("slot gauge wall ", i), RX0 + i*RP + 2, 21.7, B + 3, 0.5);
feat("boss 20mm (flat top)", 16, 50, 6, 1.5);
feat("hole pad", 98, 47, HPH, 0.6);
feat("seam cylinder", 64, 71, B + 8, 1.5);
feat("string tower L", 77, 71, B + 10, 1.2);
feat("string tower R", 99, 71, B + 10, 1.2);
feat("pointed tip", 88, 71, B + 10, 0.8, "tip");
feat("ID panel", 3, 16, B + 1.4, 0.6);
feat("text pad", 57.5, 70, B + 1.4, 0.6);
for (i = [0:2]) feat(str("wordmark ", WM[i], "mm raised"), 5.8 + wm_len(WM[i])/2, WMY[i] + WM[i]*0.4, B + 0.6, WM[i]*0.5, "info");
for (h = HOOKS) feat(str("hook ", h[0], "mm"), 106, h[1] + h[0]/2, B + 0.6, h[0]/2, h[0] < 4 ? "info" : "top");
for (d = DOTS) feat(str("dot pin ", d[0], "mm"), 108.6, d[1], B + 1.2, 0.6, d[0] < 1.5 ? "info" : "top");
feat("base", 99.5, 0.5, B, 0.4);
echo(str("TILE;", W, ";", D));
