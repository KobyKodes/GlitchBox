#!/usr/bin/env python3
"""
Bingeflix M3U8 Scraper - Standalone Playwright script
Navigates bingeflix.tv to intercept m3u8 stream URLs.

Usage:
    python3 bingeflix_scraper.py <tmdb_id> <type> [season] [episode]

Examples:
    python3 bingeflix_scraper.py 550 movie          # Fight Club
    python3 bingeflix_scraper.py 1396 tv 1 1         # Breaking Bad S01E01

Output (stdout): JSON object with {success, hls_url, subtitles, referer, error}
All debug logging goes to stderr.
"""

import sys
import json
import time
import re

def log(msg):
    """Print debug info to stderr (stdout reserved for JSON output)."""
    print(f"[BingeflixScraper] {msg}", file=sys.stderr, flush=True)

def scrape(tmdb_id, content_type='movie', season=None, episode=None):
    from playwright.sync_api import sync_playwright

    hls_url = None
    hls_content = None
    referer = None
    subtitles = []

    def handle_response(response):
        nonlocal hls_url, hls_content, referer
        url = response.url
        # Capture m3u8 master/playlist URLs
        if '.m3u8' in url and hls_url is None:
            log(f"Intercepted m3u8: {url}")
            hls_url = url
            # Capture the response body so we don't need to re-fetch from CDN
            try:
                hls_content = response.text()
                log(f"Captured m3u8 content ({len(hls_content)} bytes)")
            except Exception as e:
                log(f"Could not capture m3u8 body: {e}")
            # Extract referer from request headers
            try:
                req_headers = response.request.headers
                referer = req_headers.get('referer', '')
                log(f"Referer from request: {referer}")
            except Exception:
                pass

    def handle_response_subtitles(response):
        nonlocal subtitles
        url = response.url
        # Capture subtitle files
        if any(ext in url for ext in ['.vtt', '.srt']):
            log(f"Intercepted subtitle: {url}")
            # Try to determine language from URL
            lang = 'unknown'
            lang_match = re.search(r'[/.](\w{2,3})\.(vtt|srt)', url)
            if lang_match:
                lang = lang_match.group(1)
            subtitles.append({
                'url': url,
                'lang': lang,
                'format': 'vtt' if '.vtt' in url else 'srt'
            })

    # Build the target URL
    if content_type == 'movie':
        target_url = f"https://bingeflix.tv/movie/{tmdb_id}"
    elif content_type == 'tv' and season and episode:
        target_url = f"https://bingeflix.tv/tv/{tmdb_id}/{season}/{episode}"
    elif content_type == 'tv':
        target_url = f"https://bingeflix.tv/tv/{tmdb_id}"
    else:
        return {'success': False, 'error': f'Invalid content type: {content_type}'}

    log(f"Target URL: {target_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--disable-background-networking',
                '--single-process',
            ]
        )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
            ignore_https_errors=True,
        )

        page = context.new_page()

        # Register response listeners
        page.on('response', handle_response)
        page.on('response', handle_response_subtitles)

        try:
            log("Navigating to page...")
            page.goto(target_url, wait_until='domcontentloaded', timeout=30000)
            log("Page loaded, waiting for m3u8...")

            # Wait a bit for initial network activity
            page.wait_for_timeout(3000)

            # If no m3u8 yet, try clicking play buttons
            if not hls_url:
                log("No m3u8 yet, looking for play buttons...")
                play_selectors = [
                    'button[aria-label="Play"]',
                    '.play-button',
                    '.btn-play',
                    '[class*="play"]',
                    'button:has(svg)',
                    '.jw-icon-playback',
                    '#player',
                    '.player-wrapper',
                    'video',
                ]
                for selector in play_selectors:
                    try:
                        el = page.query_selector(selector)
                        if el and el.is_visible():
                            log(f"Clicking: {selector}")
                            el.click()
                            page.wait_for_timeout(2000)
                            if hls_url:
                                break
                    except Exception:
                        continue

            # If still no m3u8, try clicking on iframes that might contain the player
            if not hls_url:
                log("Checking iframes for player...")
                frames = page.frames
                for frame in frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        log(f"Checking frame: {frame.url}")
                        for selector in play_selectors:
                            try:
                                el = frame.query_selector(selector)
                                if el and el.is_visible():
                                    log(f"Clicking in frame: {selector}")
                                    el.click()
                                    page.wait_for_timeout(2000)
                                    if hls_url:
                                        break
                            except Exception:
                                continue
                        if hls_url:
                            break
                    except Exception:
                        continue

            # Final wait - poll for m3u8 up to 60s total
            if not hls_url:
                log("Waiting longer for m3u8 to appear...")
                start = time.time()
                while not hls_url and (time.time() - start) < 45:
                    page.wait_for_timeout(2000)
                    log(f"Still waiting... ({int(time.time() - start)}s elapsed)")

        except Exception as e:
            log(f"Navigation error: {e}")
        finally:
            browser.close()

    if hls_url:
        # If no referer was captured, use a sensible default
        if not referer:
            referer = 'https://bingeflix.tv/'

        result = {
            'success': True,
            'hls_url': hls_url,
            'subtitles': subtitles,
            'referer': referer,
        }
        if hls_content:
            result['hls_content'] = hls_content
        return result
    else:
        return {
            'success': False,
            'error': 'No m3u8 URL intercepted within timeout',
        }


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'success': False,
            'error': 'Usage: python3 bingeflix_scraper.py <tmdb_id> <type> [season] [episode]'
        }))
        sys.exit(1)

    tmdb_id = sys.argv[1]
    content_type = sys.argv[2]
    season = sys.argv[3] if len(sys.argv) > 3 else None
    episode = sys.argv[4] if len(sys.argv) > 4 else None

    log(f"Starting scrape: tmdb_id={tmdb_id}, type={content_type}, season={season}, episode={episode}")

    try:
        result = scrape(tmdb_id, content_type, season, episode)
    except Exception as e:
        log(f"Fatal error: {e}")
        result = {'success': False, 'error': str(e)}

    # Output JSON to stdout (only output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
