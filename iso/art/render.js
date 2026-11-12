// Render the boot artwork; run by hand after changing boot.svg, the PNGs are committed.
//
//   node iso/art/render.js        (needs the Playwright package with Chromium)
//
// GRUB (UEFI), iso/grub-theme/:
//   background.png  1920x1080 without the wordmark; GRUB crops it to the screen
//   wordmark.png    logo and name, placed by theme.txt next to the menu
//   select_*.png    9-slice box behind the selected entry
// isolinux (BIOS): iso/splash.png, 640x480, centre 4:3 part with the wordmark.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const ART = __dirname;
const ISO = path.dirname(ART);
const THEME = path.join(ISO, "grub-theme");
const ACCENT = "#e08cc8";
const R = 8;
const SVG = fs.readFileSync(path.join(ART, "boot.svg"), "utf8");

async function show(page, width, height, body) {
  await page.setViewportSize({ width, height });
  await page.setContent(`<body style="margin:0;overflow:hidden;background:transparent">${body}</body>`);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await show(page, 1920, 1080, SVG.replace('<g id="wordmark"', '<g id="wordmark" visibility="hidden"'));
  await page.screenshot({ path: path.join(THEME, "background.png") });

  // Wordmark alone at 0.6 scale (about 330 px wide), transparent around it.
  const mark = SVG.replace(/<rect [^>]*\/>/g, "").replace(/<g transform="translate\(1200[\s\S]*?<\/g>\n/, "")
    .replace('width="1920" height="1080"', 'width="1152" height="648"');
  await show(page, 1152, 648, mark);
  await page.screenshot({ path: path.join(THEME, "wordmark.png"), omitBackground: true,
                          clip: { x: 195, y: 140, width: 340, height: 90 } });

  await show(page, 640, 480, SVG.replace('width="1920" height="1080"',
    'width="853.33" height="480" style="display:block;margin-left:-106.67px"'));
  await page.screenshot({ path: path.join(ISO, "splash.png") });

  // Rounded box in nine pieces: corners R x R, edges and centre 4 px.
  const size = 2 * R + 4;
  await show(page, size, size,
    `<div style="width:${size}px;height:${size}px;border-radius:${R}px;background:${ACCENT}"></div>`);
  const cut = { nw: [0, 0, R, R], n: [R, 0, 4, R], ne: [R + 4, 0, R, R],
                w: [0, R, R, 4], c: [R, R, 4, 4], e: [R + 4, R, R, 4],
                sw: [0, R + 4, R, R], s: [R, R + 4, 4, R], se: [R + 4, R + 4, R, R] };
  for (const [name, [x, y, width, height]] of Object.entries(cut)) {
    await page.screenshot({ path: path.join(THEME, `select_${name}.png`),
                            clip: { x, y, width, height }, omitBackground: true });
  }
  await browser.close();
})();
