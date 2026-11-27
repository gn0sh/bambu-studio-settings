# Photo instructions (send this to the maker, as is)

Please take 4 photos of the tile, in daylight or under a bright lamp, no flash, phone held steady:

1. **Top**, straight down, the whole tile filling the frame, the ID panel readable.
2. **Three-quarter**, from the front-left corner, about 45 degrees down.
3. **Front, low**: phone almost level with the table, looking at the front edge (the overhang fins with the degree
   labels), lit from above or behind you so the undersides show.
4. **Back, low**: the same from the back edge, to see under the bridges (5-25 mm).

For photos 3 and 4, **lay the phone flat on the table** about 15 cm (6 in) from the tile. Holding it, even low,
is usually still too high to see under anything.

- Put a ruler (or calipers) along one edge in photo 1.
- A dark background helps light filament, a light background dark filament.
- Leave the tile on the table as printed (don't break anything off first).

If you have calipers, also send:
- the 20.00 square boss across, at its bottom edge and 3 mm up (both directions);
- the 10 mm hole;
- the 2 mm dot pin (brand column, right edge).

Then try the six pins in the middle: push or twist each one by hand and tell me the **tightest one that moves**
(0.10, 0.15, 0.20, 0.25, 0.30 or 0.40).

And tell me: printer and nozzle, the filament (brand, line, color, how long it has been open / dried), and any
settings you changed from the defaults. The ID panel shows what the file was made for.

Also send a photo of the **spool's label**: brand, line and the printing temperature range. Third-party filament
printed with a brand-name system preset can run at the top of (or outside) its own range; check this first when
you see stringing.

## Checking the photos you get back

- ID panel readable? If not, ask which tile it is before reading anything.
- Undersides of the fins visible in photo 3? If the light is from the front they look black - ask for a retake
  with the light behind the phone.
- Photos blurred or too far away to see the 2.5 mm wordmark? Ask for a close-up of the back-left corner.
- Never guess a dimension from a photo; ask for the caliper reading.
- What you read off the photos and the spool label (brand, line, color, verdicts) goes into the registry only
  through a JSON file you write and `registry.py add --from <file>` or `registry.py record --from <file>`, never
  on a command line: label text can hold `$(...)` or `%VAR%` that a shell would run (SKILL.md rule 8).
- A dim or soft photo hides hairs. If a later, sharper photo shows more, say so and correct the earlier verdict.
- Empty pin collars: the maker may have pushed loose pins out. Ask which ones moved rather than assuming.
