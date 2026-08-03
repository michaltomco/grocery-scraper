browser.runtime.onMessage.addListener(async (message) => {
  if (message?.type !== "get-omarchy-palette") return undefined;

  try {
    const port = browser.runtime.connectNative("omarchy_grocery_palette");
    const response = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("palette helper timeout")), 3000);
      port.onMessage.addListener((value) => {
        clearTimeout(timer);
        resolve(value);
        port.disconnect();
      });
      port.onDisconnect.addListener(() => {
        clearTimeout(timer);
        if (browser.runtime.lastError) reject(browser.runtime.lastError);
      });
      port.postMessage({ type: "get-palette" });
    });
    return response;
  } catch (error) {
    console.warn("Omarchy grocery palette unavailable", error);
    return { ok: false };
  }
});
