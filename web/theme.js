/* Restore before paint; storage is optional. */
(() => {
  let theme;
  try { theme = localStorage.getItem("star-index-theme"); } catch { /* Private browsing. */ }
  const dark = theme === "dark" || (!theme && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "night" : "day";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#18181b" : "#ffffff");
})();
