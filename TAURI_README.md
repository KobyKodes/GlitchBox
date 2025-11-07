# GlitchBox macOS App

This is the native macOS application version of GlitchBox, built with Tauri.

## Prerequisites

- Rust (already installed)
- Node.js/npm (already installed)
- Python 3 (for the backend API)

## Running the App

### Development Mode

1. **Start the Python backend** (in one terminal):
   ```bash
   python3 movie_api.py
   ```
   This will start the API server on `http://localhost:8000`

2. **Run the Tauri app** (in another terminal):
   ```bash
   npm run dev
   ```

The app window will open automatically.

### Building for Production

To create a distributable macOS app:

```bash
npm run build
```

This will create a `.dmg` file in `src-tauri/target/release/bundle/dmg/`

## Features

- Native macOS application
- Much smaller size than Electron (~10-20MB vs ~100-200MB)
- Better performance and lower memory usage
- Same functionality as the web version
- Native notifications and system integration

## Configuration

App configuration is in `src-tauri/tauri.conf.json`:
- Window size: 1400x900 (minimum 800x600)
- App name: GlitchBox
- Bundle identifier: com.glitchbox.app

## Troubleshooting

If the app doesn't load:
1. Make sure the Python backend is running on port 8000
2. Check that all dependencies are installed
3. Try running `source "$HOME/.cargo/env"` to load Rust environment
4. Clear the cache: `rm -rf src-tauri/target`
