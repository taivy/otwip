// Makes every marquee scroll at the same speed, regardless of how many frames it has
// Put this before </body> on any page with a .marquee-content:
//    <script src="/marquee.js"></script>

// Pixels per second (the more the faster)
const MARQUEE_SPEED = 40;

// Duration = Distance / Speed
function setMarqueeDuration(row) {
  // Each row holds its frames twice, and @keyframes marquee moves it by -50%,
  // so one animation loop moves half of the row's width, which contains full set of image frames,
  // so for distance/width of single set of frames we divide the row's width by 2
  const distance = row.offsetWidth / 2;
  // "s" is seconds, which is required for the CSS animation-duration property
  row.style.animationDuration = distance / MARQUEE_SPEED + "s";
}

// Runs once for each strip at the start, then again whenever a strip's width
// changes (for example when its images finish loading)
// ResizeObserver is a browser tool that watches elements and runs code when their size changes
const marqueeObserver = new ResizeObserver((entries) => {
  entries.forEach((entry) => setMarqueeDuration(entry.target));
});

// Finds every .marquee-content row on the page and starts watching it
// by applying the observer to them
// "Row" here is the row of images that slides in .marquee box
document.querySelectorAll(".marquee-content").forEach((row) => {
  marqueeObserver.observe(row);
});
