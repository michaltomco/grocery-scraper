# Omarchy colors for Grocery Prices

This extension only matches `http://100.72.212.47:8088/*`. It reads the active
Omarchy palette through native messaging and overrides the page's existing CSS
variables. Zen's own UI and all other websites are unaffected.

## Install for local testing

```bash
chmod +x native/omarchy_grocery_palette.py
mkdir -p ~/.mozilla/native-messaging-hosts
cp native/omarchy_grocery_palette.json ~/.mozilla/native-messaging-hosts/
```

In Zen, open `about:debugging`, choose **This Firefox**, select **Load Temporary
Add-on**, and choose `manifest.json` from this directory. Temporary extensions
need to be loaded again after restarting Zen.
