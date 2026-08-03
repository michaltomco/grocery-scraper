(() => {
  const style = document.createElement("style");
  style.id = "omarchy-grocery-palette";
  document.documentElement.appendChild(style);

  function apply(palette) {
    if (!palette?.ok) return;
    const colors = palette.colors;
    const css = `:root {
      --bg: ${colors.background};
      --fg: ${colors.foreground};
      --muted: ${colors.color7};
      --dim: ${colors.color8};
      --border: ${colors.color0};
      --track: ${colors.color8};
      --surface: ${colors.color0};
      --chip-off-bg: ${colors.background};
      --accent: ${colors.accent};
      --price: ${colors.foreground};
      --slider: ${colors.color4};
      --th-bg: ${colors.background};
      --toggle-bg: ${colors.background};
      --thumb-border: ${colors.background};
    }`;
    style.textContent = css;
  }

  async function refresh() {
    apply(await browser.runtime.sendMessage({ type: "get-omarchy-palette" }));
  }

  refresh();
  // Detect a theme-set hook changing the palette while the page is open.
  setInterval(refresh, 30000);
})();
